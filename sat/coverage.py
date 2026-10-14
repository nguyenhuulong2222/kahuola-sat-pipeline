"""Coverage outline: where ANY source had valid geometry this frame.

Outside this outline the frame says nothing at all — a transparent pixel there
is "not observed", which the PNG alone cannot distinguish from "no detection"."""
import json

import numpy as np
from pyproj import Transformer
import shapely
from shapely.geometry import box, mapping
from shapely.ops import unary_union

MAX_COARSE_WIDTH = 400


def outline(mask, extent):
    """Simplified (Multi)Polygon in lon/lat of the covered canvas pixels.

    The mask is reduced to blocks (majority vote) so the outline stays small,
    traced as row runs, unioned, and simplified by one block."""
    h, w = mask.shape
    block = max(1, -(-w // MAX_COARSE_WIDTH))
    hp, wp = -(-h // block) * block, -(-w // block) * block
    padded = np.zeros((hp, wp), dtype=bool)
    padded[:h, :w] = mask
    coarse = padded.reshape(hp // block, block, wp // block, block).mean(axis=(1, 3)) >= 0.5

    rects = []
    for r, row in enumerate(coarse):
        edges = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        for c0, c1 in zip(np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]):
            rects.append(box(c0 * block, r * block, min(c1 * block, w), min((r + 1) * block, h)))
    if not rects:
        return None, block
    geom = unary_union(rects).simplify(block, preserve_topology=True)

    sx = (extent["mx1"] - extent["mx0"]) / extent["width"]
    sy = (extent["my1"] - extent["my0"]) / extent["height"]
    to_ll = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

    def px_to_ll(xy):
        lon, lat = to_ll.transform(extent["mx0"] + xy[:, 0] * sx, extent["my1"] - xy[:, 1] * sy)
        return np.round(np.column_stack([lon, lat]), 4)

    return shapely.transform(geom, px_to_ll), block


def write(path, mask, extent, properties):
    geom, block = outline(mask, extent)
    fc = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": mapping(geom) if geom is not None else None,
            "properties": {**properties,
                           "method": f"majority of {block}x{block} canvas-pixel blocks, simplified by one block"},
        }],
    }
    with open(path, "w") as f:
        json.dump(fc, f, separators=(",", ":"))
    return geom
