# Search Worker v76 — Railway deployment

1. Extract this ZIP into the root of the Git repository connected to the existing Railway search-worker service.
2. Keep or attach a persistent volume at `/data`.
3. Set the variables below:

```text
RUNS_DIR=/data/runs
ADMIN_PASSWORD=<your existing password>
DFP2_PRODUCTION=true
DFP2_SERVICE_ROLE=full
FRONTEND_ORIGIN=https://<frontend>.up.railway.app
SERPER_API_KEY=<single funded key>
SERPER_CONCURRENCY=4
ANTHROPIC_API_KEY=<existing key>
```

4. Delete `SERPER_API_KEYS`, `DFP2_ADMIN_TOKEN` and `DFP2_REQUIRE_MUTATION_AUTH` if they remain from older releases.
5. Leave `FIRECRAWL_API_KEY` unset until a generated fetch-retry queue actually requires it.
6. Deploy and confirm:

```text
GET /health
GET /karnataka-recovery/ownership-self-test
GET /karnataka-recovery/capacity?serper_concurrency=4
```

7. Begin production with `RUN_01_zero_query_known_urls_6091.csv`; then follow the numbered recovery stages in the frontend.

The service uses `ADMIN_PASSWORD` only. There is no separate mutation token.
