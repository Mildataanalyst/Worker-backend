# Core Backend v89 — password-free NGO ID migration

The following operations are intentionally password-free:

- `POST /admin/ngo-ids/backfill`
- `GET /admin/ngo-ids/status`
- `GET /admin/ngo-ids/export.csv`

The backfill is idempotent and only adds missing immutable NGO IDs. It never deletes, merges, deduplicates, or changes ranking decisions. All other protected mutations retain the existing `ADMIN_PASSWORD` guard.
