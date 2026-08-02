# Core Backend v91 — Railway deployment

Deploy this repository to the existing core-backend Railway service. Keep the historical persistent volume mounted at `/data`.

## Variables

```text
RUNS_DIR=/data/runs
ADMIN_PASSWORD=<existing password>
DFP2_PRODUCTION=true
DFP2_SERVICE_ROLE=core
FRONTEND_ORIGIN=https://<frontend>.up.railway.app
```

Do not add Serper or Firecrawl keys to the core service for this workflow.

Health check:

```text
/health
```
