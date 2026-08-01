# Worker v78 — password-free Karnataka Recovery

This release fixes HTTP 401 on Karnataka Recovery CSV uploads.

The following routes never require `ADMIN_PASSWORD`:

- `POST /karnataka-recovery/start`
- `POST /karnataka-recovery/pause/{run_id}`
- `POST /karnataka-recovery/cancel/{run_id}`
- `POST /karnataka-recovery/resume/{run_id}`
- all Karnataka Recovery status, capacity and export routes

The password guard remains only for unrelated consequential legacy endpoints.

No frontend change is needed when Frontend v160 is already deployed.
