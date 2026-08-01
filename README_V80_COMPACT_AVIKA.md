# DFP 2.0 Search Worker v80 — Compact Avika Filter

Complete Railway-ready FastAPI worker repository.

## Avika mode

Start through `POST /repository/start?mode=avika&run_type=avika_filter`.

- Uses **zero Serper queries**.
- Fetches only the websites supplied in the CSV.
- Deterministically removes obvious exclusions.
- Sends remaining ambiguous/plausible NGOs to compact Claude Haiku classification.
- Returns YES / MAYBE / NO, confidence, reason code and a 20–35 word description.
- Does not run story, partner or media enrichment.
- Preserves permanent NGO ID, source-record ID and source-batch fields.

Default dedicated Avika limit: 10,000 rows per run.
