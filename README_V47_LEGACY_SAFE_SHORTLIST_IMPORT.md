# Backend v47 — Legacy-safe shortlist import

This release keeps all existing PM shortlist/review work untouched while enforcing source tag + shortlisting comment only for new leads going forward.

## Key rules

- Existing PM tasks/responses are never mutated, backfilled, reset, reassigned, or overwritten.
- Legacy PM-reviewed NGOs may not have `source_tag` or `shortlisting_comment`. That is valid.
- If a Lead Pool row matches an existing PM task by normalized NGO name or website domain, it is skipped as already assigned/rated.
- Missing metadata is required only for genuinely new leads that would create new PM tasks.
- Excel shortlisting imports also skip already assigned/rated NGOs instead of blocking them for missing metadata.

## Added/confirmed helpers

- Canonical source tag normalization.
- Shortlisting comment accessor.
- Truthy Excel cell parsing for `send_for_shortlisting`.
