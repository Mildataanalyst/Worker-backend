# DFP 2.0 backend revamp changes

Added a non-destructive workspace layer above the existing repository/discovery/story/workstream code.

## New persistence

The backend now creates region workspaces under:

```text
RUNS_DIR/workspaces/<region_slug>/
  lead_pool.csv
  workspace_log.jsonl
```

This preserves all existing run directories, repository exports, story/general discovery archives, workstream data, and logs.

## New endpoints

- `GET /workspace/{region}/lead-pool`
- `POST /workspace/{region}/lead-pool/import`
- `GET /workspace/{region}/lead-pool/export.csv`
- `POST /workspace/{region}/send-to-ranking`
- `GET /ranking/final-output`

## Lead Pool behavior

- Imports rows from General Discovery, Bulk Mode, Referrals, or Recovery.
- Normalizes and dedupes by website domain or normalized NGO name + district.
- Merges source type, preserving combinations such as `Internet + Referral`.
- Keeps referral contact/notes fresh because those are human-verified fields.

## Send to Ranking behavior

- Reads the selected region's Lead Pool.
- Skips NGO names already present in PM task lists.
- Distributes only new rows evenly across selected PMs.
- Appends tasks without deleting or resetting existing PM tasks/responses.
- Writes a `ranking_batches` entry and global log entry into existing workstream data.
