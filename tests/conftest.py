import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sat import fetch  # noqa: E402

DATA = os.path.join(ROOT, "data")


def scan_at(bucket, product, when):
    """Local path of the first scan starting at or after `when` (downloaded once into data/)."""
    found = fetch.list_scans(bucket, product, when + timedelta(minutes=10), 1, 10)
    for slot in sorted(found):
        start, key = found[slot]
        if start >= when:
            return fetch.download(bucket, key, DATA)
    pytest.skip(f"no {bucket}/{product} scan at {when.isoformat()}")


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)
