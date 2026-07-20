# Frontend v139 — Independent Fast and Deep Recovery

The NGO Discovery advanced section now presents two explicit recovery cards.

## Fast Recovery card

- Separate Fast CSV upload.
- Start Fast Recovery.
- Live progress, elapsed time, ETA, Serper and Firecrawl usage.
- Pause, Resume, Cancel, and End & save controls.
- Fast Results download is available independently of Deep Review.
- Eligible Deep CSV and optional Send eligible to Deep Review action.

## Deep Recovery card

Deep Recovery can be started from:

- a separately uploaded Deep CSV; or
- a selected finalized Fast run from history with eligible rows.

The original Fast Results remain unchanged and downloadable.

## Active run controls

The persistent Runs drawer now also exposes Cancel separately from End & save for recovery runs.

## Validation

- TypeScript `tsc --noEmit` passed.
- Next.js lint passed with only the existing layout-font and AdminUndoRedo dependency warnings.
- Next.js production compile and generation phases passed.
