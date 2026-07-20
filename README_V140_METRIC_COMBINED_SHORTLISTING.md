# Frontend v140 — Metric Combined Shortlisting

## Changes

- Removed per-PM `0/52`-style completion counts from the PM cards.
- Removed the PM leaderboard/rank-count side panels from the scoring workspace.
- Added a single **Shortlisting new metrics** cohort table showing only:
  - Total to be done
  - Left in this cohort
- Added admin controls to permanently delete one assigned NGO or a 1-based range from any PM shortlist.
- Rebuilt **Combined Review** as **Combined Shortlisting**.
- Combined Shortlisting now shows only completed three-metric assessments.
- Added columns and descending sorting for:
  - Child Progression & Alumni Outcomes
  - Learning Model
  - Development Ecosystem
  - Combined score = `(A + B + C) / 15`
- Duplicate assessments of the same NGO are averaged by the backend and displayed as one NGO row.

## Deployment dependency

Deploy backend v81 before this frontend so `/workstream/admin/delete-tasks` and the metric-first `/ranking/compiled-review` payload are available.
