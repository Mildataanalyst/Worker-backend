# v51 — NGO Presence Check

Adds a new long-running backend module for targeted NGO digital-presence checks.

## Endpoint set

- `POST /repository/presence/start`
- `GET /repository/presence/status/{run_id}`
- `GET /repository/presence/results/{run_id}`
- `GET /repository/presence/export/{run_id}/{kind}`
- `POST /repository/presence/cancel/{run_id}`

## Input CSV

Required columns:

- `ngo_name` or `name`
- `state`

Optional column:

- `center_name`

Repeated NGO rows are grouped by NGO name + state for search, so the backend does not waste Serper queries repeatedly. The final output still emits one row per original CSV row so each center remains reviewable.

## Output

`dfp2_ngo_presence_check.csv` contains:

- NGO Name
- Center Name
- State
- Official Website
- Website Confidence
- Official Site Match
- Website Strength
- Presence Score
- Digital Presence Assessment
- Evidence
- Search Channels Found
- Query Used / Queries Used

The module uses the existing smart re-check identity matching architecture, then adds deterministic website-sophistication scoring and channel classification for social/directory/article signals.

## Environment knobs

- `PRESENCE_MAX_ROWS`, default `10000`
- `PRESENCE_MAX_TOTAL_QUERIES`, default `12000`
- `PRESENCE_FETCH_TIMEOUT`, default `10`

It continues to use the existing Serper configuration: `SERPER_API_KEY` or `SERPER_API_KEYS`.
