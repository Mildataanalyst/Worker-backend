# DFP 2.0 Backend v42 — Persistent Job Registry

This version adds a lightweight durable job registry without introducing Celery/Redis.

## What changed

- New durable job records under `RUNS_DIR/_jobs/<run_id>.json`.
- Repository, recheck, story, and discovery jobs now sync status into the registry.
- New endpoints:
  - `GET /jobs`
  - `GET /jobs/{run_id}`
  - `POST /jobs/{run_id}/cancel`
- Cancel requests are now persisted as `cancel_requested=true` in the job record.
- Recheck/story/discovery loops check the persistent cancel flag in addition to their in-memory event.
- Startup reconciliation scans job records and legacy run folders:
  - active jobs with no live owner are marked `interrupted`
  - matching run status files are marked `interrupted_restart`
  - partial exports remain downloadable
- Repository status responses now include a `job` object.
- Recheck/story status responses now include a `job` object.
- Added job-registry tests.

## What this solves

This makes the single-server internal deployment safer across restarts. The backend no longer has to rely only on Python dictionaries/threads to remember what jobs existed.

## What this does not solve

This is not a full production job queue. It does not provide distributed locking, cross-worker process ownership, automatic retry workers, or horizontal scaling. If the tool becomes multi-user/high-volume, move long jobs to Redis/RQ, Celery, Dramatiq, or a database-backed queue.

## Validation

- `python -m py_compile main.py engine/dfp2_engine_safe_v5_live_status.py`
- `pytest -q` → 9 passed
