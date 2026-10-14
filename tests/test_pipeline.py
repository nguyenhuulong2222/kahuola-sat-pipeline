import os
import shutil

import numpy as np
import pytest
from netCDF4 import Dataset

from conftest import scan_at, utc
from datetime import timedelta

from sat import fetch, persist
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
    "product_label": "GOES-18 + GOES-19 ABI-L2-ADPC", "bbox": [-130, 20, -60, 53], "width": 3000, "cadence_min": 10,
    "sources": [
        {"name": "west", "satellite": "GOES-18", "bucket": "noaa-goes18", "product": "ABI-L2-ADPC",
         "lon_min": -130, "lon_max": -106},
        {"name": "east", "satellite": "GOES-19", "bucket": "noaa-goes19", "product": "ABI-L2-ADPC",
         "lon_min": -106, "lon_max": -60},
    ],
}


# 1. Night file -> status night, nothing shown.
@pytest.mark.network
def test_night_file_is_night(tmp_path):
    path = scan_at("noaa-goes18", "ABI-L2-ADPF", utc(2026, 10, 14, 8, 0))
    m, final, _ = process_frame("hawaii", HAWAII, utc(2026, 10, 14, 8, 0), {"main": path},
                             tmp_path / "state", tmp_path / "out")
    assert m["status"] == "night"
    assert m["counts"]["SMOKE"] == 0 and m["counts"]["DUST"] == 0
    assert not final.any()
    assert m["meaning_of_absence"].startswith("No qualifying satellite detection")


# 2. LA fires, 3 consecutive scans: the plume survives persistence.
@pytest.mark.network
def test_la_plume_survives_persistence(tmp_path):
    ms = []
    for minute in (0, 10, 20):
        t = utc(2025, 1, 9, 21, minute)
        path = scan_at("noaa-goes18", "ABI-L2-ADPF", t)
        m, _, _ = process_frame("la", LA, slot_of(t, 10), {"main": path}, tmp_path / "state", tmp_path / "out")
        ms.append(m)
    third = ms[-1]
    print(f"\nLA 21:20Z SMOKE={third['counts']['SMOKE']} raw_smoke={third['counts']['raw_smoke']} "
          f"transient={third['counts']['transient_suppressed']} status={third['status']}")
    assert third["status"] == "complete"
    assert third["counts"]["SMOKE"] > 100
    # First frames lack 2 scans of history within 35 min: nothing confirmed yet.
    assert ms[0]["counts"]["SMOKE"] == 0 and ms[0]["status"] == "warming_up"
    assert ms[1]["status"] == "warming_up"
    assert third["persistence_history_scans"] and len(third["persistence_history_scans"]) == 2
    assert third["scan_mode"] == ["M6"]


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
@pytest.mark.network
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
@pytest.mark.network
def test_conus_missing_east_is_partial(tmp_path):
    # Warm state first (two earlier west-only frames), so the status reflects
    # the missing half rather than warming_up, which outranks partial.
    for minute in (40, 50):
        t0 = utc(2026, 10, 13, 21, minute)
        process_frame("conus", CONUS, slot_of(t0, 10), {"west": scan_at("noaa-goes18", "ABI-L2-ADPC", t0),
                      "east": None}, tmp_path / "state", tmp_path / "out")
    t = utc(2026, 10, 13, 22, 0)
    west = scan_at("noaa-goes18", "ABI-L2-ADPC", t)
    m, _, _ = process_frame("conus", CONUS, slot_of(t, 10), {"west": west, "east": None},
                            tmp_path / "state", tmp_path / "out")
    assert m["status"] == "partial"
    assert m["source_files"][1] is None and m["scan_start_utc"][1] is None
    assert any(x.startswith("source east") for x in m["missing"])
    assert m["coverage"]["west"] > 0.5
    assert any(x.endswith("lacked source east") for x in m["missing"])
    assert os.path.exists(tmp_path / "out" / "conus_20261013T2200Z.png")


