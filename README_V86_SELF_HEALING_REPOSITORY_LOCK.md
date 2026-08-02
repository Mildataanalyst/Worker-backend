# Worker v86 — self-healing Avika/repository lock

- Clears orphaned `.repository_active.lock` files after Railway restarts.
- Preserves the interrupted run folder, checkpoints, and partial outputs.
- Adds `/repository/recover-stale-lock` for safe manual recovery.
- Returns HTTP 409 only when a real Avika/discovery subprocess is active.
- Includes all v85 password-free operator routes and v83 recovery changes.
