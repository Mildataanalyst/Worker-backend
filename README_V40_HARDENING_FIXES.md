# Backend v40 hardening fixes

Changes made over v39 after code review:

- Fixed `/repository/start` malformed-CSV runtime bug: removed undefined `strategy` reference in the bad CSV path.
- Added optional mutation-route guard. If `DFP2_ADMIN_TOKEN` or `ADMIN_PASSWORD` is set, every POST/PUT/PATCH/DELETE request must send the same value via `X-DFP2-ADMIN-TOKEN`, `X-Admin-Password`, or `Authorization: Bearer ...`.
- Added production CORS guard. Set `FRONTEND_ORIGIN` or comma-separated `FRONTEND_ORIGINS`; wildcard requires `DFP2_ALLOW_WILDCARD_CORS=true` and should only be used for local/demo.
- Replaced the unsafe story article fetch path with a safer fetch wrapper: http/https only, no private/local IP hosts, standard ports only, redirect validation, content-type check, and max response size.
- Switched important JSON status/payload writes to temp-file + `os.replace()` atomic writes.
- Changed repository lock failure from fail-open to fail-closed.
- Centralized CSV formula neutralization for key backend CSV export/write paths.
- Error responses no longer expose raw exception text by default; set `DFP2_DEBUG_ERRORS=true` only for local debugging.

Remaining production caveats:

- This is still not a substitute for proper SSO/network auth. Keep the backend private or behind an auth proxy if exposed.
- Job state is still mostly process/file based, not a persistent queue.
- Frontend dependency audit still requires a breaking Next major upgrade to fully clear.
