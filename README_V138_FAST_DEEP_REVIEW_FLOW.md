# Frontend v138 — Fast Recovery and selective Deep Review

## Workflow

The NGO Discovery page now starts **Fast Recovery** as the normal first pass. Difficult rows are counted and saved separately rather than slowing the full dataset.

After the Fast pass is complete or intentionally ended, the user can select:

**Send to Deep Review (N)**

This starts a separate Deep Review child run containing only the queued NGOs.

## Interface changes

- New runs start with `strategy=fast`.
- The live panel shows the active strategy: Fast Recovery, Deep Review, Firecrawl Review, or legacy Smart Recovery.
- The live panel and archive show the number of NGOs set aside for Deep Review.
- The queue is downloadable as **Deep queue CSV**.
- The Deep Review button remains disabled until the source Fast run is finalized.
- Active Runs uses strategy-aware labels and locations.

## Existing Smart Recovery runs

A paused legacy Smart Recovery run presents two choices:

- **Resume Fast** — preserves completed checkpoints and applies the fast profile to the remaining NGOs.
- **Resume Deep** — continues the remaining NGOs with the deep profile.

Rows already completed in the legacy run are not repeated.

## Queue discipline

The queue is selective. It includes uncertain, unreachable, timed-out, incomplete, or identity-ambiguous rows. Clean no-candidate results are not automatically sent through another expensive pass.

## Validation

A complete Next.js production build passed, including linting, type checking, static-page generation, and the `/ngo-discovery` route. Remaining warnings are pre-existing font, CSS compatibility, and `AdminUndoRedo` dependency warnings.
