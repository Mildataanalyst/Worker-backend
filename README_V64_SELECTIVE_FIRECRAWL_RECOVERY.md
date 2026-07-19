# Railway worker v64 — Selective Firecrawl Recovery

This worker mirrors backend v76 and adds a separate `strategy=firecrawl`
recovery flow. The standard Serper pass always performs direct/local
verification only and therefore spends zero Firecrawl credits.

See `README_V76_SELECTIVE_FIRECRAWL_RECOVERY.md` in the backend package or the
release deployment guide for environment variables and run order.
