# v48 — Admin Undo / Redo Safety Layer

Adds backend-level undo/redo for the irreversible-feeling workflow moves:

1. Lead Pool decisions/imports/deletes and `send-to-ranking`.
2. PM shortlisting response/task updates that feed Combined Review.
3. Final Ranking rows sent to Contact Tracker, plus tracker edits/removals.

## New endpoints

- `GET /admin/undo-redo/status?region=Karnataka`
- `POST /admin/undo` with `{ "password": "...", "region": "Karnataka" }`
- `POST /admin/redo` with `{ "password": "...", "region": "Karnataka" }`

`ADMIN_PASSWORD` must be set. Undo/redo restores internal file snapshots under `RUNS_DIR`, so it works for the CSV/JSON state files used by this deployment.

## Notes

- The journal is stored in `RUNS_DIR/undo_redo`.
- Default history depth is 50 actions. Override with `DFP2_MAX_UNDO_ENTRIES`.
- Undo is regional where possible. PM/workstream-only actions are global and can still be undone from any recovery panel.
