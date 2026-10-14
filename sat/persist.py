"""Rule 4: a SMOKE/DUST pixel is shown only if the same class is present at
that pixel or any 8-neighbour in >= 2 of the last 3 scans (current included).

History counts only if its scan time is within WINDOW_MIN of the current scan
(SAT-3b). With fewer than 2 such scans the frame is "warming_up": anything an
older or missing scan might have confirmed is NOT assumed.

A scan counts ONCE (SAT-4c): history entries are identified by their
per-source scan_start_utc, so the same scan processed twice (manual dispatch
plus schedule, two triggers) can never supply both confirmations.

State per region = the last KEEP pre-persistence candidate arrays in
state/<region>.npz, each with its scan time, per-source scan starts and which
sources were present."""
import json
import os
from datetime import datetime, timedelta

import numpy as np

from .classify import DUST, SMOKE

KEEP = 3
NEED = 2
PREVIOUS_NEEDED = 2
WINDOW_MIN = 35


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


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def scan_ids(scan_time, scans):
    """Identity of a processed scan: each source's scan_start_utc, plus the
    frame scan time (the only identity entries saved before SAT-4c carry)."""
    ids = {f"{n}:{t}" for n, t in (scans or {}).items() if t}
    if scan_time:
        t = parse_time(scan_time) if isinstance(scan_time, str) else scan_time
        ids.add(f"*:{t.isoformat()}")
    return ids


def entry_ids(entry):
    return scan_ids(entry["scan_time"], entry.get("scans"))


def record(hist, key, entry):
    """Store entry under key, first dropping any entry that shares a scan with it."""
    ids = entry_ids(entry)
    for k in [k for k, e in hist.items() if entry_ids(e) & ids]:
        del hist[k]
    hist[key] = entry


def select_history(hist, scan_time, window_min=WINDOW_MIN, current_scans=None):
    """The (up to) 2 most recent distinct scans strictly before scan_time and
    no more than window_min older, never sharing a scan with the current one
    or with each other. Returns [(scan_time_iso, entry), ...] newest first."""
    lo = scan_time - timedelta(minutes=window_min)
    usable = [e for e in hist.values() if lo <= parse_time(e["scan_time"]) < scan_time]
    usable.sort(key=lambda e: parse_time(e["scan_time"]), reverse=True)
    seen = scan_ids(scan_time, current_scans)
    out = []
    for e in usable:
        ids = entry_ids(e)
        if ids & seen:
            continue
        seen |= ids
        out.append((e["scan_time"], e))
        if len(out) == PREVIOUS_NEEDED:
            break
    return out


def load(path, shape):
    """{slot_iso: {"cand", "scan_time", "present"}}; entries of another shape are dropped."""
    if not os.path.exists(path):
        return {}
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
        hist = {}
        for i, m in enumerate(meta):
            a = z[f"cand_{i}"]
            if a.shape == tuple(shape) and "scan_time" in m:
                hist[m["slot"]] = {"cand": a, "scan_time": m["scan_time"], "present": m["present"],
                                  "scans": m.get("scans", {})}
    return hist


def save(path, hist):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    keys = sorted(hist, key=lambda k: parse_time(hist[k]["scan_time"]))[-KEEP:]
    arrays = {f"cand_{i}": hist[k]["cand"] for i, k in enumerate(keys)}
    meta = [{"slot": k, "scan_time": hist[k]["scan_time"], "present": hist[k]["present"],
             "scans": hist[k].get("scans", {})} for k in keys]
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, meta=json.dumps(meta), **arrays)
    os.replace(tmp, path)
