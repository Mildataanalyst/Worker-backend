# Worker v83 — large batches and automatic checkpoint resume

This complete Railway worker retains the v82 strict ownership and stage-handoff fixes and adds:

- 100 MB streamed CSV upload safety cap (the old 8 MB limitation is removed);
- up to 20,000 Karnataka Recovery source records per run, including 15,000-row enhanced-search batches;
- up to 20,000 rows per compact Avika run;
- a durable background checkpoint supervisor;
- automatic recovery after Railway/container restarts;
- automatic retry after transient Serper/Firecrawl capacity pauses and unexpected worker errors;
- exponential retry backoff, capped at five minutes, with a 50-attempt safety ceiling;
- explicit user pauses and cancellations are never auto-resumed;
- streamed upload handling to avoid loading a 100 MB CSV into worker memory at once.

Recommended Railway variables are in `.env.example`.
