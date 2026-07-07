# V56 Force CORS Worker Patch

This build adds an outer CORS guard that stamps CORS headers on every response, including early auth/service-role failures and OPTIONS preflight responses.

Use for Railway worker service:
- DFP2_SERVICE_ROLE=full
- RUNS_DIR=/tmp/dfp2-runs
- FRONTEND_ORIGINS=https://thedailyfeedingprogram-production.up.railway.app,https://the-daily-feeding-program.vercel.app,http://localhost:3000

This patch is intended to fix browser errors like:
`No 'Access-Control-Allow-Origin' header is present on the requested resource`
for `/repository/start?mode=bulk` and related worker routes.
