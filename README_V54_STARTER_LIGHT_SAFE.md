# DFP2 Backend v54 — Starter-Light Safe Build

This build is designed for Render Starter / 512MB RAM.

## Important guarantees

- It does **not** delete workspace data.
- It does **not** delete lead pool / PM review / final ranking data.
- The maintenance cleanup endpoint protects any run that was imported/sent to Lead Pool by checking:
  - `workspaces/*/lead_pool.csv` source_run/run_id columns
  - `workspaces/*/workspace_log.jsonl` lead_pool_import_run entries
- Cleanup is dry-run by default and requires `confirm=true` to actually delete anything.

## Low-memory changes

- Lazy imports Anthropic only when AI routes are used.
- Lazy imports BeautifulSoup only when website scoring/fetching is used.
- Fixed BeautifulSoup lazy-import recursion bug.
- Skips startup reconciliation by default via `DFP2_SKIP_STARTUP_RECONCILE=true`.
- Lowers defaults for Starter RAM:
  - `MAX_ROWS_PER_RUN=1000`
  - `PRESENCE_MAX_ROWS=1000`
  - `PRESENCE_MAX_TOTAL_QUERIES=2000`
  - `MAX_UPLOAD_BYTES=8000000`
  - `STORY_MAX_ARTICLES=15`
- Import-to-lead-pool endpoint returns only first 200 rows by default to avoid large response memory spikes. Full data remains stored in the backend and can be fetched from the lead-pool endpoint.

## Required env for Render Starter

```env
WEB_CONCURRENCY=1
DFP2_SKIP_STARTUP_RECONCILE=true
DFP2_LIGHTWEIGHT_ARCHIVE_COUNTS=true
DFP2_LIGHT_RESPONSES=true
DFP2_MAX_UNDO_ENTRIES=15
MAX_ROWS_PER_RUN=1000
PRESENCE_MAX_ROWS=1000
PRESENCE_MAX_TOTAL_QUERIES=2000
AI_BATCH_SIZE=200
DIRECT_AI_MAX_ITEMS=10
```

## Optional protected-run check

```http
GET /admin/maintenance/protected-runs?password=YOUR_ADMIN_PASSWORD
```

## Cleanup endpoint

Dry run only:

```json
POST /admin/maintenance/cleanup-runs
{"password":"YOUR_ADMIN_PASSWORD","keep_latest":10}
```

Actual cleanup, while protecting imported/sent-to-lead-pool runs:

```json
POST /admin/maintenance/cleanup-runs
{"password":"YOUR_ADMIN_PASSWORD","keep_latest":10,"confirm":true}
```
