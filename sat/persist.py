"""Rule 4: a SMOKE/DUST pixel is shown only if the same class is present at
that pixel or any 8-neighbour in >= 2 of the last 3 scans (current included).

State per region = the last 3 pre-persistence candidate arrays, keyed by scan
slot, in state/<region>.npz. A previous slot that was never processed is
MISSING, never assumed empty-and-fine: the caller marks the frame partial."""
import json
import os
from datetime import datetime, timedelta

import numpy as np

from .classify import DUST, SMOKE

KEEP = 3
NEED = 2


def dilate(mask):
    p = np.pad(mask, 1)
    out = np.zeros_like(mask)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            out |= p[dy:dy + mask.shape[0], dx:dx + mask.shape[1]]
    return out


def apply(cand, previous):
    """previous: list of earlier candidate arrays (0-2 of them)."""
    kept = np.zeros(cand.shape, dtype=bool)
    for c in (SMOKE, DUST):
        here = cand == c
        if not here.any():
            continue
        support = dilate(here).astype(np.int8)
        for p in previous:
            support += dilate(p == c)
        kept |= here & (support >= NEED)
    return kept


def load(path, shape):
    """{slot_iso: {"cand": array, "present": {...}}}; entries of another shape are dropped."""
    if not os.path.exists(path):
        return {}
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
        hist = {}
        for i, m in enumerate(meta):
            a = z[f"cand_{i}"]
            if a.shape == tuple(shape):
                hist[m["slot"]] = {"cand": a, "present": m["present"]}
    return hist


def save(path, hist):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    slots = sorted(hist)[-KEEP:]
    arrays = {f"cand_{i}": hist[s]["cand"] for i, s in enumerate(slots)}
    meta = [{"slot": s, "present": hist[s]["present"]} for s in slots]
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, meta=json.dumps(meta), **arrays)
    os.replace(tmp, path)


def previous_slots(slot, cadence_min):
    return [(slot - timedelta(minutes=cadence_min * k)).isoformat() for k in (1, 2)]


def slot_iso(slot: datetime):
    return slot.isoformat()
