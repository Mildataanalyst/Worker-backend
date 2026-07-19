# Railway worker v63 — Serper-only two-pass recovery

This release makes the 29,000-NGO run safe to operate without Brave.

- Darpan exact search first.
- Registered name + geography second.
- Two Serper queries maximum per NGO by default.
- Brave, Firecrawl, and paid rename recovery disabled by default.
- Existing website/email-domain candidates are verified before any paid search.
- Fetch failures are retained as `candidate_site_unreachable`.
- Retry files carry the candidate URL and retry it without a search query.
- Confirmed/probable websites are exported for the normal Avika-fit scan.
- The repository engine accepts supplied websites and skips website search.
