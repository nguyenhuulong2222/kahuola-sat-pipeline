# SAT pipeline notes

## Logged, not chased (SAT-2b ruling 3)

- `number_of_good_retrievals_where_smoke_detected = 5,753` (GOES-18 ADPF
  2026-10-13 22:00Z) matches no per-pixel definition tried: `Smoke==1` = 119,183;
  `Smoke==1` & smoke-DQF medium/high = 4,640. The granule counters are not used.
- `number_of_good_smoke_retrievals = 11,871,924` is reproduced to 0.017% by
  "clear & day & SnowIce==0 & PQI1==0" (11,873,894) — a geometry/cloud count that
  ignores the smoke DQF. Not adopted (SAT-2b ruling 1).

## DQF encoding (resolved)

ATBD v1.1 Table 4 (p.13) lists codes `01` Low, `10` Medium, `00` High, `11`
Bad/missing. The tables write bits least-significant first: Table 5 gives
"Invalid SZA (SZA > 90 or < 0)" as `01` on bits 2-3, and every night pixel in
the 2026-10-14 08:00Z file has PQI1 = 8. So on the smoke field Low = 8,
Medium = 4, High = 0, Bad = 12 — exactly the file's own `flag_values` /
`flag_meanings`. (ATBD §6.3 p.81 separately says "0/1/2 representing
lower/medium/high", which contradicts both; the file + Table 4 win.)

`0` is also the DEFAULT confidence value (p.51), so DQF=0 on a non-smoke pixel
means "not assigned", not "high-confidence clear". Cloudy pixels are
"not processed" (p.47) and carry 0.

## What smoke-DQF "bad" means on a clear pixel

ATBD: bad = missing observations / FPM anomaly (p.12-13), or the input-quality
test failed (p.48). It does not state what DQF a clear pixel gets when no smoke
test fires. Empirically (LA fires 2025-01-09 21:00Z, lon -121..-117,
lat 32..35): every clear pixel outside the plume is `bad` — 12,597/12,597 over
water and 5,455/5,455 over land, including pixels 1-3 px from the plume edge —
while all 1,406 plume pixels over water carry high/medium/low. In practice
`bad` on a clear pixel = "no detection fired", not "could not see".

Consequence: ADP has no positive "retrieved, no smoke" state. Pixels that land
in NOT_DETECTED under the current rule are smoke-DQF `low` with Smoke=0 (PQI1
snow/ice source = internal test, Hawaiʻi Island high terrain), or Smoke=1 at low
confidence — neither is evidence of clear air.

## Hawaiʻi observations (7 daytime files, 2026-10-07..13)

- retrieval_fraction 0.0000-0.0007; NOT_DETECTED over water = 0 in every file.
- Every DUST pixel is inside the Mauna Loa VOLCANIC_ZONES bbox. Volcanic tagging
  currently counts SMOKE only.
- SMOKE pixels: small clusters (5-6 px) with no independent confirmation.

## SAT-3 (detections-only processor)

- The SAT-2a/2b spike scripts (`process_adp.py`, `verify_fulldisk.py`,
  `sat2b_evidence.py`) carried the retired NOT_DETECTED state and were removed;
  they remain in history at e202d41 / 3161f01. `inspect_adp.py` + `geo.py`
  (structure dump, no state model) are kept.
- Counts are in canvas pixels (`count_unit`), not native pixels; the canvas is
  sized near native resolution so the 8-neighbour persistence radius ≈ 1 native px.
- CONUS split at −106°: GOES-18 PACUS does not reach southern New Mexico /
  Chihuahua (uncovered −107.85..−106 at 32°N; El Paso), which GOES-19 CONUS does
  cover. Both CONUS sectors end near 51–53°N, short of the 55°N bbox edge. Gaps
  render transparent, like "no detection"; only `coverage` reports them.
