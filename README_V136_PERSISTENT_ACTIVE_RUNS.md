# Frontend v136 — Persistent Active Runs Monitor

- Adds a small floating **Runs** button on the NGO Discovery page.
- Reads the worker's durable `/jobs` registry every three seconds, so live jobs are rediscovered after refresh or navigation rather than relying on React/browser memory.
- Shows General Discovery, Bulk Discovery, Smart Recovery and NGO Presence Check jobs with live progress, elapsed time, current work, run ID and worker location.
- Supports **Go to run**, **Pause**, **Resume** and **End run** where the underlying module supports those actions.
- Automatically reattaches the existing page progress panels to live jobs after a refresh.
- Shortlisting and lead-pool work remain independent and do not stop worker jobs.
