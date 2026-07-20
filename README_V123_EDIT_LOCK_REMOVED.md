# Frontend v123 — PM edit locking removed

- Removed the PM-wide and global edit-lock controls from the admin gear.
- Removed all lock-state checks from PM Shortlisting.
- Metric scores, rationales, exception override, NGO details, submit, update, clear and delete controls are no longer disabled by stored lock state.
- Existing previous overall rankings remain intentionally read-only; this is separate from edit locking.
- Stale `edit_locks` data returned by an older backend is ignored by the frontend.
