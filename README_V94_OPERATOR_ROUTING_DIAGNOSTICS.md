# Backend v94

- Prevents misleading 401 responses if a repository request is accidentally routed to the core service.
- Returns the proper service-role response instead.
- Adds service-role metadata to `/health` for frontend routing diagnostics.
