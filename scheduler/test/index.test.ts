// node --test: mocked fetch, no network. Run: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { dispatch, DispatchError, type Env } from "../src/index.ts";

const TOKEN = "TEST_TOKEN_never_log_me";
const T = Date.UTC(2026, 9, 14, 18, 5);
const env = (over: Partial<Env> = {}): Env => ({
  GH_OWNER: "o", GH_REPO: "r", GH_WORKFLOW: "sat.yml", GH_REF: "main",
  GH_DISPATCH_TOKEN: TOKEN, ...over,
});

function mock(status: number) {
  const calls: { url: string; init: RequestInit }[] = [];
  const f = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(null, { status, headers: { "x-secret-header": TOKEN } });
  }) as unknown as typeof fetch;
  return { f, calls };
}

function capture() {
  const lines: string[] = [];
  return { lines, log: (l: string) => void lines.push(l) };
}

test("204 -> ok, correct request", async () => {
  const { f, calls } = mock(204);
  const { lines, log } = capture();
  assert.equal(await dispatch(env(), T, f, log), 204);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.github.com/repos/o/r/actions/workflows/sat.yml/dispatches");
  const h = calls[0].init.headers as Record<string, string>;
  assert.equal(calls[0].init.method, "POST");
  assert.equal(h.Authorization, `Bearer ${TOKEN}`);
  assert.equal(h.Accept, "application/vnd.github+json");
  assert.equal(h["X-GitHub-Api-Version"], "2022-11-28");
  assert.equal(h["User-Agent"], "kahuola-sat-scheduler");
  assert.deepEqual(JSON.parse(calls[0].init.body as string), { ref: "main" });
  assert.ok(calls[0].init.signal);
  assert.deepEqual(JSON.parse(lines[0]), { scheduled: "2026-10-14T18:05:00.000Z", status: 204, error: null });
  assert.ok(!lines.join("").includes(TOKEN));
});

for (const status of [401, 404, 500]) {
  test(`${status} -> throws http_${status}`, async () => {
    const { f } = mock(status);
    const { lines, log } = capture();
    await assert.rejects(dispatch(env(), T, f, log), (e: DispatchError) => e.code === `http_${status}`);
    assert.deepEqual(JSON.parse(lines[0]).error, `http_${status}`);
    assert.ok(!lines.join("").includes(TOKEN));
  });
}

test("missing token -> throws missing_token, no request", async () => {
  const { f, calls } = mock(204);
  const { lines, log } = capture();
  await assert.rejects(dispatch(env({ GH_DISPATCH_TOKEN: undefined }), T, f, log),
    (e: DispatchError) => e.code === "missing_token");
  assert.equal(calls.length, 0);
  assert.equal(JSON.parse(lines[0]).error, "missing_token");
});

test("network error -> throws network_error, no token in logs", async () => {
  const f = (async () => { throw new Error(`boom ${TOKEN}`); }) as unknown as typeof fetch;
  const { lines, log } = capture();
  await assert.rejects(dispatch(env(), T, f, log), (e: DispatchError) => e.code === "network_error");
  assert.ok(!lines.join("").includes(TOKEN));
});

test("timeout -> aborts and throws timeout", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = ((_u: string, init: RequestInit) => new Promise((_, rej) => {
    init.signal!.addEventListener("abort", () => rej(new Error("aborted")));
  })) as unknown as typeof fetch;
  const { lines, log } = capture();
  const p = dispatch(env(), T, f, log);
  t.mock.timers.tick(10_000);
  await assert.rejects(p, (e: DispatchError) => e.code === "timeout");
  assert.equal(JSON.parse(lines[0]).error, "timeout");
});

test("fetch handler -> 404", async () => {
  const res = await worker.fetch();
  assert.equal(res.status, 404);
});

test("scheduled handler propagates failure", async () => {
  const orig = globalThis.fetch;
  const orig_log = console.log;
  const logged: string[] = [];
  globalThis.fetch = mock(500).f;
  console.log = (l: string) => void logged.push(l);
  try {
    await assert.rejects(worker.scheduled({ scheduledTime: T } as ScheduledController, env()));
  } finally {
    globalThis.fetch = orig;
    console.log = orig_log;
  }
  assert.ok(!logged.join("").includes(TOKEN));
});
