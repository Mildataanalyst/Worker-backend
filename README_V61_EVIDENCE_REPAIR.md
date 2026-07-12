# V61 — Persistent Evidence Repair

## New routes

- `GET /enrichment/repair-preview/{run_id}`
- `POST /enrichment/repair/{run_id}`

The repair workflow uses an already-stored Deep Enrichment run as its source. It preserves the original Serper corpus and does not issue new Serper searches.

## Repair behavior

- Automatically selects dossiers with zero or limited official-site evidence.
- Preserves already-complete official crawls.
- Copies the original NGO research packs into a new repair run.
- Crawls only missing official websites.
- Reuses existing Serper queries, source URLs and external evidence.
- Applies a stricter identity filter to existing external results while preserving the raw source ledger.
- Optionally reruns a blind Haiku synthesis without PM rating/comment context.
- Produces a new full ZIP, Markdown packet, JSONL and master CSV.
- Adds model-readiness, crawl-status and repair fields to the master CSV.

## Firecrawl safety

- Active crawls remain pinned to the key that created them.
- A key failure during polling restarts the site crawl on another configured key.
- When all Firecrawl keys are unavailable, the run pauses as `waiting_for_firecrawl_credits` before any Serper work.
- Resume continues the correct workflow for both normal enrichment and repair runs.

## Validation

`pytest -q` — 24 tests passed.
