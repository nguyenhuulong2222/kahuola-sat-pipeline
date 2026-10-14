"""SAT-2b evidence.

  la  FILE      — smoke-DQF distribution over WATER around a known plume
                  (LA fires, 2025-01-09), inside vs outside the plume.
  hi  FILE...   — Hawaiʻi daytime state counts under the current classify().
"""
import sys

import numpy as np
from netCDF4 import Dataset

from geo import HAWAII_BBOX, hawaii_window
from process_adp import STATES, check_flag_layout, classify

LA_BBOX = (-121.0, 32.0, -117.0, 35.0)
SMOKE_DQF = {0: "high", 4: "medium", 8: "low", 12: "bad"}  # mask 12, from the file


def window(path, bbox):
    ds = Dataset(path)
    check_flag_layout(ds)
    rs, cs, lon, lat, inside = hawaii_window(ds, bbox)
    ds.set_auto_mask(False)
    ds.set_auto_scale(False)
    v = {n: np.asarray(ds.variables[n][rs, cs]) for n in ("DQF", "Smoke", "Dust", "Cloud", "PQI1", "PQI2")}
    fills = int(ds.variables["DQF"].getncattr("_FillValue")), int(ds.variables["Smoke"].getncattr("_FillValue"))
    start = ds.getncattr("time_coverage_start")
    ds.close()
    return v, inside, fills, start


def dqf_counts(dqf, m):
    sf = dqf.astype(np.int32)[m] & 12
    return {name: int((sf == code).sum()) for code, name in SMOKE_DQF.items()}


def la(path):
    v, inside, _, start = window(path, LA_BBOX)
    p2 = v["PQI2"].astype(np.int32)
    earth = inside & (v["DQF"] != 65535)
    water, day = (p2 & 4) == 0, (p2 & 8) == 0
    groups = {
        "water, inside plume (Smoke==1)": earth & water & (v["Smoke"] == 1),
        "water, clear outside plume (Cloud==0, Smoke==0, day)": earth & water & day & (v["Cloud"] == 0) & (v["Smoke"] == 0),
        "land, inside plume (Smoke==1) [context]": earth & ~water & (v["Smoke"] == 1),
        "land, clear outside plume [context]": earth & ~water & day & (v["Cloud"] == 0) & (v["Smoke"] == 0),
    }
    print(f"== LA {start}  window={LA_BBOX}  pixels={int(inside.sum())}")
    print(f"   {'group':56s} {'n':>7} {'high':>7} {'medium':>7} {'low':>7} {'bad':>7}")
    for k, m in groups.items():
        c = dqf_counts(v["DQF"], m)
        print(f"   {k:56s} {int(m.sum()):>7} {c['high']:>7} {c['medium']:>7} {c['low']:>7} {c['bad']:>7}")
    # Clear water broken down by distance to the nearest plume pixel (in pixels),
    # to see whether "valid" clear water exists only next to the plume.
    plume = earth & (v["Smoke"] == 1)
    clear_w = groups["water, clear outside plume (Cloud==0, Smoke==0, day)"]
    if plume.any():
        from_plume = distance_to(plume)
        for lo, hi in ((1, 3), (4, 10), (11, 30), (31, 10**9)):
            m = clear_w & (from_plume >= lo) & (from_plume <= hi)
            c = dqf_counts(v["DQF"], m)
            print(f"     clear water {lo:>2}-{hi if hi < 10**9 else '∞':>3} px from plume: n={int(m.sum()):>6} {c}")


def distance_to(mask):
    """Chebyshev distance (pixels) to the nearest True cell; small windows only."""
    d = np.full(mask.shape, 10**9, dtype=np.int64)
    d[mask] = 0
    for _ in range(max(mask.shape)):
        nb = d.copy()
        nb[1:, :] = np.minimum(nb[1:, :], d[:-1, :] + 1)
        nb[:-1, :] = np.minimum(nb[:-1, :], d[1:, :] + 1)
        nb[:, 1:] = np.minimum(nb[:, 1:], d[:, :-1] + 1)
        nb[:, :-1] = np.minimum(nb[:, :-1], d[:, 1:] + 1)
        nb[1:, 1:] = np.minimum(nb[1:, 1:], d[:-1, :-1] + 1)
        nb[:-1, :-1] = np.minimum(nb[:-1, :-1], d[1:, 1:] + 1)
        nb[1:, :-1] = np.minimum(nb[1:, :-1], d[:-1, 1:] + 1)
        nb[:-1, 1:] = np.minimum(nb[:-1, 1:], d[1:, :-1] + 1)
        if np.array_equal(nb, d):
            break
        d = nb
    return d


def hi(paths):
    print(f"== Hawaiʻi daytime, bbox={HAWAII_BBOX}")
    print(f"   {'scan_start':24s} {'SMOKE':>6} {'DUST':>6} {'NOT_DET':>8} {'NO_RET':>7} {'retr':>7} {'cloud':>6} {'ND land':>8} {'ND water':>9}")
    for path in paths:
        v, inside, (dfill, ifill), start = window(path, HAWAII_BBOX)
        st = classify(v["DQF"], v["Smoke"], v["Dust"], v["Cloud"], v["PQI1"], v["PQI2"], dfill, ifill)[inside]
        n = st.size
        c = {s: int((st == i).sum()) for i, s in enumerate(STATES)}
        land = (v["PQI2"].astype(np.int32)[inside] & 4) != 0
        nd = st == STATES.index("NOT_DETECTED")
        cloud = float((v["Cloud"][inside] == 1).mean())
        print(f"   {start:24s} {c['SMOKE']:>6} {c['DUST']:>6} {c['NOT_DETECTED']:>8} {c['NO_RETRIEVAL']:>7} "
              f"{(n - c['NO_RETRIEVAL']) / n:>7.4f} {cloud:>6.3f} {int((nd & land).sum()):>8} {int((nd & ~land).sum()):>9}")


if __name__ == "__main__":
    mode, *files = sys.argv[1:]
    if mode == "la":
        for f in files:
            la(f)
    else:
        hi(files)
