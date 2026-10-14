"""List and download GOES ABI L2 objects from NOAA's public NODD buckets.

Plain HTTPS (S3 ListObjectsV2), no credentials and no aws CLI dependency."""
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import defusedxml.ElementTree as ET

S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
SCAN_START = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})(\d)_")
SCAN_MODE = re.compile(r"-M(\d)_")


def list_keys(bucket, prefix):
    keys, token = [], None
    while True:
        q = {"list-type": "2", "prefix": prefix}
        if token:
            q["continuation-token"] = token
        url = f"https://{bucket}.s3.amazonaws.com/?{urllib.parse.urlencode(q)}"
        with urllib.request.urlopen(url, timeout=60) as r:
            root = ET.fromstring(r.read())
        for c in root.iter(f"{S3_NS}Contents"):
            keys.append((c.find(f"{S3_NS}Key").text, int(c.find(f"{S3_NS}Size").text)))
        if root.findtext(f"{S3_NS}IsTruncated") != "true":
            return keys
        token = root.findtext(f"{S3_NS}NextContinuationToken")


def scan_start(key):
    m = SCAN_START.search(key)
    if not m:
        return None
    y, doy, hh, mm, ss, tenth = (int(g) for g in m.groups())
    return datetime(y, 1, 1, hh, mm, ss, tenth * 100_000, tzinfo=timezone.utc) + timedelta(days=doy - 1)


def scan_mode(key):
    """'M6', 'M4', ... from the file name; None if absent."""
    m = SCAN_MODE.search(key)
    return f"M{m.group(1)}" if m else None


def slot_of(t, cadence_min):
    """Floor a scan start to its cadence slot (ADPC starts at :x1:17, ADPF at :x0:20)."""
    return t.replace(minute=t.minute - t.minute % cadence_min, second=0, microsecond=0)


def list_scans(bucket, product, end, hours_back, cadence_min):
    """{slot: (scan_start, key)} for scans starting in (end - hours_back, end].
    When a slot holds more than one scan, Mode 6 wins; otherwise the earliest."""
    out = {}
    for h in range(hours_back, -1, -1):
        t = end - timedelta(hours=h)
        prefix = f"{product}/{t:%Y}/{t.timetuple().tm_yday:03d}/{t:%H}/"
        for key, _ in sorted(list_keys(bucket, prefix)):
            s = scan_start(key)
            if s is None or s > end or s <= end - timedelta(hours=hours_back):
                continue
            slot = slot_of(s, cadence_min)
            prev = out.get(slot)
            if prev is None or (scan_mode(prev[1]) != "M6" and scan_mode(key) == "M6"):
                out[slot] = (s, key)
    return out


def download(bucket, key, data_dir):
    dest = os.path.join(data_dir, bucket, key)
    if os.path.exists(dest):
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    urllib.request.urlretrieve(f"https://{bucket}.s3.amazonaws.com/{urllib.parse.quote(key)}", tmp)
    os.replace(tmp, dest)
    return dest
