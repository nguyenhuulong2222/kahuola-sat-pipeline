"""Per-frame manifest (schema 1.0, documented field by field in SCHEMA.md) and
the shadow log line."""
import json
from datetime import datetime, timezone

MEANING_OF_ABSENCE = "No qualifying satellite detection. Does NOT indicate absence of smoke."
RULES = {"confidence": "medium_high", "persistence": "2_of_3_with_neighbours",
         "sza_vza_max": 60, "exclusions": ["volcanic", "snowice"]}
NIGHT_DAYLIGHT_FRACTION = 0.05


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def status_of(any_source, daylight_fraction, warming_up, missing):
    """Precedence: unavailable > night > warming_up > partial > complete."""
    if not any_source:
        return "unavailable"
    if daylight_fraction < NIGHT_DAYLIGHT_FRACTION:
        return "night"
    if warming_up:
        return "warming_up"
    return "partial" if missing else "complete"


def build(region, cfg, metas, counts, aux, missing, coverage, canvas_info,
          coverage_geojson, fallback_pixels, history, warming_up):
    """metas: {source_name: ScanMeta | None}, in config order. A missing source
    contributes null entries, never a guessed value."""
    order = [s["name"] for s in cfg["sources"]]
    present = [metas[n] for n in order if metas.get(n)]
    versions = sorted({m.algorithm_version for m in present})
    per = lambda attr: [getattr(metas[n], attr) if metas.get(n) else None for n in order]  # noqa: E731
    return {
        "schema_version": "1.0",
        "region": region,
        "product": cfg["product_label"],
        "algorithm_version": ",".join(versions) if versions else None,
        "source_names": order,
        "source_files": [metas[n].path.rsplit("/", 1)[-1] if metas.get(n) else None for n in order],
        "scan_mode": per("scan_mode"),
        "scan_start_utc": per("scan_start"),
        "file_created_utc": per("created"),
        "generated_at_utc": now_utc(),
        "bbox": cfg["bbox"],
        "crs": "EPSG:3857",
        "status": status_of(bool(present), aux.get("daylight_fraction", 0.0), warming_up, missing),
        "missing": missing,
        "rules": RULES,
        "persistence_history_scans": history,
        "counts": counts,
        "count_unit": "canvas_pixel",
        "canvas": canvas_info,
        "coverage": coverage,
        "fallback_pixels": fallback_pixels,
        "coverage_geojson": coverage_geojson,
        "daylight_fraction": round(aux.get("daylight_fraction", 0.0), 6),
        "cloud_fraction": None if aux.get("cloud_fraction") is None else round(aux["cloud_fraction"], 6),
        "meaning_of_absence": MEANING_OF_ABSENCE,
    }


def shadow_line(m, slot_iso):
    keys = ("region", "status", "missing", "scan_mode", "scan_start_utc", "source_files", "counts",
            "daylight_fraction", "cloud_fraction", "coverage", "fallback_pixels", "generated_at_utc")
    return json.dumps({"slot": slot_iso, **{k: m[k] for k in keys}}, separators=(",", ":"))
