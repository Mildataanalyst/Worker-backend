# Search Worker v80 — Railway deployment

Deploy this repository to the existing worker Railway service. Keep a persistent volume mounted at `/data`.

## Variables

```text
RUNS_DIR=/data/runs
ADMIN_PASSWORD=<existing password>
DFP2_PRODUCTION=true
DFP2_SERVICE_ROLE=full
FRONTEND_ORIGIN=https://<frontend>.up.railway.app
SERPER_API_KEY=<single funded Serper key>
SERPER_CONCURRENCY=4
ANTHROPIC_API_KEY=<existing Anthropic key>
AVIKA_MAX_ROWS_PER_RUN=10000
AVIKA_SITE_TEXT_CHARS=1800
AVIKA_MAX_TOKENS=120
```

Do not configure `SERPER_API_KEYS`. Avika mode does not spend Serper credits; the Serper key remains necessary for ordinary discovery and recovery runs.

Firecrawl remains optional:

```text
FIRECRAWL_API_KEY=<only when required>
```

Health check:

```text
/health
```
