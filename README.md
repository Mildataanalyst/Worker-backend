# DFP 2.0 Search Worker v77 — Final Ownership Guard

Complete Railway-deployable search worker for NGO Discovery and Karnataka Recovery.

## What is fixed

- A page that merely mentions the NGO can never be exported as an owned site.
- Directory, registry, fundraiser, marketplace, vendor, client-list, news, article, research, government, travel and social pages are carrier evidence only.
- Existing and historical URLs are re-fetched and re-verified under the same current rules; no old label is required and no old status grants ownership.
- Exact-name text proves entity relevance only. Automatic verification separately requires domain-control evidence and page-level self-identification.
- Deep URLs receive an independent site-root ownership check. A programme/article page cannot be verified when the domain root belongs to another publisher.
- Parent/project pages require an explicitly supplied parent identity, an explicit relationship statement and root-domain confirmation.
- Hosted microsites require an identity-bearing subdomain plus page self-identification and source-record evidence.
- A known official URL that fails to fetch remains `candidate_fetch_pending`; it is never replaced by a directory result.
- Three independent fail-closed layers protect the output: candidate verification, a final checkpoint/export guard, and a deterministic rebuild of the Avika input from only safe verified rows.
- Every source row and every shortlist-facing output retains its permanent `DFP-NGO-XXXXXXXXXXXXXXXX` ID.
- Uses one funded `SERPER_API_KEY`; provider attempts do not consume another logical NGO query.
- Completed rows are checkpointed and resumable. Firecrawl remains optional and selective.

## Required Railway variables

```text
RUNS_DIR=/data/runs
ADMIN_PASSWORD=<your existing password; same on all three services>
DFP2_PRODUCTION=true
DFP2_SERVICE_ROLE=full
FRONTEND_ORIGIN=https://<frontend>.up.railway.app
SERPER_API_KEY=<your single funded Serper key>
SERPER_CONCURRENCY=4
ANTHROPIC_API_KEY=<your existing Anthropic key>
```

Mount a persistent worker volume at `/data`. Leave `FIRECRAWL_API_KEY` unset initially.

## Production start

Start directly with `RUN_01_zero_query_known_urls_6091.csv`. Every retained URL is rechecked from the live page; historical labels are never required and never grant ownership. The historical 44-NGO file is not a deployment gate and is not shown as a normal production button.

## Health checks

```text
GET /health
GET /karnataka-recovery/ownership-self-test
GET /karnataka-recovery/capacity?serper_concurrency=4
```
