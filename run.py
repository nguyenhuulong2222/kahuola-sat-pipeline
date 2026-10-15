"""python run.py --region hawaii|conus [--end ISO] [--scans N] [--upload [--shadow]]

Processes the latest N scan slots ending at --end (default: now), oldest first,
so each frame's persistence check sees the frames before it.

--upload  restore persistence state from R2 first, then publish every frame
          (png -> geojson -> manifest -> state -> latest.json) and health.json.
--shadow  also append the frame's shadow line to <region>/shadow/<date>.jsonl.

Exit code is non-zero on any failure; only an error CODE is printed."""
import argparse
import json
import os
import resource
import sys
import time
import urllib.error
from datetime import datetime, timezone

from sat import fetch, storage
from sat.decode import FlagLayoutError
from sat.manifest import now_utc, shadow_line
from sat.pipeline import process_frame


class RunError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def error_code(e):
    if isinstance(e, (storage.StorageError, RunError)):
        return e.code
    if isinstance(e, FlagLayoutError):
        return "FLAG_LAYOUT_CHANGED"
    if isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return "UPSTREAM_FETCH_FAILED"
    if isinstance(e, OSError):
        return "LOCAL_IO_FAILED"
    return "INTERNAL_ERROR"


def choose_slots(cfg, end, n):
    per_source = {s["name"]: fetch.list_scans(s["bucket"], s["product"], end, 2, cfg["cadence_min"])
                  for s in cfg["sources"]}
    union = sorted(set().union(*per_source.values()))
    complete = [t for t in union if all(t in v for v in per_source.values())]
    # Prefer slots every source has published; a source still in latency for the
    # newest slot would otherwise make every "latest" frame partial.
    slots = (complete if len(complete) >= n else union)[-n:]
    return slots, per_source


def run(a, cfg, store):
    state_path = os.path.join(a.state_dir, f"{a.region}.npz")
    if store:
        blob = store.get_bytes(storage.state_key(a.region))
        if blob is not None:
            os.makedirs(a.state_dir, exist_ok=True)
            with open(state_path, "wb") as f:
                f.write(blob)
            print(f"state: restored from R2 ({len(blob)} bytes)")
        else:
            print("state: none in R2 (cold start)")

    end = datetime.fromisoformat(a.end.replace("Z", "+00:00")) if a.end else datetime.now(timezone.utc)
    slots, per_source = choose_slots(cfg, end, a.scans)
    if not slots:
        raise RunError("NO_SCANS_FOUND")

    for slot in slots:
        t0 = time.perf_counter()
        paths = {}
        for s in cfg["sources"]:
            hit = per_source[s["name"]].get(slot)
            paths[s["name"]] = fetch.download(s["bucket"], hit[1], a.data_dir) if hit else None
        t1 = time.perf_counter()
        m, _, _ = process_frame(a.region, cfg, slot, paths, a.state_dir, a.out_dir)
        t2 = time.perf_counter()
        stamp = slot.strftime("%Y%m%dT%H%MZ")
        if store:
            out = lambda name: os.path.join(a.out_dir, name)  # noqa: E731
            storage.publish_frame(
                store, a.region, stamp, m,
                png_path=out(f"{a.region}_{stamp}.png"),
                coverage_path=out(m["coverage_geojson"]) if m["coverage_geojson"] else None,
                manifest_path=out(f"{a.region}_{stamp}.json"),
                state_path=state_path,
            )
            if a.shadow:
                storage.append_shadow(store, a.region, slot.strftime("%Y-%m-%d"), shadow_line(m, slot.isoformat()))
        t3 = time.perf_counter()
        c = m["counts"]
        print(f"{a.region} slot={slot:%Y-%m-%dT%H:%MZ} status={m['status']} SMOKE={c['SMOKE']} DUST={c['DUST']} "
              f"raw_smoke={c['raw_smoke']} raw_dust={c['raw_dust']} below={c['below_threshold']} "
              f"transient={c['transient_suppressed']} warming={c['warming_up_suppressed']} volcanic={c['volcanic_suppressed']} "
              f"snowice={c['snowice_suppressed']} daylight={m['daylight_fraction']:.3f} "
              f"cloud={m['cloud_fraction']} coverage={m['coverage']} fallback={m['fallback_pixels']} "
              f"mode={m['scan_mode']} fetch_s={t1 - t0:.1f} process_s={t2 - t1:.2f}"
              + (f" upload_s={t3 - t2:.2f}" if store else ""))
        for x in m["missing"]:
            print(f"    missing: {x}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--end", help="ISO UTC; scans starting after this are ignored")
    ap.add_argument("--scans", type=int, default=1)
    ap.add_argument("--config", default="config/regions.json")
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--shadow", action="store_true")
    a = ap.parse_args(argv)
    if a.shadow and not a.upload:
        ap.error("--shadow requires --upload")

    with open(a.config) as f:
        cfg = json.load(f)[a.region]

    store, previous_health = None, None
    try:
        if a.upload:
            store = storage.R2.from_env()
            previous_health = store.get_json(f"{a.region}/health.json")
        run(a, cfg, store)
        if store:
            storage.write_health(store, a.region, previous_health, now_utc(), ok=True)
    except Exception as e:  # noqa: BLE001 — every failure becomes a code, never a trace
        code = error_code(e)
        cause = getattr(e, "cause_type", None)
        print(f"error: {code}" + (f" ({cause})" if cause else f" ({type(e).__name__})"), file=sys.stderr)
        if store:
            try:
                storage.write_health(store, a.region, previous_health, now_utc(), ok=False, error_code=code)
            except storage.StorageError:
                print("error: R2_HEALTH_WRITE_FAILED", file=sys.stderr)
        return 1
    finally:
        print(f"peak_rss_mb={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1e6 if sys.platform == 'darwin' else 1e3):.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
