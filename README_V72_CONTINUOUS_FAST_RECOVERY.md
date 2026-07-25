# DFP 2.0 Worker V72 — Continuous Fast Recovery

## Why V71 looked slow despite 24-way concurrency

V71 used fixed waves of 24 NGOs. It waited for every NGO in a wave to finish or
reach the row deadline before starting the next wave. A few slow or unreachable
websites therefore left most worker slots idle. The Serper count per completed
NGO was normal; the scheduling and deep website verification were the
bottlenecks.

## Changes

- Replaced fixed 24-row waves with a continuously replenished worker pool.
  Whenever one NGO finishes, the next pending NGO starts immediately.
- Preserved provider hard-pause semantics. On credit exhaustion, no new work is
  submitted; requests already in flight settle and completed rows are
  checkpointed before the run pauses.
- Fast Recovery now performs a shallow official-site identity check. Deep
  Recovery retains the existing deeper verification settings.
- Avika rule prefilter and compact Haiku classification from V71 are unchanged.

## Fast Recovery defaults

- `FAST_RECOVERY_CONCURRENCY=24`
- `FAST_RECOVERY_MAX_VERIFY_PER_ROW=1`
- `FAST_RECOVERY_VERIFY_MAX_PAGES=2`
- `FAST_RECOVERY_FETCH_TIMEOUT=8`
- `FAST_RECOVERY_HARD_FETCH_DEADLINE_SEC=12`
- `FAST_RECOVERY_MAX_ROW_SECONDS=45`
- `FAST_RECOVERY_FETCH_RETRY_ATTEMPTS=1`

All values can be overridden in Railway.

## Expected UI interpretation

`Serper queries / completed NGOs` should normally be between 1 and 2. A count
below `2 × completed` is not evidence of missing searches because rows can stop
after the first successful query or use a direct/entity-register match.

The key health indicators are rows/minute and row-timeout percentage.
