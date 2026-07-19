# Railway Worker v66 — Resume Old Website Searches

Mirrors backend v78 resumable website-recovery support.

- Lists durable paused/stopped/cancelled/error/interrupted Serper and Firecrawl jobs.
- Restarts a selected job from existing CSV checkpoints.
- Clears stale cancellation flags from older cancelled jobs.
- Normalises orphaned active statuses to restart-interrupted.
- Preserves original input filename and progress metadata.

Configure `RUNS_DIR` on persistent Railway storage so checkpoint folders survive redeploys.
