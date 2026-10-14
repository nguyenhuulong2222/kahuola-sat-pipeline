"""One frame: sample sources onto the region canvas (preferred satellite per
half, the other one where the preferred cannot see), classify, apply
persistence against state, render, write the coverage outline, the manifest
and a shadow-log line."""
import json
import os

import numpy as np

from . import coverage, persist
from .classify import DUST, SMOKE, classify
from .decode import EXPECTED_FILLS, FIELDS, U16_FILL, open_scan, sample
from .manifest import build, shadow_line
from .render import make_canvas, write_png


def _band(lon, src):
    upper = (lon < src["lon_max"]) if src["lon_max"] < 180 else (lon <= 180)
    return (lon >= src["lon_min"]) & upper


def process_frame(region, cfg, slot, paths, state_dir="state", out_dir="out"):
    """paths: {source_name: local file path or None}. Returns (manifest, final
    class array, covered mask). Raises FlagLayoutError (aborting the run) if any
    file's flag encoding differs from the verified one."""
    lon, lat, extent = make_canvas(cfg["bbox"], cfg["width"])
    fields = {n: np.full(lon.shape, EXPECTED_FILLS[n], dtype=np.int32) for n in FIELDS}
    fields["in_grid"] = np.zeros(lon.shape, dtype=bool)
    metas, missing, bands, opened = {}, [], {}, {}
    fallback_pixels = {}

    try:
        for src in cfg["sources"]:
            name = src["name"]
            path = paths.get(name)
            if path:
                opened[name], metas[name] = open_scan(path)
            else:
                metas[name] = None
                missing.append(f"source {name} ({src['satellite']} {src['product']}): no scan for slot {slot.isoformat()}")

        # SAT-3b rule 1: per-pixel fallback. Each half tries its own satellite
        # first, then the others, for pixels the previous one could not see.
        for src in cfg["sources"]:
            name = src["name"]
            band = _band(lon, src)
            bands[name] = band
            lon_b, lat_b = lon[band], lat[band]
            out = {n: fields[n][band] for n in FIELDS + ("in_grid",)}
            remaining = np.ones(lon_b.shape, dtype=bool)
            fallback_pixels[name] = 0
            order = [name] + [s["name"] for s in cfg["sources"] if s["name"] != name]
            for o in order:
                if o not in opened or not remaining.any():
                    continue
                sel = np.nonzero(remaining)[0]
                s = sample(opened[o], lon_b[sel], lat_b[sel])
                good = s["in_grid"] & (s["DQF"] != U16_FILL)
                idx = sel[good]
                for n in FIELDS:
                    out[n][idx] = s[n][good]
                out["in_grid"][idx] = True
                remaining[idx] = False
                if o != name:
                    fallback_pixels[name] += int(good.sum())
            for n in FIELDS + ("in_grid",):
                fields[n][band] = out[n]
    finally:
        for ds in opened.values():
            ds.close()

    cand, counts, aux = classify(fields, lon, lat)
    covered = fields["in_grid"] & (fields["DQF"] != U16_FILL)
    present = {n: metas[n] is not None for n in metas}
    cov = {n: round(float(covered[bands[n]].mean()), 6) for n in bands}

    # Persistence: history must be within WINDOW_MIN of this scan's time.
    scan_times = [persist.parse_time(m.scan_start) for m in metas.values() if m]
    scan_time = min(scan_times) if scan_times else None
    state_path = os.path.join(state_dir, f"{region}.npz")
    hist = persist.load(state_path, cand.shape)
    used = persist.select_history(hist, scan_time) if scan_time else []
    if scan_time and len(used) < persist.PREVIOUS_NEEDED:
        missing.append(f"persistence history: {len(used)} of {persist.PREVIOUS_NEEDED} previous scans "
                       f"within {persist.WINDOW_MIN} min of {scan_time.isoformat()}")
    for t, entry in used:
        for n, ok in entry["present"].items():
            if not ok:
                missing.append(f"persistence history: scan {t} lacked source {n}")
    kept = persist.apply(cand, [e["cand"] for _, e in used])
    final = np.where(kept, cand, 0).astype(np.uint8)
    counts = {
        "SMOKE": int((final == SMOKE).sum()),
        "DUST": int((final == DUST).sum()),
        **counts,
        "transient_suppressed": int(((cand > 0) & ~kept).sum()),
    }
    if scan_time:
        hist[slot.isoformat()] = {"cand": cand, "scan_time": scan_time.isoformat(), "present": present}
        persist.save(state_path, hist)

    os.makedirs(out_dir, exist_ok=True)
    stamp = slot.strftime("%Y%m%dT%H%MZ")
    cov_name = None
    if scan_time:
        write_png(final, os.path.join(out_dir, f"{region}_{stamp}.png"))
        cov_name = f"{region}_coverage_{stamp}.geojson"
        coverage.write(os.path.join(out_dir, cov_name), covered, extent,
                       {"region": region, "slot": slot.isoformat(),
                        "source_files": [os.path.basename(m.path) for m in metas.values() if m]})

    canvas_info = {"width": extent["width"], "height": extent["height"],
                   "pixel_m_epsg3857": round((extent["mx1"] - extent["mx0"]) / extent["width"], 1)}
    m = build(region, cfg, metas, counts, aux, missing, cov, canvas_info,
              coverage_geojson=cov_name, fallback_pixels=fallback_pixels,
              history=[t for t, _ in used], warming_up=bool(scan_time) and len(used) < persist.PREVIOUS_NEEDED)
    with open(os.path.join(out_dir, f"{region}_{stamp}.json"), "w") as f:
        json.dump(m, f, indent=2)
    with open(os.path.join(out_dir, f"shadow_{region}.jsonl"), "a") as f:
        f.write(shadow_line(m, slot.isoformat()) + "\n")
    return m, final, covered
