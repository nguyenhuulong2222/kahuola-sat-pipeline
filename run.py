"""python run.py --region hawaii|conus [--end ISO] [--scans N]

Processes the latest N scan slots ending at --end (default: now), oldest first,
so each frame's persistence check sees the frames before it."""
import argparse
import json
import resource
import sys
import time
from datetime import datetime, timezone

from sat import fetch
from sat.pipeline import process_frame


def choose_slots(cfg, end, n):
    per_source = {s["name"]: fetch.list_scans(s["bucket"], s["product"], end, 2, cfg["cadence_min"])
                  for s in cfg["sources"]}
    union = sorted(set().union(*per_source.values()))
    complete = [t for t in union if all(t in v for v in per_source.values())]
    # Prefer slots every source has published; a source still in latency for the
    # newest slot would otherwise make every "latest" frame partial.
    slots = (complete if len(complete) >= n else union)[-n:]
    return slots, per_source


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--end", help="ISO UTC; scans starting after this are ignored")
    ap.add_argument("--scans", type=int, default=1)
    ap.add_argument("--config", default="config/regions.json")
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args(argv)

    with open(a.config) as f:
        cfg = json.load(f)[a.region]
    end = datetime.fromisoformat(a.end.replace("Z", "+00:00")) if a.end else datetime.now(timezone.utc)
    slots, per_source = choose_slots(cfg, end, a.scans)
    if not slots:
        print(f"no scans found for {a.region} before {end.isoformat()}", file=sys.stderr)
    for slot in slots:
        t0 = time.perf_counter()
        paths = {}
        for s in cfg["sources"]:
            hit = per_source[s["name"]].get(slot)
            paths[s["name"]] = fetch.download(s["bucket"], hit[1], a.data_dir) if hit else None
        t1 = time.perf_counter()
        m, _ = process_frame(a.region, cfg, slot, paths, a.state_dir, a.out_dir)
        t2 = time.perf_counter()
        c = m["counts"]
        print(f"{a.region} slot={slot:%Y-%m-%dT%H:%MZ} status={m['status']} SMOKE={c['SMOKE']} DUST={c['DUST']} "
              f"raw_smoke={c['raw_smoke']} raw_dust={c['raw_dust']} below={c['below_threshold']} "
              f"transient={c['transient_suppressed']} volcanic={c['volcanic_suppressed']} "
              f"snowice={c['snowice_suppressed']} daylight={m['daylight_fraction']:.3f} "
              f"cloud={m['cloud_fraction']} coverage={m['coverage']} "
              f"fetch_s={t1 - t0:.1f} process_s={t2 - t1:.2f}")
        for x in m["missing"]:
            print(f"    missing: {x}")
    print(f"peak_rss_mb={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.0f}")


if __name__ == "__main__":
    main()
