"""Cloudflare R2 writer (S3 API).

Credentials come from the environment only. Nothing here ever prints or
returns the endpoint (it contains the account id), a credential, or a
presigned URL; failures surface as a short error CODE.

Key layout per region:
  <region>/frames/<stamp>.png
  <region>/frames/<stamp>.json               manifest
  <region>/frames/<stamp>_coverage.geojson
  <region>/state/history.npz                 persistence state
  <region>/latest.json                       pointer, written LAST
  <region>/health.json
  <region>/shadow/<YYYY-MM-DD>.jsonl         shadow log (download, append, upload)
"""
import json
import os

ENV = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")

FRAME_CACHE = "public, max-age=300"
POINTER_CACHE = "no-store"


class StorageError(RuntimeError):
    """`code` is safe to log and to write to health.json; the message is not."""

    def __init__(self, code, cause=None):
        super().__init__(code)
        self.code = code
        self.cause_type = type(cause).__name__ if cause else None


class R2:
    def __init__(self, client, bucket):
        self._c = client
        self._bucket = bucket

    @classmethod
    def from_env(cls):
        missing = [k for k in ENV if not os.environ.get(k)]
        if missing:
            raise StorageError("R2_CONFIG_MISSING")
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            region_name="auto",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"},
                          connect_timeout=10, read_timeout=30),
        )
        return cls(client, os.environ["R2_BUCKET"])

    def get_bytes(self, key):
        """Object bytes, or None if the key does not exist."""
        try:
            r = self._c.get_object(Bucket=self._bucket, Key=key)
            return r["Body"].read()
        except Exception as e:  # botocore.ClientError and transport errors
            code = getattr(e, "response", {}).get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404", "NotFound"):
                return None
            raise StorageError("R2_GET_FAILED", e) from None

    def get_json(self, key):
        b = self.get_bytes(key)
        if b is None:
            return None
        try:
            return json.loads(b)
        except ValueError:
            return None

    def put_bytes(self, key, data, content_type, cache_control=FRAME_CACHE):
        try:
            self._c.put_object(Bucket=self._bucket, Key=key, Body=data,
                               ContentType=content_type, CacheControl=cache_control)
        except Exception as e:
            raise StorageError("R2_PUT_FAILED", e) from None

    def put_file(self, key, path, content_type, cache_control=FRAME_CACHE):
        with open(path, "rb") as f:
            self.put_bytes(key, f.read(), content_type, cache_control)


def frame_keys(region, stamp):
    base = f"{region}/frames/{stamp}"
    return {"png": f"{base}.png", "manifest": f"{base}.json", "coverage": f"{base}_coverage.geojson"}


def state_key(region):
    return f"{region}/state/history.npz"


def publish_frame(store, region, stamp, manifest, png_path, coverage_path, manifest_path, state_path):
    """Upload one frame in the fixed order png -> geojson -> manifest -> state ->
    latest.json. Any failure raises before latest.json, so the pointer never
    names a frame whose files are not all in place."""
    keys = frame_keys(region, stamp)
    have_png = png_path and os.path.exists(png_path)
    have_cov = coverage_path and os.path.exists(coverage_path)
    if have_png:
        store.put_file(keys["png"], png_path, "image/png")
    if have_cov:
        store.put_file(keys["coverage"], coverage_path, "application/geo+json")
    store.put_file(keys["manifest"], manifest_path, "application/json")
    if state_path and os.path.exists(state_path):
        store.put_file(state_key(region), state_path, "application/octet-stream", POINTER_CACHE)
    starts = [s for s in manifest["scan_start_utc"] if s]
    latest = {
        "manifest_key": keys["manifest"],
        "png_key": keys["png"] if have_png else None,
        "coverage_key": keys["coverage"] if have_cov else None,
        "scan_start_utc": min(starts) if starts else None,
        "generated_at_utc": manifest["generated_at_utc"],
        "status": manifest["status"],
    }
    store.put_bytes(f"{region}/latest.json", json.dumps(latest, indent=2).encode(), "application/json",
                    POINTER_CACHE)
    return latest


def append_shadow(store, region, date_utc, line):
    key = f"{region}/shadow/{date_utc}.jsonl"
    existing = store.get_bytes(key) or b""
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    store.put_bytes(key, existing + line.encode() + b"\n", "application/x-ndjson", POINTER_CACHE)
    return key


def write_health(store, region, previous, now_utc, ok, error_code=None):
    previous = previous or {}
    health = {
        "last_run_utc": now_utc,
        "last_success_utc": now_utc if ok else previous.get("last_success_utc"),
        "last_error_code": None if ok else error_code,
        "consecutive_failures": 0 if ok else int(previous.get("consecutive_failures") or 0) + 1,
    }
    store.put_bytes(f"{region}/health.json", json.dumps(health, indent=2).encode(), "application/json",
                    POINTER_CACHE)
    return health
