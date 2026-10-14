"""Open an ABI-L2-ADP file, verify its flag layout, and sample its per-pixel
fields onto an arbitrary set of lon/lat points (nearest native pixel).

Every bit position used downstream was read from the files' own flag_masks /
flag_values / flag_meanings in SAT-2a and confirmed against ATBD v1.1 in SAT-2b.
check_flag_layout() re-verifies them on every file; a mismatch aborts the run."""
import os
from dataclasses import dataclass

import numpy as np
from netCDF4 import Dataset
from pyproj import Proj

from .fetch import scan_mode

FIELDS = ("DQF", "Smoke", "Dust", "Cloud", "SnowIce", "PQI1", "PQI2")
U16_FILL = 65535
I8_FILL = -128

EXPECTED_MASKED = {
    "DQF": {
        "low_confidence_smoke_detection_qf": (12, 8),
        "medium_confidence_smoke_detection_qf": (12, 4),
        "high_confidence_smoke_detection_qf": (12, 0),
        "bad_missing_smoke_detection_qf": (12, 12),
        "low_confidence_dust_detection_qf": (48, 32),
        "medium_confidence_dust_detection_qf": (48, 16),
        "high_confidence_dust_detection_qf": (48, 0),
        "bad_missing_dust_detection_qf": (48, 48),
    },
    "PQI1": {
        "valid_longitude": (1, 0), "invalid_longitude": (1, 1),
        "valid_latitude": (2, 0), "invalid_latitude": (2, 2),
        "valid_solar_zenith_angle": (12, 0), "invalid_solar_zenith_angle": (12, 8),
        "out_of_range_solar_zenith_angle": (12, 12),
        "valid_satellite_view_zenith_angle": (48, 0), "invalid_satellite_view_zenith_angle": (48, 32),
        "out_of_range_satellite_view_zenith_angle": (48, 48),
    },
    "PQI2": {"water": (4, 0), "land": (4, 4), "day": (8, 0), "night": (8, 8)},
}
EXPECTED_BINARY = {
    "Smoke": "smoke_absent smoke_present",
    "Dust": "dust_absent dust_present",
    "Cloud": "cloud_absent cloud_present",
    "SnowIce": "snow_ice_absent snow_ice_present",
}
EXPECTED_FILLS = {"DQF": U16_FILL, "PQI1": U16_FILL, "PQI2": U16_FILL,
                  "Smoke": I8_FILL, "Dust": I8_FILL, "Cloud": I8_FILL, "SnowIce": I8_FILL}


class FlagLayoutError(RuntimeError):
    """The file's flag encoding differs from the one this code was verified against."""


def _flag_table(var):
    meanings = var.getncattr("flag_meanings").split()
    masks = np.atleast_1d(var.getncattr("flag_masks")).tolist()
    values = np.atleast_1d(var.getncattr("flag_values")).tolist()
    if not (len(meanings) == len(masks) == len(values)):
        raise FlagLayoutError(f"{var.name}: flag_meanings/masks/values lengths differ")
    return {m: (int(k), int(v)) for m, k, v in zip(meanings, masks, values)}


def check_flag_layout(ds):
    try:
        for name, expected in EXPECTED_MASKED.items():
            table = _flag_table(ds.variables[name])
            for meaning, mv in expected.items():
                if table.get(meaning) != mv:
                    raise FlagLayoutError(f"{name} '{meaning}' is {table.get(meaning)}, expected {mv}")
        for name, meanings in EXPECTED_BINARY.items():
            v = ds.variables[name]
            if v.getncattr("flag_meanings") != meanings or list(v.getncattr("flag_values")) != [0, 1]:
                raise FlagLayoutError(f"{name} flag layout changed")
        for name, fill in EXPECTED_FILLS.items():
            if int(ds.variables[name].getncattr("_FillValue")) != fill:
                raise FlagLayoutError(f"{name} _FillValue changed")
    except (KeyError, AttributeError) as e:
        raise FlagLayoutError(f"missing variable or attribute: {e}") from e


@dataclass
class ScanMeta:
    path: str
    scan_start: str
    scan_end: str
    created: str
    algorithm_version: str
    scan_mode: str | None


def open_scan(path):
    ds = Dataset(path)
    check_flag_layout(ds)
    ds.set_auto_maskandscale(False)
    meta = ScanMeta(
        path=path,
        scan_start=ds.getncattr("time_coverage_start"),
        scan_end=ds.getncattr("time_coverage_end"),
        created=ds.getncattr("date_created"),
        algorithm_version=ds.variables["algorithm_product_version_container"].getncattr("product_version"),
        scan_mode=scan_mode(os.path.basename(path)),
    )
    return ds, meta


def sample(ds, lon, lat):
    """Nearest-native-pixel values of FIELDS at each (lon, lat). Points the
    satellite cannot see, or outside this file's sector, get the fill value and
    in_grid=False — they are never given a guessed value."""
    p = ds.variables["goes_imager_projection"]
    h = float(p.getncattr("perspective_point_height"))
    proj = Proj(proj="geos", h=h, a=float(p.getncattr("semi_major_axis")),
                b=float(p.getncattr("semi_minor_axis")),
                lon_0=float(p.getncattr("longitude_of_projection_origin")),
                sweep=str(p.getncattr("sweep_angle_axis")))
    gx, gy = proj(lon, lat)
    gx, gy = np.asarray(gx, dtype="f8"), np.asarray(gy, dtype="f8")
    xv, yv = ds.variables["x"], ds.variables["y"]
    ok = np.isfinite(gx) & np.isfinite(gy) & (np.abs(gx) < 1e29) & (np.abs(gy) < 1e29)
    col = np.zeros(lon.shape, dtype=np.int64)
    row = np.zeros(lon.shape, dtype=np.int64)
    col[ok] = np.rint((gx[ok] / h - float(xv.getncattr("add_offset"))) / float(xv.getncattr("scale_factor")))
    row[ok] = np.rint((gy[ok] / h - float(yv.getncattr("add_offset"))) / float(yv.getncattr("scale_factor")))
    in_grid = ok & (col >= 0) & (col < xv.shape[0]) & (row >= 0) & (row < yv.shape[0])

    out = {n: np.full(lon.shape, EXPECTED_FILLS[n], dtype=np.int32) for n in FIELDS}
    if in_grid.any():
        r0, r1 = row[in_grid].min(), row[in_grid].max() + 1
        c0, c1 = col[in_grid].min(), col[in_grid].max() + 1
        for n in FIELDS:
            block = np.asarray(ds.variables[n][r0:r1, c0:c1]).astype(np.int32)
            out[n][in_grid] = block[row[in_grid] - r0, col[in_grid] - c0]
    out["in_grid"] = in_grid
    return out
