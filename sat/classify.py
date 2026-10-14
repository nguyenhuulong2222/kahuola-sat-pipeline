"""Rules 1-3 (SAT-3): SMOKE / DUST / NONE plus suppression counters.

NONE means "no qualifying detection", NEVER "no smoke": ADP assigns no
positive "retrieved, clear" state (ATBD v1.1 p.48; SAT-2b LA control)."""
import numpy as np

from .decode import I8_FILL, U16_FILL

NONE, SMOKE, DUST = 0, 1, 2

# Copied verbatim from kahuola-web worker/src/index.ts:2373-2376 (VOLCANIC_ZONES).
VOLCANIC_ZONES = [
    {"id": "kilauea-summit-erz", "west": -155.35, "south": 19.25, "east": -154.80, "north": 19.50},
    {"id": "mauna-loa",          "west": -155.75, "south": 19.30, "east": -155.40, "north": 19.60},
]


def in_volcanic(lon, lat):
    m = np.zeros(np.shape(lon), dtype=bool)
    for z in VOLCANIC_ZONES:
        m |= (lon >= z["west"]) & (lon <= z["east"]) & (lat >= z["south"]) & (lat <= z["north"])
    return m


def classify(f, lon, lat):
    """f: sampled fields (decode.sample) on the canvas. Returns
    (candidates uint8 NONE/SMOKE/DUST before persistence, counts, aux)."""
    dqf, pqi1, pqi2 = f["DQF"], f["PQI1"], f["PQI2"]
    valid = (
        f["in_grid"]
        & (dqf != U16_FILL) & (pqi1 != U16_FILL) & (pqi2 != U16_FILL)
        & (f["Smoke"] != I8_FILL) & (f["Dust"] != I8_FILL) & (f["Cloud"] != I8_FILL)
        & ((pqi1 & 1) == 0) & ((pqi1 & 2) == 0)          # valid lon / lat
    )
    day = valid & ((pqi2 & 8) == 0)
    # Rule 2: ATBD p.49 — SZA>60 or VZA>60 is low confidence by definition.
    geometry_ok = ((pqi1 & 12) == 0) & ((pqi1 & 48) == 0)
    clear = f["Cloud"] != 1
    sf, df = dqf & 12, dqf & 48

    raw_smoke = valid & (f["Smoke"] == 1)
    raw_dust = valid & (f["Dust"] == 1)
    smoke_q = raw_smoke & day & clear & geometry_ok & ((sf == 0) | (sf == 4))   # high | medium
    dust_q = raw_dust & day & clear & geometry_ok & ((df == 0) | (df == 16)) & ~smoke_q
    qualifying = smoke_q | dust_q
    below = (raw_smoke | raw_dust) & ~qualifying

    # Rule 3: exclusions apply to SMOKE and DUST alike. Snow/ice first.
    snow = qualifying & (f["SnowIce"] == 1)
    volcanic = qualifying & ~snow & in_volcanic(lon, lat)
    keep = qualifying & ~snow & ~volcanic

    cand = np.full(dqf.shape, NONE, dtype=np.uint8)
    cand[keep & smoke_q] = SMOKE
    cand[keep & dust_q] = DUST

    counts = {
        "raw_smoke": int(raw_smoke.sum()),
        "raw_dust": int(raw_dust.sum()),
        "below_threshold": int(below.sum()),
        "volcanic_suppressed": int(volcanic.sum()),
        "snowice_suppressed": int(snow.sum()),
    }
    n_valid = int(valid.sum())
    n_day = int(day.sum())
    aux = {
        "valid": valid,
        "daylight_fraction": n_day / n_valid if n_valid else 0.0,
        "cloud_fraction": float((day & (f["Cloud"] == 1)).sum()) / n_day if n_day else None,
    }
    return cand, counts, aux
