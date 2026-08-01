# DFP 2.0 Search Worker v73

This release contains the complete Karnataka Recovery worker, stable NGO IDs, resumable concurrency, and one-account Serper handling.

## One Serper account only

Configure only:

```text
SERPER_API_KEY=<the funded 59k-credit account>
SERPER_CONCURRENCY=4
```

`SERPER_API_KEYS` is deliberately ignored. This prevents an old unfunded key from silently receiving a share of the run.

The Karnataka Recovery UI has a per-run credit ceiling. Set it to `59000`; one query is retained as preflight headroom. A failed provider attempt does not consume the NGO's logical-query allowance. Permanent exhaustion pauses safely with completed rows checkpointed.

Recommended starting values:

```text
Row concurrency: 12
Serper account concurrency: 4
Serper run-credit ceiling: 59000
Firecrawl: off
```

Raise Serper concurrency to 6–8 only after the 44-NGO regression run is stable.

## Stable IDs

Every Karnataka Recovery input row receives or preserves a `DFP-NGO-XXXXXXXXXXXXXXXX` ID. The ID is written to results, audit, retry, Avika input and repository exports, then preserved when the core backend imports the row into the Lead Pool and PM shortlisting.

## Recovery behaviour

- Known URLs and saved candidates are verified with zero Serper queries.
- Search modes use legal/public/project names, acronyms, spelling variants, address, pincode and registration evidence.
- Directories, donor pages, articles and profiles are evidence only and can never become the selected NGO website.
- The worker continues through remaining candidates after a mismatch.
- Fetch failures are retained as retryable technical statuses.
- Every completed source record is checkpointed; pause, resume and end-and-save are supported.

## Railway

Mount a worker volume at `/data` and set `RUNS_DIR=/data/runs`. This worker volume stores recovery checkpoints and is separate from the core backend's historical-data volume.
