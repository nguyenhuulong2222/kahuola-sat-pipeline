"""SAT-2a: classify one GOES-18 ABI-L2-ADPF file over Hawaiʻi into exactly one
state per pixel, and render a Web Mercator PNG + JSON manifest.

Every bit position below is taken from the file's own flag_masks / flag_values /
flag_meanings and is re-verified at runtime by check_flag_layout(); a file whose
layout differs aborts instead of being guessed at.
"""
import json
import os
import resource
import sys
import time
from datetime import datetime, timezone

import numpy as np
from netCDF4 import Dataset
from PIL import Image
from pyproj import Transformer

from geo import HAWAII_BBOX, geos_proj, hawaii_window

STATES = ("SMOKE", "DUST", "NOT_DETECTED", "NO_RETRIEVAL")
SMOKE, DUST, NOT_DETECTED, NO_RETRIEVAL = range(4)

# Copied verbatim from kahuola-web worker/src/index.ts:2373-2376 (VOLCANIC_ZONES).
VOLCANIC_ZONES = [
    {"id": "kilauea-summit-erz", "west": -155.35, "south": 19.25, "east": -154.80, "north": 19.50},
    {"id": "mauna-loa",          "west": -155.75, "south": 19.30, "east": -155.40, "north": 19.60},
]

# The flag layout this classifier was written against, as read from the file.
EXPECTED_DQF = {
    "medium_confidence_smoke_detection_qf": (12, 4),
    "high_confidence_smoke_detection_qf": (12, 0),
    "low_confidence_smoke_detection_qf": (12, 8),
    "bad_missing_smoke_detection_qf": (12, 12),
    "medium_confidence_dust_detection_qf": (48, 16),
    "high_confidence_dust_detection_qf": (48, 0),
    "low_confidence_dust_detection_qf": (48, 32),
    "bad_missing_dust_detection_qf": (48, 48),
}
EXPECTED_PQI1 = {
    "valid_longitude": (1, 0), "valid_latitude": (2, 0),
    "valid_solar_zenith_angle": (12, 0), "valid_satellite_view_zenith_angle": (48, 0),
}
EXPECTED_PQI2 = {"day": (8, 0), "night": (8, 8)}
EXPECTED_BINARY = {"Smoke": "smoke_absent smoke_present", "Dust": "dust_absent dust_present",
                   "Cloud": "cloud_absent cloud_present"}


def flag_table(var):
    meanings = var.getncattr("flag_meanings").split()
    masks = np.atleast_1d(var.getncattr("flag_masks")).tolist()
    values = np.atleast_1d(var.getncattr("flag_values")).tolist()
    return {m: (int(k), int(v)) for m, k, v in zip(meanings, masks, values)}


def check_flag_layout(ds):
    for var, expected in (("DQF", EXPECTED_DQF), ("PQI1", EXPECTED_PQI1), ("PQI2", EXPECTED_PQI2)):
        table = flag_table(ds.variables[var])
        for meaning, mv in expected.items():
            if table.get(meaning) != mv:
                raise SystemExit(f"STOP: {var} '{meaning}' is {table.get(meaning)}, expected {mv}")
    for var, meanings in EXPECTED_BINARY.items():
        v = ds.variables[var]
        if v.getncattr("flag_meanings") != meanings or list(v.getncattr("flag_values")) != [0, 1]:
            raise SystemExit(f"STOP: {var} flag layout changed")


def classify(dqf, smoke, dust, cloud, pqi1, pqi2, dqf_fill, i8_fill):
    """One state per pixel. NO_RETRIEVAL is decided FIRST, from flags that
    describe whether a retrieval happened at all, because the file shows DQF
    alone is not enough: cloudy pixels carry high-confidence smoke DQF with
    Smoke=0, and so do some night pixels."""
    dqf = dqf.astype(np.int32)
    pqi1 = pqi1.astype(np.int32)
    pqi2 = pqi2.astype(np.int32)
    sf = dqf & 12
    df = dqf & 48

    no_ret = (
        (dqf == dqf_fill) | (dqf < 0) | (dqf > 255)
        | (smoke == i8_fill) | (dust == i8_fill) | (cloud == i8_fill)
        | (pqi1 == 65535) | (pqi2 == 65535)
        | (cloud == 1)                       # cloud_present
        | ((pqi2 & 8) == 8)                  # night
        | ((pqi1 & 1) != 0) | ((pqi1 & 2) != 0)   # invalid lon / lat
        | ((pqi1 & 12) != 0)                 # invalid / out-of-range solar zenith
        | ((pqi1 & 48) != 0)                 # invalid / out-of-range view zenith
    )
    smoke_med_high = (sf == 0) | (sf == 4)
    dust_med_high = (df == 0) | (df == 16)
    smoke_valid = sf != 12

    state = np.full(dqf.shape, NO_RETRIEVAL, dtype=np.uint8)
    # SAT-2b ruling: NOT_DETECTED is a statement about SMOKE only. The dust
    # field may be bad; dust is shown only as a positive detection.
    nd = ~no_ret & smoke_valid
    state[nd] = NOT_DETECTED                 # includes low-confidence detections (ruling)
    state[~no_ret & (dust == 1) & dust_med_high] = DUST
    state[~no_ret & (smoke == 1) & smoke_med_high] = SMOKE   # smoke wins over dust
    return state


