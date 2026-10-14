"""EPSG:3857 canvas and PNG. NONE is fully transparent — no hatch, no tint:
absence of a detection must not be drawn as anything."""
import numpy as np
from PIL import Image
from pyproj import Transformer

from .classify import DUST, SMOKE

COLORS = {SMOKE: (0x8E, 0x24, 0xAA, round(0.55 * 255)), DUST: (0xC8, 0xA1, 0x65, round(0.55 * 255))}


def make_canvas(bbox, width):
    """Pixel-centre lon/lat for an EPSG:3857 grid covering bbox (w, s, e, n)."""
    w, s, e, n = bbox
    to_merc = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    to_ll = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    mx0, my0 = to_merc.transform(w, s)
    mx1, my1 = to_merc.transform(e, n)
    height = int(round(width * (my1 - my0) / (mx1 - mx0)))
    mx = mx0 + (np.arange(width) + 0.5) * (mx1 - mx0) / width
    my = my1 - (np.arange(height) + 0.5) * (my1 - my0) / height
    lon, lat = to_ll.transform(*np.meshgrid(mx, my))
    extent = {"mx0": mx0, "my0": my0, "mx1": mx1, "my1": my1, "width": width, "height": height}
    return np.asarray(lon), np.asarray(lat), extent


def write_png(cls, path):
    rgba = np.zeros(cls.shape + (4,), dtype=np.uint8)
    for c, col in COLORS.items():
        rgba[cls == c] = col
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)
