# Worker v85

- Makes the full `/repository/*`, `/karnataka-recovery/*`, `/jobs/*` and `/enrichment/*` operator namespaces password-free.
- Adds an HTTP-level regression test proving Avika start cannot be intercepted by the admin-password middleware.
- Adds service role and capability metadata to `/health` for robust frontend routing.
