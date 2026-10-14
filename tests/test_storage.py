"""R2 publishing order and failure behaviour, against an in-memory fake client."""
import json

import pytest

from sat import storage


class FakeClient:
    def __init__(self, fail_on=None):
        self.objects, self.order, self.fail_on = {}, [], fail_on

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        if self.fail_on and Key.endswith(self.fail_on):
            raise ConnectionError("https://ACCOUNTID.r2.cloudflarestorage.com unreachable")
        self.objects[Key] = Body
        self.order.append(Key)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            e = Exception("missing")
            e.response = {"Error": {"Code": "NoSuchKey"}}
            raise e

        class Body:
            def __init__(self, b):
                self.b = b

            def read(self):
                return self.b
        return {"Body": Body(self.objects[Key])}


def _frame(tmp_path):
    for name in ("f.png", "f.geojson", "f.json", "h.npz"):
        (tmp_path / name).write_bytes(b"x")
    manifest = {"scan_start_utc": ["2026-07-14T13:51:17.7Z", "2026-07-14T13:51:18.9Z"],
                "generated_at_utc": "2026-10-14T00:00:00Z", "status": "night"}
    return manifest, {k: str(tmp_path / v) for k, v in
                      (("png_path", "f.png"), ("coverage_path", "f.geojson"),
                       ("manifest_path", "f.json"), ("state_path", "h.npz"))}


def test_publish_order_latest_last(tmp_path):
    c = FakeClient()
    manifest, paths = _frame(tmp_path)
    latest = storage.publish_frame(storage.R2(c, "b"), "conus", "20260714T1350Z", manifest, **paths)
    assert c.order == [
        "conus/frames/20260714T1350Z.png",
        "conus/frames/20260714T1350Z_coverage.geojson",
        "conus/frames/20260714T1350Z.json",
        "conus/state/history.npz",
        "conus/latest.json",
    ]
    # A night frame still becomes latest — an honest pointer, not a stale daytime one.
    assert latest["status"] == "night"
    assert latest["scan_start_utc"] == "2026-07-14T13:51:17.7Z"
    assert json.loads(c.objects["conus/latest.json"])["png_key"] == "conus/frames/20260714T1350Z.png"


@pytest.mark.parametrize("fail_on", [".png", "_coverage.geojson", "1350Z.json", "history.npz"])
def test_no_latest_if_an_earlier_upload_fails(tmp_path, fail_on):
    c = FakeClient(fail_on=fail_on)
    manifest, paths = _frame(tmp_path)
    with pytest.raises(storage.StorageError) as ei:
        storage.publish_frame(storage.R2(c, "b"), "conus", "20260714T1350Z", manifest, **paths)
    assert ei.value.code == "R2_PUT_FAILED"
    assert "ACCOUNTID" not in str(ei.value)     # the code, never the endpoint
    assert "conus/latest.json" not in c.objects


def test_health_counts_failures_and_keeps_last_success():
    c = FakeClient()
    s = storage.R2(c, "b")
    h1 = storage.write_health(s, "hawaii", None, "T1", ok=True)
    h2 = storage.write_health(s, "hawaii", h1, "T2", ok=False, error_code="UPSTREAM_FETCH_FAILED")
    h3 = storage.write_health(s, "hawaii", h2, "T3", ok=False, error_code="R2_PUT_FAILED")
    assert h3 == {"last_run_utc": "T3", "last_success_utc": "T1",
                  "last_error_code": "R2_PUT_FAILED", "consecutive_failures": 2}
    assert storage.write_health(s, "hawaii", h3, "T4", ok=True)["consecutive_failures"] == 0


def test_shadow_appends(tmp_path):
    c = FakeClient()
    s = storage.R2(c, "b")
    storage.append_shadow(s, "hawaii", "2026-10-14", '{"a":1}')
    storage.append_shadow(s, "hawaii", "2026-10-14", '{"a":2}')
    assert c.objects["hawaii/shadow/2026-10-14.jsonl"] == b'{"a":1}\n{"a":2}\n'


def test_missing_env_is_a_code(monkeypatch):
    for k in storage.ENV:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(storage.StorageError) as ei:
        storage.R2.from_env()
    assert ei.value.code == "R2_CONFIG_MISSING"
