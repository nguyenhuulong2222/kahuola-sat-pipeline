"""Read-only R2 check: list conus/ and hawaii/ keys, print latest.json and
health.json for both regions. Credentials from the environment only; nothing
is written anywhere, and the endpoint is never printed.

  R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=... \\
      python scripts/verify_r2.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sat.storage import R2, StorageError  # noqa: E402


def main():
    try:
        s = R2.from_env()
        for region in ("conus", "hawaii"):
            print(f"== {region}/")
            paginator = s._c.get_paginator("list_objects_v2")
            n = 0
            for page in paginator.paginate(Bucket=s._bucket, Prefix=f"{region}/"):
                for o in page.get("Contents", []):
                    print(f"  {o['Key']:60s} {o['Size']:>9}  {o['LastModified']:%Y-%m-%dT%H:%M:%SZ}")
                    n += 1
            print(f"  ({n} objects)")
            for name in ("latest.json", "health.json"):
                print(f"-- {region}/{name}")
                print(json.dumps(s.get_json(f"{region}/{name}"), indent=2))
    except StorageError as e:
        print(f"error: {e.code}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
