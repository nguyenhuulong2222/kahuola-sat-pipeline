"""SAT-2a Step 2: dump an ABI-L2-ADP file's structure, then histogram every
integer pixel variable inside the Hawaiʻi bbox. No variable names are assumed
beyond the CF/ABI grid variables x, y and goes_imager_projection."""
import sys

import numpy as np
from netCDF4 import Dataset

from geo import hawaii_window

GLOBALS = ["time_coverage_start", "time_coverage_end", "date_created", "scene_id", "production_site"]


def dump(path):
    ds = Dataset(path)
    ds.set_auto_mask(False)
    ds.set_auto_scale(False)
    print(f"\n######## {path.split('/')[-1]}")
    print("== global attributes")
    for k in GLOBALS:
        print(f"  {k}: {ds.getncattr(k) if k in ds.ncattrs() else '<absent>'}")
    print("== variables")
    for name, v in ds.variables.items():
        line = f"  {name}: dtype={v.dtype} shape={v.shape} dims={v.dimensions}"
        if v.ndim == 0 or (v.ndim == 1 and v.shape[0] <= 2):
            line += f" value={np.asarray(v[...]).tolist()}"
        print(line)
    print("== attributes per variable")
    for name, v in ds.variables.items():
        attrs = {k: v.getncattr(k) for k in v.ncattrs()}
        if not attrs:
            continue
        print(f"  -- {name}")
        for k, val in attrs.items():
            print(f"     {k}: {val!r}")
    ds.close()


def histograms(path):
    ds = Dataset(path)
    ny, nx = ds.variables["y"].shape[0], ds.variables["x"].shape[0]
    rs, cs, lon, lat, inside = hawaii_window(ds)
    ds.set_auto_mask(False)
    ds.set_auto_scale(False)
    print(f"\n== Hawaiʻi bbox histograms ({inside.sum()} pixels) — {path.split('/')[-1]}")
    for name, v in ds.variables.items():
        if v.dimensions != ("y", "x") or v.shape != (ny, nx):
            continue
        if not np.issubdtype(v.dtype, np.integer):
            continue
        vals = np.asarray(v[rs, cs])[inside]
        u, c = np.unique(vals, return_counts=True)
        print(f"  {name}: " + ", ".join(f"{int(a)}:{int(b)}" for a, b in zip(u, c)))
    # Joint distribution of the per-pixel flags, to see which combinations occur.
    cols = [n for n in ("DQF", "Smoke", "Dust", "Cloud", "PQI2") if n in ds.variables]
    if cols:
        stack = np.stack([np.asarray(ds.variables[n][rs, cs])[inside].astype("i8") for n in cols], axis=1)
        u, c = np.unique(stack, axis=0, return_counts=True)
        print(f"  joint ({', '.join(cols)}):")
        for row, n in sorted(zip(u.tolist(), c.tolist()), key=lambda t: -t[1]):
            print(f"     {row}: {n}")
    ds.close()


if __name__ == "__main__":
    for p in sys.argv[1:]:
        dump(p)
    for p in sys.argv[1:]:
        histograms(p)
