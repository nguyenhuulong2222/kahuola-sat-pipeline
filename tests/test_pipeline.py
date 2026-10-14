import os
import shutil

import numpy as np
import pytest
from netCDF4 import Dataset

from conftest import scan_at, utc
from sat import persist
from sat.classify import DUST, NONE, SMOKE, classify
from sat.decode import FlagLayoutError
from sat.pipeline import process_frame
from sat.fetch import slot_of

HAWAII = {
    "product_label": "GOES-18 ABI-L2-ADPF", "bbox": [-161, 18, -154, 23], "width": 384, "cadence_min": 10,
    "sources": [{"name": "main", "satellite": "GOES-18", "bucket": "noaa-goes18", "product": "ABI-L2-ADPF",
                 "lon_min": -180, "lon_max": 180}],
}
LA = {**HAWAII, "bbox": [-121, 32, -117, 35], "width": 192}
CONUS = {
    "product_label": "GOES-18 + GOES-19 ABI-L2-ADPC", "bbox": [-130, 20, -60, 55], "width": 3000, "cadence_min": 10,
    "sources": [
        {"name": "west", "satellite": "GOES-18", "bucket": "noaa-goes18", "product": "ABI-L2-ADPC",
         "lon_min": -130, "lon_max": -106},
        {"name": "east", "satellite": "GOES-19", "bucket": "noaa-goes19", "product": "ABI-L2-ADPC",
         "lon_min": -106, "lon_max": -60},
    ],
}


# 1. Night file -> status night, nothing shown.
def test_night_file_is_night(tmp_path):
    path = scan_at("noaa-goes18", "ABI-L2-ADPF", utc(2026, 10, 14, 8, 0))
    m, final = process_frame("hawaii", HAWAII, utc(2026, 10, 14, 8, 0), {"main": path},
                             tmp_path / "state", tmp_path / "out")
    assert m["status"] == "night"
    assert m["counts"]["SMOKE"] == 0 and m["counts"]["DUST"] == 0
    assert not final.any()
    assert m["meaning_of_absence"].startswith("No qualifying satellite detection")


# 2. LA fires, 3 consecutive scans: the plume survives persistence.
def test_la_plume_survives_persistence(tmp_path):
    ms = []
    for minute in (0, 10, 20):
        t = utc(2025, 1, 9, 21, minute)
        path = scan_at("noaa-goes18", "ABI-L2-ADPF", t)
        m, _ = process_frame("la", LA, slot_of(t, 10), {"main": path}, tmp_path / "state", tmp_path / "out")
        ms.append(m)
    third = ms[-1]
    print(f"\nLA 21:20Z SMOKE={third['counts']['SMOKE']} raw_smoke={third['counts']['raw_smoke']} "
          f"transient={third['counts']['transient_suppressed']} status={third['status']}")
    assert third["status"] == "complete"
    assert third["counts"]["SMOKE"] > 100
    # First frame has no history, so nothing can be shown yet — and it says why.
    assert ms[0]["counts"]["SMOKE"] == 0 and ms[0]["status"] == "partial"


# 3. A single-scan blip is suppressed; the same pixel in 2 of 3 scans (shifted
#    by one pixel) is kept.
def test_single_scan_blip_suppressed():
    cand = np.zeros((20, 20), np.uint8)
    cand[10, 10] = SMOKE
    empty = np.zeros_like(cand)
    assert not persist.apply(cand, [empty, empty]).any()
    shifted = np.zeros_like(cand)
    shifted[11, 11] = SMOKE
    assert persist.apply(cand, [empty, shifted])[10, 10]
    other_class = np.zeros_like(cand)
    other_class[10, 10] = DUST
    assert not persist.apply(cand, [empty, other_class]).any()


def _fields(n, **over):
    f = {
        "DQF": np.full(n, 3 | 12 | 0 | 192, np.int32),      # ash bad, smoke bad, dust high, nuc bad
        "Smoke": np.zeros(n, np.int32), "Dust": np.ones(n, np.int32),
        "Cloud": np.zeros(n, np.int32), "SnowIce": np.zeros(n, np.int32),
        "PQI1": np.zeros(n, np.int32), "PQI2": np.full(n, 4, np.int32),   # land, day
        "in_grid": np.ones(n, bool),
    }
    f.update(over)
    return f


# 4. DUST inside VOLCANIC_ZONES (Mauna Loa summit) is suppressed; outside it is kept.
def test_volcanic_dust_suppressed():
    lon = np.array([-155.60, -157.00])
    lat = np.array([19.47, 21.00])
    cand, counts, _ = classify(_fields(2), lon, lat)
    assert cand[0] == NONE and cand[1] == DUST
    assert counts["volcanic_suppressed"] == 1


def test_snowice_and_geometry_suppressed():
    lon = np.array([-157.0, -157.0])
    lat = np.array([21.0, 21.0])
    f = _fields(2, SnowIce=np.array([1, 0], np.int32), PQI1=np.array([0, 12], np.int32))
    cand, counts, _ = classify(f, lon, lat)
    assert (cand == NONE).all()
    assert counts["snowice_suppressed"] == 1 and counts["below_threshold"] == 1


# 5. A changed flag layout aborts the run.
def test_flag_layout_change_aborts(tmp_path):
    src = scan_at("noaa-goes18", "ABI-L2-ADPF", utc(2026, 10, 14, 8, 0))
    bad = tmp_path / "mutated.nc"
    shutil.copy(src, bad)
    with Dataset(bad, "a") as ds:
        m = ds["DQF"].getncattr("flag_meanings")
        ds["DQF"].setncattr("flag_meanings", m.replace("low_confidence_smoke", "TMP").replace(
            "medium_confidence_smoke", "low_confidence_smoke").replace("TMP", "medium_confidence_smoke"))
    with pytest.raises(FlagLayoutError):
        process_frame("hawaii", HAWAII, utc(2026, 10, 14, 8, 0), {"main": str(bad)},
                      tmp_path / "state", tmp_path / "out")
    assert not os.path.exists(tmp_path / "out")


# 6. CONUS with the east half missing -> partial; the west half is still rendered.
def test_conus_missing_east_is_partial(tmp_path):
    t = utc(2026, 10, 13, 22, 0)
    west = scan_at("noaa-goes18", "ABI-L2-ADPC", t)
    m, _ = process_frame("conus", CONUS, slot_of(t, 10), {"west": west, "east": None},
                         tmp_path / "state", tmp_path / "out")
    assert m["status"] == "partial"
    assert m["source_files"][1] is None and m["scan_start_utc"][1] is None
    assert any(x.startswith("source east") for x in m["missing"])
    assert m["coverage"]["east"] is None and m["coverage"]["west"] > 0.5
    assert os.path.exists(tmp_path / "out" / "conus_20261013T2200Z.png")
