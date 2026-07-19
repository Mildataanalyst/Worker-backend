# Worker v69 — Two-pass Fast Recovery and Deep Review

## Purpose

Website recovery now uses a two-pass workflow:

1. **Fast Recovery** scans every NGO with a bounded, low-cost verification profile.
2. Only technically uncertain or genuinely ambiguous rows are written to a separate **Deep Review queue**.
3. A completed or intentionally ended Fast Recovery run can create a resumable Deep Review child run from that queue.

This prevents the full dataset from receiving expensive multi-candidate, multi-page verification while retaining a deeper route for difficult cases.

## Fast Recovery defaults

- Up to 2 search queries per NGO.
- Verify the strongest candidate only.
- Inspect at most 2 pages.
- One direct-fetch attempt.
- 10-second hard fetch deadline.
- 40-second total row watchdog.
- Serper only by default; Brave, Firecrawl, and rename recovery disabled.

All values remain environment-configurable through the `FAST_RECHECK_*` variables.

## Deep Review defaults

- Up to 3 search queries per NGO.
- Verify up to 3 candidates.
- Inspect up to 7 pages per candidate.
- Two direct-fetch attempts.
- 20-second hard fetch deadline.
- 120-second row watchdog.
- Rename/alternate-brand recovery enabled.

All values remain environment-configurable through the `DEEP_RECHECK_*` variables.

## Which rows enter Deep Review

Fast Recovery writes a row to `dfp2_deep_review_input.csv` when it is uncertain for a meaningful reason, including:

- plausible candidate found but identity could not be verified;
- candidate website unreachable;
- manual identity review required;
- row timeout;
- incomplete or failed search/provider call;
- alias or associated-entity ambiguity.

A clean `no_candidate_after_completed_search` result is not automatically repeated. It is queued only when the audit contains a plausible nominated website that could not be verified.

## API changes

- `POST /repository/recheck/start?strategy=fast` starts the default first pass.
- `POST /repository/recheck/deep-review/start/{source_run_id}` creates a Deep Review child run from a finalized source run.
- `POST /repository/recheck/resume/{run_id}?strategy_override=fast` allows an older Smart Recovery run to continue in Fast mode without losing checkpointed rows.
- `strategy_override=deep`, `smart`, and `firecrawl` are also supported where appropriate.

The source run must be complete or intentionally ended before its Deep Review queue can be started. Only one recovery-class run may be active at a time.

## Compatibility and performance safeguards

- Existing v68 result CSVs are migrated before new columns are appended.
- Existing checkpointed rows are preserved and skipped on Resume.
- Summary regeneration is throttled instead of rescanning the full result set after every row.
- Status polling uses lightweight counters rather than repeatedly scanning large output files.
- Archive records expose strategy, parent run ID, and Deep Review count.

## Validation

The full worker suite passes with 50 tests, including tests for profile selection, queue eligibility, checkpoint creation, old-schema migration, child-run creation, and legacy Smart-to-Fast resume.
