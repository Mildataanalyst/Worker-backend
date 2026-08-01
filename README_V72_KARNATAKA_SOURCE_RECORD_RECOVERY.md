# V72 — Karnataka Source-Record Recovery

## Purpose

Adds a separate `/karnataka-recovery` workflow for repairing the Karnataka Darpan coverage exercise without changing or deleting the legacy Fast / Deep Recovery workflow.

The new workflow is source-record based. A row is never removed merely because its normalised NGO name and district match another row.

## New recovery modes

- `regression_test` — 25/44-row technical golden-set testing.
- `known_url_identity` — zero-query verification of retained URLs.
- `saved_candidate_fetch` — zero-query verification of saved candidates and timeout rows.
- `missing_query_only` — exactly one missing logical query.
- `enhanced_search` — staged legal/public/project/acronym/address recovery.
- `new_unlinked` — full search for source records without historical source-row coverage.
- `identity_collision` — source-specific handling for same-name/same-district records.
- `firecrawl_retry` — direct fetch first; optional crawler spend only for classified fetch failures.

A CSV may use `recovery_mode_override` to select the exact stage per row. This is used by the mixed identity-collision file.

## Correctness changes

- Immutable `source_record_id` and source fingerprint.
- No input deduplication by name + district + state.
- Zero-query reuse before Serper.
- Staged query generation using legal name, referral/public identity, project/parent, registration reference, address/pincode, acronym and distinctive tokens.
- Up to 10 viable candidates verified per query.
- Directory, registry, article, social, donor and partner pages are carrier evidence only; they can never be selected as the official NGO website.
- After a mismatch, the worker continues to the next candidate and may replace the final generic query with one carrier-alias recovery query.
- Organisation-controlled 1NGO/Site123/hosted pages and verified parent/project pages can be accepted with sufficient identity evidence.
- Website discovery status and DFP-fit status are separate.
- Every completed row is checkpointed; timeout paths retain candidates, audit events and counters.

## Provider handling

- `Not enough credits` is treated as a permanently exhausted Serper key.
- Provider attempts and logical queries are tracked separately.
- The same logical query fails over to another funded key.
- The run pauses only when no usable key remains.
- `/karnataka-recovery/capacity` preflights masked keys and reports safe concurrency.
- Effective concurrency is clamped to `healthy Serper keys × per-key concurrency`.

## Firecrawl cost controls

- Firecrawl is optional and off by default.
- Direct HTTP and URL normalisation run first.
- Firecrawl is used only for blocked, SSL or JavaScript-classified failures.
- The run has a hard credit ceiling.
- Basic proxy is the default.
- The current Firecrawl v2 scrape and credit-usage endpoints are used.

## API

- `GET /karnataka-recovery/modes`
- `GET /karnataka-recovery/capacity`
- `POST /karnataka-recovery/start`
- `GET /karnataka-recovery/status/{run_id}`
- `POST /karnataka-recovery/pause/{run_id}`
- `POST /karnataka-recovery/resume/{run_id}`
- `POST /karnataka-recovery/cancel/{run_id}`
- `GET /karnataka-recovery/runs`
- `GET /karnataka-recovery/export/{run_id}/{kind}`

## Exports

Results, source-level audit, query plan, manual-review queue, terminal no-site queue, next retry CSV, Avika input, filtered repository, summary and errors.

## Environment

Required for search modes:

```env
SERPER_API_KEYS=key1,key2,key3
# or SERPER_API_KEY=key1
```

Optional:

```env
FIRECRAWL_API_KEYS=key1,key2
# or FIRECRAWL_API_KEY=key1
FAST_RECOVERY_RUN_AVIKA_FILTER=true
```

Keep `RUNS_DIR` on a Railway persistent volume.

## Validation

- Python compilation passed.
- Full worker suite: **62 passed**.
- New Karnataka recovery suite: **8 passed**.
