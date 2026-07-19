# Worker v70 — Independent Fast and Deep Recovery

## Final workflow

Website Recovery now has two independent modes.

### Fast Recovery

- Starts from its own uploaded CSV.
- Produces a downloadable result checkpoint from the first completed row onward.
- Results remain downloadable while running, after Pause, after Cancel, after End, and after completion.
- Uses two search formulations, one strongest-candidate verification path, two-page depth, a 40-second row watchdog, and selective Firecrawl fallback.
- Firecrawl is attempted only when the strongest nominated candidate cannot be read through the free direct fetch path. It is not used on every NGO.
- Ambiguous, unreachable, timed-out, or provider-incomplete rows are written to a separate eligible Deep Review CSV.
- Clean completed searches with no plausible candidate are not automatically repeated.

### Deep Recovery

Deep Recovery can start in either of two ways:

1. Upload a new CSV directly with `strategy=deep`.
2. Start a child run from the eligible queue of a finalized Fast Recovery run.

Deep runs use deeper candidate and page verification, rename recovery, longer deadlines, and selective Firecrawl verification when configured.

The original Fast result is never replaced or withheld when Deep Review starts.

## Controls

Both modes support:

- Pause: stop safely after the current bounded NGO and resume later.
- Resume: continue from completed checkpoints.
- Cancel: mark the run cancelled while preserving completed checkpoints and downloads.
- End & save: intentionally finalize the rows completed so far and preserve downloads.

## Firecrawl configuration

Selective Firecrawl requires all of the following on the worker service:

```text
SMART_RECHECK_USE_FIRECRAWL=true
FAST_RECHECK_USE_FIRECRAWL=true
DEEP_RECHECK_USE_FIRECRAWL=true
FIRECRAWL_API_KEY=...
```

`FIRECRAWL_API_KEYS` may be used instead for multiple-key failover. Existing total and bucket credit budgets remain enforced.

## API

- `POST /repository/recheck/start?strategy=fast` with CSV upload.
- `POST /repository/recheck/start?strategy=deep` with CSV upload.
- `POST /repository/recheck/deep-review/start/{fast_run_id}`.
- `POST /repository/recheck/pause/{run_id}`.
- `POST /repository/recheck/resume/{run_id}`.
- `POST /repository/recheck/cancel/{run_id}`.
- `POST /repository/recheck/stop/{run_id}`.

## Validation

- 53 worker tests passed.
- Python compilation passed.
- New tests cover Fast Firecrawl profile activation, propagation into verification, and distinct cancellation state.