# SAT-3b 1. El Paso sits in the west half, outside GOES-18 PACUS; GOES-19 fills it.
@pytest.mark.network
def test_conus_fallback_covers_el_paso(tmp_path):
    t = utc(2026, 10, 13, 22, 0)
    west = scan_at("noaa-goes18", "ABI-L2-ADPC", t)
    east = scan_at("noaa-goes19", "ABI-L2-ADPC", t)
    from sat.render import make_canvas
    lon, lat, _ = make_canvas(CONUS["bbox"], CONUS["width"])
    r, c = np.unravel_index(np.argmin((lon + 106.4) ** 2 + (lat - 31.8) ** 2), lon.shape)
    assert lon[r, c] < -106  # the west half
    m, _, covered = process_frame("conus", CONUS, slot_of(t, 10), {"west": west, "east": east},
                                  tmp_path / "state", tmp_path / "out")
    assert covered[r, c]
    assert m["fallback_pixels"]["west"] > 0
    # Without the east file nothing can fill it.
    _, _, covered_w = process_frame("conus", CONUS, slot_of(t, 10), {"west": west, "east": None},
                                    tmp_path / "state2", tmp_path / "out2")
    assert not covered_w[r, c]
    # The coverage outline is written, referenced, and contains El Paso.
    import json
    from shapely.geometry import Point, shape
    gj = json.load(open(tmp_path / "out" / m["coverage_geojson"]))
    assert shape(gj["features"][0]["geometry"]).contains(Point(-106.4, 31.8))


# SAT-3b 4. History older than 35 min does not count.
def test_history_window():
    now = utc(2026, 10, 13, 22, 0)
    mk = lambda mins: {"cand": None, "present": {}, "scan_time": (now - timedelta(minutes=mins)).isoformat()}
    hist = {"a": mk(10), "b": mk(20), "c": mk(36)}
    assert [t for t, _ in persist.select_history(hist, now)] == [hist["a"]["scan_time"], hist["b"]["scan_time"]]
    assert len(persist.select_history({"c": mk(36), "d": mk(50)}, now)) == 0
    assert len(persist.select_history({"x": mk(-10)}, now)) == 0   # future scans never count


# SAT-3b 5. M6 preferred when a slot holds two scans.
def test_scan_mode_parsed():
    assert fetch.scan_mode("OR_ABI-L2-ADPF-M4_G18_s20262851905205_e2_c3.nc") == "M4"
    assert fetch.scan_mode("OR_ABI-L2-ADPF-M6_G18_s20262862200204_e2_c3.nc") == "M6"


# SAT-4c. The same scan processed twice counts once toward 2-of-3.
def test_duplicate_scan_counts_once():
    t = utc(2026, 10, 14, 18, 10)
    scans = {"west": "2026-10-14T18:11:17.1Z", "east": "2026-10-14T18:10:21.2Z"}
    cand = np.zeros((20, 20), dtype=np.uint8)
    cand[10, 10] = SMOKE
    entry = lambda sc: {"cand": cand.copy(), "scan_time": t.isoformat(), "present": {}, "scans": dict(sc)}
    # Dispatch and schedule both processed this scan, under different keys.
    hist = {"dispatch": entry(scans), "schedule": entry(scans)}
    # Reprocessing it again: neither copy may confirm it, so nothing is shown.
    used = persist.select_history(hist, t + timedelta(seconds=1), current_scans=scans)
    assert used == []
    assert not persist.apply(cand, [e["cand"] for _, e in used]).any()
    # A later, distinct scan sees the two copies as ONE earlier scan.
    later = {"west": "2026-10-14T18:21:17.1Z", "east": "2026-10-14T18:20:21.2Z"}
    assert len(persist.select_history(hist, t + timedelta(minutes=10), current_scans=later)) == 1
    # Sharing one source's scan is enough to be the same scan.
    part = {"a": entry({"west": scans["west"], "east": None}), "b": entry(scans)}
    assert len(persist.select_history(part, t + timedelta(minutes=10), current_scans=later)) == 1
    # Recording drops the earlier copy, so state holds the scan once.
    one = {}
    for key in ("dispatch", "schedule"):
        persist.record(one, key, entry(scans))
    assert list(one) == ["schedule"]


# SAT-4c. End to end: the LA plume scan processed twice is not confirmed by itself.
@pytest.mark.network
def test_la_same_scan_twice_does_not_persist(tmp_path):
    t = utc(2025, 1, 9, 21, 10)
    path = scan_at("noaa-goes18", "ABI-L2-ADPF", t)
    ms = [process_frame("la", LA, slot, {"main": path}, tmp_path / "state", tmp_path / "out")[0]
          for slot in (slot_of(t, 10), slot_of(t, 10) + timedelta(minutes=10))]
    assert ms[1]["counts"]["raw_smoke"] > 100           # the plume is there ...
    assert ms[1]["counts"]["SMOKE"] == 0                # ... but one scan cannot confirm itself
    assert ms[1]["status"] == "warming_up"
    assert ms[1]["persistence_history_scans"] == []
