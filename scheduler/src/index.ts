// kahuola-sat-scheduler: Cloudflare cron -> GitHub workflow_dispatch for sat.yml.
// GitHub's own cron never registered and is best-effort; persistence needs
// on-time runs. This Worker only triggers; GitHub executes.
//
// Logs carry ONLY the scheduled time, the HTTP status and a short error code.
// Never the token, never response headers or bodies.

export interface Env {
  GH_OWNER: string;
  GH_REPO: string;
  GH_WORKFLOW: string;
  GH_REF: string;
  GH_DISPATCH_TOKEN?: string;
}

export const TIMEOUT_MS = 10_000;

type Log = (line: string) => void;

export class DispatchError extends Error {
  code: string;
  constructor(code: string) {
    super(code);
    this.code = code;
  }
}

export async function dispatch(
  env: Env,
  scheduledTime: number,
  fetchImpl: typeof fetch = fetch,
  log: Log = (line) => console.log(line),
): Promise<number> {
  const scheduled = new Date(scheduledTime).toISOString();
  const report = (status: number | null, error: string | null) =>
    log(JSON.stringify({ scheduled, status, error }));

  if (!env.GH_DISPATCH_TOKEN) {
    report(null, "missing_token");
    throw new DispatchError("missing_token");
  }

  const url =
    `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}` +
    `/actions/workflows/${env.GH_WORKFLOW}/dispatches`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let status: number;
  try {
    const res = await fetchImpl(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "kahuola-sat-scheduler",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: env.GH_REF }),
      signal: ctrl.signal,
    });
    status = res.status;
  } catch {
    const code = ctrl.signal.aborted ? "timeout" : "network_error";
    report(null, code);
    throw new DispatchError(code);
  } finally {
    clearTimeout(timer);
  }

  if (status !== 204) {
    report(status, `http_${status}`);
    throw new DispatchError(`http_${status}`);
  }
  report(status, null);
  return status;
}

export default {
  async scheduled(controller: ScheduledController, env: Env): Promise<void> {
    await dispatch(env, controller.scheduledTime);
  },
  async fetch(): Promise<Response> {
    return new Response(null, { status: 404 });
  },
};
