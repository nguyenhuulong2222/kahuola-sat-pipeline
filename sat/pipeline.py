"""One frame: sample every source onto the region canvas, classify, apply
persistence against state, render, write the manifest and a shadow-log line."""
import json
import os

import numpy as np

from . import persist
from .classify import DUST, SMOKE, classify
from .decode import EXPECTED_FILLS, FIELDS, open_scan, sample
from .manifest import build, shadow_line
from .render import make_canvas, write_png


def process_frame(region, cfg, slot, paths, state_dir="state", out_dir="out"):
    """paths: {source_name: local file path or None}. Raises FlagLayoutError
    (aborting the run) if any file's flag encoding differs from the verified one."""
    lon, lat, px_m = make_canvas(cfg["bbox"], cfg["width"])
    fields = {n: np.full(lon.shape, EXPECTED_FILLS[n], dtype=np.int32) for n in FIELDS}
    fields["in_grid"] = np.zeros(lon.shape, dtype=bool)
    metas, missing, bands = {}, [], {}

    for src in cfg["sources"]:
        name = src["name"]
        band = (lon >= src["lon_min"]) & (lon < src["lon_max"] if src["lon_max"] < 180 else lon <= 180)
        bands[name] = band
        path = paths.get(name)
        if not path:
            metas[name] = None
            missing.append(f"source {name} ({src['satellite']} {src['product']}): no scan for slot {slot.isoformat()}")
            continue
        ds, meta = open_scan(path)
        try:
            s = sample(ds, lon[band], lat[band])
        finally:
            ds.close()
        metas[name] = meta
        for n in FIELDS + ("in_grid",):
            fields[n][band] = s[n]

    cand, counts, aux = classify(fields, lon, lat)
    present = {n: metas[n] is not None for n in metas}
    coverage = {n: (round(float(aux["valid"][bands[n]].mean()), 6) if present[n] else None) for n in bands}

    # Persistence against the two previous slots.
    state_path = os.path.join(state_dir, f"{region}.npz")
    hist = persist.load(state_path, cand.shape)
    previous = []
    for ps in persist.previous_slots(slot, cfg["cadence_min"]):
        entry = hist.get(ps)
        if entry is None:
            missing.append(f"persistence history: slot {ps} not processed")
            continue
        previous.append(entry["cand"])
        for n, ok in entry["present"].items():
            if not ok:
                missing.append(f"persistence history: slot {ps} lacked source {n}")
    kept = persist.apply(cand, previous)
    final = np.where(kept, cand, 0).astype(np.uint8)
    counts = {
        "SMOKE": int((final == SMOKE).sum()),
        "DUST": int((final == DUST).sum()),
        **counts,
        "transient_suppressed": int(((cand > 0) & ~kept).sum()),
    }
    if any(present.values()):
        hist[slot.isoformat()] = {"cand": cand, "present": present}
        persist.save(state_path, hist)

    canvas_info = {"width": int(lon.shape[1]), "height": int(lon.shape[0]), "pixel_m_epsg3857": round(px_m, 1)}
    m = build(region, cfg, metas, counts, aux, missing, coverage, canvas_info)

    os.makedirs(out_dir, exist_ok=True)
    stamp = slot.strftime("%Y%m%dT%H%MZ")
    if any(present.values()):
        write_png(final, os.path.join(out_dir, f"{region}_{stamp}.png"))
    with open(os.path.join(out_dir, f"{region}_{stamp}.json"), "w") as f:
        json.dump(m, f, indent=2)
    with open(os.path.join(out_dir, f"shadow_{region}.jsonl"), "a") as f:
        f.write(shadow_line(m, slot.isoformat()) + "\n")
    return m, final
