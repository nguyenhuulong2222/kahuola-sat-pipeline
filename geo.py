"""Fixed-grid -> lon/lat for GOES ABI L2 files, using only the file's own attributes."""
import numpy as np
from pyproj import Proj

HAWAII_BBOX = (-161.0, 18.0, -154.0, 23.0)  # west, south, east, north


def projection_attrs(ds):
    p = ds.variables["goes_imager_projection"]
    return {k: p.getncattr(k) for k in p.ncattrs()}


def geos_proj(ds):
    a = projection_attrs(ds)
    return Proj(
        proj="geos",
        h=float(a["perspective_point_height"]),
        a=float(a["semi_major_axis"]),
        b=float(a["semi_minor_axis"]),
        lon_0=float(a["longitude_of_projection_origin"]),
        sweep=str(a["sweep_angle_axis"]),
    ), a


def hawaii_window(ds, bbox=HAWAII_BBOX):
    """Return (row_slice, col_slice, lon2d, lat2d, inside_mask) for the bbox.

    x/y are read through netCDF4's automatic scale_factor/add_offset (radians),
    then multiplied by perspective_point_height as +proj=geos expects.
    """
    proj, a = geos_proj(ds)
    h = float(a["perspective_point_height"])
    x = np.asarray(ds.variables["x"][:], dtype="f8") * h
    y = np.asarray(ds.variables["y"][:], dtype="f8") * h

    # Coarse pass on a strided grid to find the window, then exact pass inside it.
    step = 20
    xx, yy = np.meshgrid(x[::step], y[::step])
    lon, lat = proj(xx, yy, inverse=True)
    lon = np.where(np.abs(lon) > 1e29, np.nan, lon)
    lat = np.where(np.abs(lat) > 1e29, np.nan, lat)
    w, s, e, n = bbox
    with np.errstate(invalid="ignore"):
        hit = (lon >= w - 1) & (lon <= e + 1) & (lat >= s - 1) & (lat <= n + 1)
    rows, cols = np.nonzero(hit)
    if rows.size == 0:
        raise RuntimeError("bbox not visible from this satellite")
    r0, r1 = max(rows.min() * step - step, 0), min((rows.max() + 1) * step + step, y.size)
    c0, c1 = max(cols.min() * step - step, 0), min((cols.max() + 1) * step + step, x.size)

    xx, yy = np.meshgrid(x[c0:c1], y[r0:r1])
    lon, lat = proj(xx, yy, inverse=True)
    lon = np.where(np.abs(lon) > 1e29, np.nan, lon)
    lat = np.where(np.abs(lat) > 1e29, np.nan, lat)
    with np.errstate(invalid="ignore"):
        inside = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
    return slice(r0, r1), slice(c0, c1), lon, lat, inside
