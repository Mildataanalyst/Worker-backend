# V59 — Deep Enrichment Railway Worker

This release adds the Deep Enrichment research pipeline to the **Railway worker only**. The normal/core backend is unchanged.

## What the worker does

For up to 100 selected NGOs per run, it:

1. Receives NGO name, website, PM reviewer, PM rating, PM comment, and optional existing category.
2. Uses Firecrawl to crawl the official public website up to the configured page ceiling.
3. Preserves clean page Markdown, metadata, links, source URLs, publication signals, and exact candidate excerpts.
4. Uses Serper for external discovery, including news/media, awards, unusual achievements, alumni, education/employment/sports outcomes, government and institutional references, partners, and adverse or contradictory reporting.
5. Fetches external sources directly first and uses Firecrawl only as a limited fallback.
6. Produces model-ready dossiers without changing PM ratings or making a final transformation decision.
7. Optionally runs a small Claude Haiku pass to suggest categories and separate, explicitly non-final signals.

## API routes

- `POST /enrichment/start`
- `GET /enrichment/status/{run_id}`
- `GET /enrichment/results/{run_id}`
- `GET /enrichment/export/{run_id}/{kind}`
- `POST /enrichment/cancel/{run_id}`
- `POST /enrichment/resume/{run_id}`
- `GET /enrichment/archive`
- `GET /enrichment/config`

Export kinds: `zip`, `csv`, `jsonl`, `packet`, `report`.

## Output bundle

Each completed run generates:

- `master_summary.csv`
- `all_dossiers.jsonl`
- `gpt_fable_packet.md`
- category-grouped Markdown packets
- one research pack per NGO containing `dossier.md`, `evidence.json`, `sources.csv`, official pages, external sources, search records, and highlights
- `run_report.json`
- `deep_enrichment_export.zip`

The Markdown packet is intended for manual upload to GPT-5.6 or Claude/Fable. JSONL is the canonical structured export. CSV is the lightweight control sheet.

## Required Railway variables

```env
FIRECRAWL_API_KEY=
SERPER_API_KEY=
# Existing multi-key configuration is also supported:
# SERPER_API_KEYS=key1,key2
```

## Optional variables

```env
# Optional preliminary categorisation/signals
ANTHROPIC_API_KEY=
ENRICHMENT_HAIKU_MODEL=claude-haiku-4-5-20251001
ENRICHMENT_HAIKU_MAX_TOKENS=2200

# Run ceilings
ENRICHMENT_MAX_NGOS_PER_RUN=100
ENRICHMENT_MAX_PAGES_PER_SITE=50
ENRICHMENT_SERPER_QUERIES_PER_NGO=35
ENRICHMENT_SERPER_RESULTS_PER_QUERY=5
ENRICHMENT_MAX_EXTERNAL_SOURCES=30
ENRICHMENT_EXTERNAL_FIRECRAWL_FALLBACKS=5

# Firecrawl behaviour
FIRECRAWL_SITE_CONCURRENCY=2
FIRECRAWL_CRAWL_TIMEOUT_SECONDS=1800
FIRECRAWL_PAGE_TIMEOUT_MS=60000
FIRECRAWL_POLL_SECONDS=4
ENRICHMENT_ALLOW_SUBDOMAINS=false

# Search pacing
ENRICHMENT_SERPER_DELAY_SECONDS=0.15
```

Keep `FIRECRAWL_API_KEY`, `SERPER_API_KEY`, and `ANTHROPIC_API_KEY` only in Railway's private Variables. Do not use `NEXT_PUBLIC_` names for these secrets.

## Deployment notes

- Deploy this archive to the existing Railway **worker/search service**.
- Keep `DFP2_SERVICE_ROLE=search` or the existing search-worker role configuration.
- The frontend must point `NEXT_PUBLIC_SEARCH_BACKEND_URL` to this worker.
- The job is backgrounded and checkpointed; closing the browser does not stop it.
- Individual NGO failures do not terminate the complete batch.
- Cancelled and partial runs retain completed research packs and can be resumed.

## Validation completed

- Worker test suite: 15 tests passed.
- FastAPI route smoke test passed for health, configuration, and missing-key error handling.
- No live Firecrawl, Serper, or Anthropic request was executed because deployment secrets were not supplied to the local build environment.