def in_volcanic(lon, lat):
    m = np.zeros(lon.shape, dtype=bool)
    for z in VOLCANIC_ZONES:
        m |= (lon >= z["west"]) & (lon <= z["east"]) & (lat >= z["south"]) & (lat <= z["north"])
    return m


def pixel_size_km(ds, lon0=-156.3, lat0=20.8):
    """Distance between neighbouring fixed-grid pixel centres near Maui."""
    from pyproj import Geod
    proj, a = geos_proj(ds)
    h = float(a["perspective_point_height"])
    step = float(ds.variables["x"].getncattr("scale_factor")) * h
    x, y = proj(lon0, lat0)
    g = Geod(a=float(a["semi_major_axis"]), b=float(a["semi_minor_axis"]))
    lo_e, la_e = proj(x + step, y, inverse=True)
    lo_n, la_n = proj(x, y + step, inverse=True)
    return g.inv(lon0, lat0, lo_e, la_e)[2] / 1000, g.inv(lon0, lat0, lo_n, la_n)[2] / 1000


def render(ds, rs, cs, state_win, path, width=1024):
    """Nearest-neighbour resample of the native-grid states into EPSG:3857."""
    w, s, e, n = HAWAII_BBOX
    to_merc = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    to_ll = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    mx0, my0 = to_merc.transform(w, s)
    mx1, my1 = to_merc.transform(e, n)
    height = int(round(width * (my1 - my0) / (mx1 - mx0)))
    mx = mx0 + (np.arange(width) + 0.5) * (mx1 - mx0) / width
    my = my1 - (np.arange(height) + 0.5) * (my1 - my0) / height
    MX, MY = np.meshgrid(mx, my)
    lon, lat = to_ll.transform(MX, MY)

    proj, a = geos_proj(ds)
    h = float(a["perspective_point_height"])
    gx, gy = proj(lon, lat)
    xv, yv = ds.variables["x"], ds.variables["y"]
    col = np.rint((gx / h - float(xv.add_offset)) / float(xv.scale_factor)).astype(np.int64) - cs.start
    row = np.rint((gy / h - float(yv.add_offset)) / float(yv.scale_factor)).astype(np.int64) - rs.start
    ok = np.isfinite(gx) & (np.abs(gx) < 1e29) & (row >= 0) & (row < state_win.shape[0]) \
        & (col >= 0) & (col < state_win.shape[1])
    st = np.full(lon.shape, NO_RETRIEVAL, dtype=np.uint8)
    st[ok] = state_win[row[ok], col[ok]]

    rgba = np.zeros(lon.shape + (4,), dtype=np.uint8)
    rgba[st == SMOKE] = (0x8E, 0x24, 0xAA, round(0.55 * 255))
    rgba[st == DUST] = (0xC8, 0xA1, 0x65, round(0.55 * 255))
    ii, jj = np.indices(lon.shape)
    hatch = ((ii + jj) % 10) < 2
    rgba[(st == NO_RETRIEVAL) & hatch] = (0x9E, 0x9E, 0x9E, round(0.45 * 255))
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)
    return width, height


def process(path, out_dir="out"):
    t0 = time.perf_counter()
    ds = Dataset(path)
    check_flag_layout(ds)
    rs, cs, lon, lat, inside = hawaii_window(ds)
    ds.set_auto_mask(False)
    ds.set_auto_scale(False)
    v = ds.variables
    state_win = classify(
        np.asarray(v["DQF"][rs, cs]), np.asarray(v["Smoke"][rs, cs]), np.asarray(v["Dust"][rs, cs]),
        np.asarray(v["Cloud"][rs, cs]), np.asarray(v["PQI1"][rs, cs]), np.asarray(v["PQI2"][rs, cs]),
        int(v["DQF"].getncattr("_FillValue")), int(v["Smoke"].getncattr("_FillValue")),
    )
    states = state_win[inside]
    counts = {name: int((states == i).sum()) for i, name in enumerate(STATES)}
    total = int(states.size)
    volc = in_volcanic(lon, lat) & inside
    volcanic_smoke = int(((state_win == SMOKE) & volc).sum())

    g = ds.__dict__
    start = g["time_coverage_start"]
    stamp = start.replace("-", "").replace(":", "").split(".")[0]
    png = os.path.join(out_dir, f"hawaii_adp_{stamp}.png")
    pw, ph = render(ds, rs, cs, state_win, png)
    ew, ns = pixel_size_km(ds)

    manifest = {
        "product": "GOES-18 ABI-L2-ADPF",
        "source_file": os.path.basename(path),
        "scan_start_utc": start,
        "scan_end_utc": g["time_coverage_end"],
        "file_created_utc": g["date_created"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "bbox": list(HAWAII_BBOX),
        "crs": "EPSG:3857",
        "confidence_rule": "medium_high_only",
        "counts": counts,
        "retrieval_fraction": round((total - counts["NO_RETRIEVAL"]) / total, 6) if total else 0.0,
        "volcanic_zone_smoke_pixels": volcanic_smoke,
    }
    with open(os.path.join(out_dir, f"hawaii_adp_{stamp}.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    ds.close()

    elapsed = time.perf_counter() - t0
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6  # bytes on macOS
    print(json.dumps(manifest, indent=2))
    print(f"pixels_in_bbox={total} volcanic_zone_pixels={int(volc.sum())} "
          f"pixel_km_over_maui=EW {ew:.2f} x NS {ns:.2f} png={pw}x{ph} "
          f"runtime_s={elapsed:.2f} peak_rss_mb={peak_mb:.0f}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        process(p)
