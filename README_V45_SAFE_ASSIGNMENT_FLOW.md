# Backend v45 — Safe Lead Pool → Ranking assignment

Adds stricter admin-gated assignment from Lead Pool to PM Ranking.

## What changed
- `/workspace/{region}/send-to-ranking` now requires `ADMIN_PASSWORD` in the request body.
- Supports assigning approved leads to one PM, split across PMs, or to every selected PM.
- Does not overwrite existing PM tasks or responses. It only appends new tasks.
- Skips leads already assigned/rated/finalized by name or website and marks the Lead Pool row as already rated.
- Returns explicit counts: new tasks, new leads, already existing / not sent again.
- Lead Pool import responses now separate `added` from `already_existing_count` / `merged`, so the frontend can show clear confirmation.

Use with frontend v70.
