# DFP 2.0 Backend v44 — Archive/Import Existing-Ranking Guard

Small backend follow-up on v43.

## Change

Lead Pool imports now immediately check incoming rows against existing PM ranking/review data.

This applies to:

- direct Lead Pool imports / referrals
- archived/history run imports via `lead-pool/import-run`

If a row already exists in PM ranking/review data, the backend marks it as:

- `curation_status = already_rated`
- `ranking_status = Already Rated`
- `existing_ranking_ref = <normalized NGO name>`

The row is still imported/merged so new contact/referral/archive information is preserved, but it will not appear as an approved queue item or be re-sent to PM Ranking.

## Why

Archive/history runs are allowed to send rows to Lead Pool, but they should not create duplicate PM review tasks for NGOs already rated earlier.

## Compatibility

No endpoint paths changed. This is a backend-only refinement on top of v43.
