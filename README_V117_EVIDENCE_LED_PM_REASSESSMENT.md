# Frontend v117 — Evidence-led PM Re-assessment

## PM shortlisting changes

- Keeps the earlier overall 1–5 ranking and earlier reason in a grey, read-only block.
- The earlier ranking cannot be edited or overwritten from this screen.
- Adds a prominent Kalkeri Alumni Outcomes tutorial inside a full-screen modal.
- Requires three new 1–5 scores:
  - Alumni Outcomes
  - Learning Model
  - Development Environment
- Requires a minimum 100-character rationale for each new score.
- Shows a collapsible evidence pack above each score.
- Evidence is rendered as a numbered factual package; enter one factual sentence per line from the gear panel.
- Shows source links and an NGO-specific recommended ceiling inside the evidence pack.
- Shows metric-specific mandatory ceiling rules in the Scoring Rules modal.
- Tracks completion using the new metric submission rather than the previous overall ranking.
- Clearing the new assessment removes only the three metric scores and preserves the earlier ranking.

## Tutorial

`public/tutorials/kalkeri-alumni-outcomes.html`

The PM can open it from the three-metric assessment header. It demonstrates:

`actual source → four factual sentences → PM rating → calibration → mandatory ceiling`

## Frontend variables

- `NEXT_PUBLIC_BACKEND_URL`: Railway core backend. PM memory must come from this service.
- `NEXT_PUBLIC_SEARCH_BACKEND_URL`: Railway search/deep-enrichment worker.
- `NEXT_PUBLIC_DFP2_ADMIN_TOKEN`: optional shared mutation token when backend mutation auth is enabled.

The browser also keeps a non-authoritative fallback cache in localStorage under:

`dfp-workstream-fallback`

The backend remains the source of truth whenever reachable.
