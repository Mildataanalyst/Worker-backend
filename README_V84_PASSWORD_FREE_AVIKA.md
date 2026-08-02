# Worker v84 — Password-free Avika Fit Review

This release removes the global admin-password gate from normal operator workflows.

Password-free routes include:

- `POST /repository/start` — including `mode=avika&run_type=avika_filter`
- repository resume/cancel
- recheck start/pause/stop/resume/cancel
- presence-check start/cancel
- all Karnataka Recovery controls

Destructive run deletion and unrelated administrative mutations remain protected.

No new Railway variables are required. `ADMIN_PASSWORD` may remain configured for other protected admin routes; Avika ignores it.
