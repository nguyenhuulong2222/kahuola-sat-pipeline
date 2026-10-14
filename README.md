# kahuola-sat-pipeline

Offline processor for satellite **smoke and dust detections** used as a
context overlay by [Kahu Ola](https://kahuola.org). It reads NOAA's GOES-R
ABI Level-2 **Aerosol Detection Product (ADP)** and publishes small,
pre-rendered frames (PNG + manifest + coverage outline). No raster processing
happens in the web tier.

## Data source

NOAA Open Data Dissemination (NODD), public buckets, no credentials:

| Region | Satellite | Product |
|---|---|---|
| Hawaiʻi | GOES-18 (West) | `ABI-L2-ADPF` (Full Disk) |
| CONUS + southern Canada | GOES-18 west of −106°, GOES-19 (East) east of it, each filling the other's gaps per pixel | `ABI-L2-ADPC` |

Algorithm reference: NOAA/NESDIS/STAR, *ABI Enterprise Processing System
Aerosol Detection Product ATBD*, v1.1 (2024-03-18).

## Detections only — absence is not "no smoke"

ADP flags pixels where smoke or dust **was detected**. It has no positive
"looked, and the air is clear" state: a clear pixel next to a real plume
carries the same flag as one where nothing was tested. So every frame shows
only `SMOKE` and `DUST`; everything else is `NONE`, which means *no qualifying
detection* — never *no smoke*. Each manifest says so in `meaning_of_absence`,
and a coverage outline marks where the satellites could see at all.

A pixel is shown only if it is a medium/high-confidence daytime, cloud-free
detection with solar and view zenith ≤ 60°, outside snow/ice and the
Kīlauea / Mauna Loa volcanic zones, and seen in at least 2 of the last 3 scans
(within 35 minutes, same pixel or a neighbour). Night frames say `night`;
frames without enough history say `warming_up`.

This is a context layer. It never drives alerts, warnings or air-quality
statements.

## Layout

```
sat/        fetch, decode, classify, persist, render, coverage, manifest, storage, pipeline
run.py      python run.py --region hawaii|conus [--upload [--shadow]]
config/     region definitions
tests/      pytest (-m "not network" skips tests that download sample files)
```

Output fields are documented in [SCHEMA.md](SCHEMA.md).

## Running locally

```
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python run.py --region conus --scans 3
```

`--upload` publishes to object storage and reads its credentials from
environment variables only (see `sat/storage.py`).

## License

MIT — see [LICENSE](LICENSE).
