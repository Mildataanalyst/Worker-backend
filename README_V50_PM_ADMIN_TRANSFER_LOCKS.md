# Backend v50 — PM admin transfer + edit locks

Adds admin-only controls for the PM shortlisting workflow.

## New endpoints

### POST `/workstream/admin/transfer-tasks`
Transfers one PM shortlist item, or an inclusive 1-based range, from one PM to another.

Payload:

```json
{
  "password": "ADMIN_PASSWORD",
  "from_pm": "Avika",
  "to_pm": "Milan",
  "start_index": 15,
  "end_index": 27,
  "move_responses": true
}
```

- Uses 1-based numbering by default, matching the UI.
- Removes transferred tasks from the source PM and appends them to the target PM.
- Reindexes the source PM responses safely after the move.
- If `move_responses` is true, submitted responses move with the transferred tasks.
- Creates an undo snapshot.

### POST `/workstream/admin/lock-edits`
Locks or unlocks PM shortlist edits.

Payload for all PMs:

```json
{
  "password": "ADMIN_PASSWORD",
  "all_pms": true,
  "locked": true
}
```

Payload for selected PMs:

```json
{
  "password": "ADMIN_PASSWORD",
  "pms": ["Avika", "Kamran"],
  "locked": true
}
```

- Locked PMs can view shortlists but cannot submit, edit, or delete responses.
- Creates an undo snapshot.

## Other changes

- `/workstream/submit` now stores `last_submitted_task_index` and `last_submitted_at`.
- `/workstream/submit` and `/workstream/delete-response` return `423` if the PM is locked.
