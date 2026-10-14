# Frame manifest — schema 1.0

One JSON file per region per scan slot: `out/<region>_<YYYYMMDDTHHMMZ>.json`,
next to `<region>_<stamp>.png` and `<region>_coverage_<stamp>.geojson`.

**Reading rule for every consumer:** a transparent pixel in the PNG is never a
statement that the air is clear. Inside the coverage outline it means "no
qualifying detection"; outside it means "not observed". ADP has no
"retrieved, no smoke" state (ATBD v1.1 p.48; SAT-2b LA control).

## Fields

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | `"1.0"`. |
| `region` | string | Config key: `hawaii`, `conus`. |
| `product` | string | Human label from config, e.g. `GOES-18 ABI-L2-ADPF`. |
| `algorithm_version` | string \| null | `product_version` read from the file(s) (e.g. `v02r00`). Comma-joined if sources differ; null if no source was read. |
| `source_names` | string[] | Config source names in config order (`["main"]`, `["west","east"]`). Every per-source array below is aligned to this order. |
| `source_files` | (string \| null)[] | NetCDF file name per source; **null when that source had no scan** for the slot. Never filled with another scan. |
| `scan_mode` | (string \| null)[] | ABI scan mode from the file name per source (`"M6"`, `"M4"`, ...); null when the source is missing. When a slot holds more than one scan, M6 is chosen. |
| `scan_start_utc` | (string \| null)[] | `time_coverage_start` per source, verbatim from the file. |
| `file_created_utc` | (string \| null)[] | `date_created` per source, verbatim from the file. |
| `generated_at_utc` | string | When this manifest was written. |
| `bbox` | number[4] | `[west, south, east, north]` in degrees. |
| `crs` | string | `"EPSG:3857"` — the PNG's grid. |
| `status` | string | One of, in precedence order: `unavailable` (no source read), `night` (`daylight_fraction` < 0.05), `warming_up` (fewer than 2 earlier scans within 35 min of this one — persistence could not confirm anything from them), `partial` (anything else in `missing`), `complete`. |
| `missing` | string[] | Every reason the frame is not complete: a source with no scan for the slot, persistence history short of 2 scans within 35 min, or a history scan that lacked a source. Empty only when complete. |
| `rules` | object | `confidence: "medium_high"`, `persistence: "2_of_3_with_neighbours"`, `sza_vza_max: 60`, `exclusions: ["volcanic","snowice"]`. |
| `persistence_history_scans` | string[] | Scan times (ISO UTC) of the earlier scans that persistence used, newest first; at most 2. |
| `counts` | object | See below. |
| `count_unit` | string | `"canvas_pixel"`: every count is in output-canvas pixels, not native ABI pixels. The canvas is sized near native resolution; the ratio varies with latitude and viewing angle. |
| `canvas` | object | `width`, `height` (PNG pixels) and `pixel_m_epsg3857` (canvas pixel width in EPSG:3857 metres; ground size is this × cos(latitude)). |
| `coverage` | object | Per source band (`west`/`east`/`main`): fraction of that band's canvas pixels with valid geometry from **any** source after fallback. |
| `fallback_pixels` | object | Per source band: canvas pixels filled from a satellite other than the band's preferred one (SAT-3b per-pixel fallback). |
| `coverage_geojson` | string \| null | File name of the coverage outline for this frame (same directory); null when no source was read. |
| `daylight_fraction` | number | Share of valid canvas pixels flagged day (PQI2 bit 3 = 0). |
| `cloud_fraction` | number \| null | Share of daylight pixels with `Cloud==1`; null when there is no daylight. |
| `meaning_of_absence` | string | Fixed: `"No qualifying satellite detection. Does NOT indicate absence of smoke."` |

## `counts`

| Key | Meaning |
|---|---|
| `SMOKE` | Pixels shown as smoke: Smoke==1, smoke-DQF medium/high, day, cloud-free, SZA and VZA ≤ 60°, not snow/ice, outside VOLCANIC_ZONES, and confirmed by persistence. |
| `DUST` | Same for dust (dust-DQF medium/high). Smoke wins a pixel flagged as both. |
| `raw_smoke` | Pixels with Smoke==1 before any rule. |
| `raw_dust` | Pixels with Dust==1 before any rule. |
| `below_threshold` | Raw smoke/dust pixels failing confidence, geometry (SZA/VZA > 60°), day or cloud rules. Never shown. |
| `volcanic_suppressed` | Qualifying smoke/dust inside VOLCANIC_ZONES (copied from kahuola-web `worker/src/index.ts` VOLCANIC_ZONES). |
| `snowice_suppressed` | Qualifying smoke/dust where SnowIce==1. |
| `transient_suppressed` | Qualifying smoke/dust not confirmed by persistence (same class at the pixel or an 8-neighbour in ≥ 2 of the last 3 scans within 35 min). |

## Coverage GeoJSON

`FeatureCollection` with one `Feature`: geometry = (Multi)Polygon in lon/lat
of canvas pixels with valid geometry from any source (majority vote over
blocks of ≤ 1/400 of the canvas width, simplified by one block — approximate at
the edges); properties `region`, `slot`, `source_files`, `method`.

## Shadow log

`out/shadow_<region>.jsonl`, one line per frame: `slot`, `region`, `status`,
`missing`, `scan_mode`, `scan_start_utc`, `source_files`, `counts`,
`daylight_fraction`, `cloud_fraction`, `coverage`, `fallback_pixels`,
`generated_at_utc`. No images.
