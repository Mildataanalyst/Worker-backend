# Backend v24 — tiered output + anti-zero-output safety net

This version builds on v23 reject-flow fixes.

## Key changes

- Benchmark/must-have organisations are **calibration references only**.
  - The query generator still hard-blocks literal benchmark-name searches.
  - If a benchmark naturally surfaces, it is tagged as `Benchmark reference`, not counted as fresh discovery.

- General Discovery output is now tiered:
  - `Fresh strong lead`
  - `Manual-check promising`
  - `Benchmark reference`

- Added an anti-zero-output safety net.
  - If strict review produces too few non-benchmark rows, the backend promotes vetted high-scoring candidates into `Manual-check promising`.
  - This prevents a 1,500-query run from returning a blank sheet purely because the classifier was too strict.
  - Weak/donation/social/research junk is still not promoted.

- Status JSON now includes:
  - `actionable_stories`
  - `benchmark_rows`
  - `safety_net_added`

## Optional Render env vars

```env
DISCOVERY_MIN_OUTPUT_ROWS=20
DISCOVERY_MANUAL_FALLBACK_MIN_SCORE=8
```

If unset, minimum non-benchmark manual/fresh rows are:

- <300 query run: 5
- 300–799: 8
- 800–1499: 12
- >=1500: 20

These are safety-net rows, not a quality target.
