# Search Worker v73 — Railway deployment

1. Upload this ZIP to the Git repository connected to the existing **search worker** Railway service.
2. Keep or attach a persistent volume mounted at `/data`.
3. Set `RUNS_DIR=/data/runs`.
4. Set only `SERPER_API_KEY` for the funded 59k-credit account.
5. Delete `SERPER_API_KEYS` from Railway Variables. The code ignores it, but removing it avoids confusion.
6. Set `SERPER_CONCURRENCY=4`.
7. Keep `ANTHROPIC_API_KEY` if Avika/Haiku classification is used.
8. Leave `FIRECRAWL_API_KEY` unset until a selective Firecrawl retry is actually required.
9. Set `FRONTEND_ORIGIN` to the frontend Railway domain and deploy.
10. Confirm `GET /health` returns HTTP 200.

First production action: run `TEST_05_all_partner_recommendations_44.csv` in Karnataka Recovery with concurrency 4, Serper concurrency 4, Firecrawl off and Avika off.
