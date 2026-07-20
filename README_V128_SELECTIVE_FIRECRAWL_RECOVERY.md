# Frontend v128 — Selective Firecrawl Recovery

The repository re-check panel now provides two explicit actions:

1. **Run Serper pass** — maximum two Serper searches per NGO; zero Firecrawl.
2. **Run Firecrawl recovery** — accepts the generated recovery queue and spends
   from the configured Firecrawl envelope.

The UI exposes Firecrawl credits used and provides downloads for:

- recovered websites for Avika filtering;
- Firecrawl recovery input;
- unresolved and fetch-error rows;
- detailed audit output.
