# kahuola-sat-scheduler

A tiny Cloudflare Worker whose only job is to start the `sat.yml` workflow on
time. A Cron Trigger fires at :05, :15, :25, :35, :45, :55 UTC and sends one
`workflow_dispatch` request to the GitHub API. GitHub does all the processing.

Why: GitHub's own `schedule:` never registered for this repo and is
best-effort even when it works, while persistence (2 of 3 scans within 35 min)
needs runs on time. `sat.yml`'s `schedule:` stays commented out — enabling
both would trigger twice.

- No public surface: `workers_dev = false`, no routes; `fetch` returns 404.
- Logs only `{scheduled, status, error}`. Never the token or response headers.
- Any non-204 answer throws, so the failure shows in the Cron Trigger logs.

## Token

A fine-grained GitHub personal access token, stored as the Worker secret
`GH_DISPATCH_TOKEN` (never in a file, never in `wrangler.toml`):

- Resource owner: the account that owns `kahuola-sat-pipeline`
- Repository access: only `kahuola-sat-pipeline`
- Repository permissions: **Actions: Read and write** (Metadata: read is automatic)
- Expiration: 1 year — renew before it lapses (runs stop with `http_401`)

## Deploy

```
cd scheduler
npm install
npx wrangler secret put GH_DISPATCH_TOKEN   # paste the token at the prompt
npx wrangler deploy
```

## Check

```
npm run typecheck
npm test                                    # mocked fetch, no network
gh run list -R nguyenhuulong2222/kahuola-sat-pipeline --limit 6
```

Expect `workflow_dispatch` runs roughly 10 minutes apart. Error codes in the
Worker logs: `missing_token`, `timeout`, `network_error`, `http_<status>`.
