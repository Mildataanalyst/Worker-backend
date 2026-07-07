# DFP 2.0 Smart Recovery Rerun v37

This backend keeps the strict first-pass bulk engine unchanged and adds an opt-in smart recovery mode for rows where an official website was not identified earlier.

## Endpoint

Classic mode remains the default:

```bash
POST /repository/recheck/start
```

Smart mode:

```bash
POST /repository/recheck/start?strategy=smart
```

Existing status/results/export/cancel endpoints are unchanged.

## New export kinds

```text
results  -> dfp2_no_website_recheck.csv
audit    -> dfp2_no_website_recheck_audit.csv
summary  -> dfp2_no_website_recheck_summary.json
skipped  -> dfp2_no_website_recheck_skipped_input.csv
errors   -> dfp2_no_website_recheck_errors.log
status   -> dfp2_no_website_recheck_status.json
```

Remaining/skipped rows can also be downloaded with:

```bash
GET /repository/recheck/remaining/{run_id}
```

## Smart recovery behavior

Smart recovery uses staged Serper queries to nominate candidate websites. Candidate sites are then fetched and verified against the record identity before acceptance.

Evidence grades:

- A: government/registration identifier found on site
- B: registered legal name found on site
- C: brand + geography corroboration found on site
- D: nominee exists but site did not verify the record identity

Smart output avoids the phrase `no_official_website` and uses `no_candidate_found` for search-exhaustion cases.

## Environment variables

```text
SMART_RECHECK_MAX_QUERIES_PER_ROW=3
SMART_RECHECK_MAX_TOTAL_QUERIES=15000
SMART_RECHECK_STOP_ON_HIGH_CONFIDENCE=true
SMART_RECHECK_FUZZY_THRESHOLD=0.84
SMART_RECHECK_NOMINATION_SCORE=8
SMART_RECHECK_MAX_VERIFY_PER_ROW=2
SMART_RECHECK_FETCH_TIMEOUT=10
SMART_RECHECK_ALIASES_JSON={... optional ...}
```

## Important reporting language

Use: "website not identified in first pass" or "no candidate found after staged searches".

Do not say: "these NGOs have no websites".

## Operational recommendation for 29k unresolved rows

Run in 2,000–2,500 row batches. Review `summary` and `audit` after each batch before continuing.
