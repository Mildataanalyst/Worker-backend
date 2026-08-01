# DFP 2.0 Search Worker v73 — Railway release

This is the complete search worker for NGO Discovery and Karnataka Recovery.

## Main changes

- Uses exactly one funded Serper account through `SERPER_API_KEY`.
- `SERPER_API_KEYS` is ignored, even if an old Railway variable remains.
- Default account concurrency is 4 and is capped at 8.
- Failed provider attempts do not consume an NGO's logical-query budget.
- Zero-query verification for known URLs and saved candidates.
- Staged identity recovery, mismatch continuation, page-type classification and resumable checkpoints.
- Permanent NGO IDs written to results, audits, retry queues, Avika inputs and repository exports.
- Firecrawl remains optional and selective.

## Railway

Required variables:

```text
RUNS_DIR=/data/runs
SERPER_API_KEY=<single funded 59k-credit key>
SERPER_CONCURRENCY=4
ANTHROPIC_API_KEY=<existing key>
DFP2_ADMIN_TOKEN=<shared token>
DFP2_REQUIRE_MUTATION_AUTH=true
DFP2_PRODUCTION=true
FRONTEND_ORIGIN=https://<frontend>.up.railway.app
```

Mount the worker checkpoint volume at `/data`.

After deployment:

```text
GET /health
GET /karnataka-recovery/capacity?serper_concurrency=4
```

See `RAILWAY_DEPLOYMENT.md` and `README_V73_NGO_ID_SINGLE_SERPER.md`.
