# DFP 2.0 Backend v41 — Hardening Pass 2

Changes over v40:
- Global mutation middleware now uses only `DFP2_ADMIN_TOKEN`; `ADMIN_PASSWORD` remains for body-password admin flows and no longer breaks those routes by itself.
- `DFP2_REQUIRE_MUTATION_AUTH=true` now fails closed with `503` unless `DFP2_ADMIN_TOKEN` is configured.
- Smart recovery verification fetch now routes through the shared safe fetcher, with public-host validation, redirect validation, content-type checks, standard-port checks, and streaming size limits.
- CSV formula neutralization now checks the left-trimmed value before formula sigils.
- Lead Pool full-file writes are atomic and guarded by a process-local lock.

Validation:
- `python -m py_compile main.py engine/dfp2_engine_safe_v5_live_status.py` passed.
- `pytest -q` passed: 7 passed.

Still not solved:
- This remains an internal/demo gate, not SSO.
- In-memory job state remains unsuitable for multi-worker/horizontal production.
- Next/PostCSS dependency upgrades belong to a separate frontend dependency pass.
