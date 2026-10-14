"""Positive control for classify(): run it over the whole disk and compare with
the file's own granule counters. Over Hawaiʻi the sample files classify as 100%
NO_RETRIEVAL, which a classifier that always says NO_RETRIEVAL would also do."""
import sys

import numpy as np
from netCDF4 import Dataset

from process_adp import STATES, check_flag_layout, classify

for path in sys.argv[1:]:
    ds = Dataset(path)
    check_flag_layout(ds)
    ds.set_auto_mask(False)
    ds.set_auto_scale(False)
    v = ds.variables
    st = classify(v["DQF"][:], v["Smoke"][:], v["Dust"][:], v["Cloud"][:], v["PQI1"][:], v["PQI2"][:],
                  int(v["DQF"].getncattr("_FillValue")), int(v["Smoke"].getncattr("_FillValue")))
    earth = np.asarray(v["DQF"][:]) != int(v["DQF"].getncattr("_FillValue"))
    dqf = np.asarray(v["DQF"][:]).astype(np.int32)
    smoke = np.asarray(v["Smoke"][:])
    print(f"== {path.split('/')[-1]}")
    print("  classify() over earth pixels:", {n: int(((st == i) & earth).sum()) for i, n in enumerate(STATES)})
    for name in ("number_of_good_smoke_retrievals", "number_of_good_retrievals_where_smoke_detected",
                 "number_of_good_dust_retrievals", "number_of_good_retrievals_where_dust_detected"):
        print(f"  file {name} = {int(v[name][...])}")
    sf = dqf & 12
    print("  for comparison — earth pixels with smoke DQF != bad:", int((earth & (sf != 12)).sum()),
          "| Smoke==1 any conf:", int((earth & (smoke == 1)).sum()),
          "| Smoke==1 & DQF med/high:", int((earth & (smoke == 1) & ((sf == 0) | (sf == 4))).sum()))
    ds.close()
