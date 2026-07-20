# Frontend v134 — Archive render resilience

Builds on v133. Fixes the run-history UI path without changing either backend.

## Findings

- v132 did not violate the Rules of Hooks: its added hooks were top-level and in a stable order.
- v132 also completes `next build`; a production build alone cannot reproduce data-dependent React render failures.
- The archive UI previously showed the same empty-state text while loading, after a failed/invalid payload, and when the archive was genuinely empty.
- Backend values were rendered directly. A malformed row or object-valued scalar could throw during `.map()` and leave the previous empty-state render visible.

## Changes

- Uses `safeStoryJSON` and `safeSearchJSON` with service-relative paths for archive and disk endpoints.
- Validates that archive responses contain an array in `rows` or `runs` (including a nested `data` envelope).
- Filters non-object rows.
- Adds separate loading, loaded-empty, and load-error states.
- Converts backend display fields to safe scalar strings.
- Normalizes `downloads` before field access.
- Disables row actions when `run_id` is absent.
- Adds an archive-only React error boundary with a reload action.
- Refresh now reloads discovery archive, repository archive, and disk usage together.
- Retains v133 Smart Recovery watchdog indicators.

## Validation

`npm run build` completed successfully with only the pre-existing font, CSS compatibility, and AdminUndoRedo hook-dependency warnings.
