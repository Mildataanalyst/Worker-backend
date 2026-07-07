# DFP 2.0 Backend v39 — Lead Pool → Approved Leads → Ranking

Built on backend v38 full carrier-phrase smart recovery.

## Main changes

- Lead Pool is now the curation board.
- Approved Leads is a filtered view of Lead Pool, not a copied table.
- Only leads with `curation_status` in `approved_for_ranking` or `approved_with_comment` can be sent to PM ranking.
- Insufficient leads can still be approved for ranking, but only as `approved_with_comment`.
- Contact number is optional for ranking.
- Human referrals preserve `contact_number`, `referred_by`, and source tag.
- Archive/history runs can be imported to Lead Pool as `Archive Import`.
- Sending to ranking now checks existing PM tasks/ratings and skips duplicates.
- Added read-only ranking views: compiled review, final board, final summary.

## New/extended Lead Pool fields

`curation_status`, `curation_comment`, `approved_by`, `approved_at`, `decided_by`, `decided_at`, `ranking_status`, `source_mix`, `one_line_understanding`, `background_summary`, `existing_ranking_ref`, `duplicate_of`.

## New endpoints

- `POST /workspace/{region}/lead-pool/curate`
- `GET /workspace/{region}/approved-leads`
- `GET /workspace/{region}/funnel-metrics`
- `GET /ranking/compiled-review`
- `GET /ranking/final-board`
- `GET /ranking/final-summary`

## Gate rule

`POST /workspace/{region}/send-to-ranking` now hard-blocks rows that are not approved.

Allowed:
- `approved_for_ranking`
- `approved_with_comment`

Blocked:
- `pending_review`
- `needs_follow_up`
- `sent_back_to_pool`
- `duplicate`
- `already_rated`
- `hold`

## Validation

- `python -m py_compile backend/main.py` passed.
- FastAPI smoke test passed for import → blocked send → approve with comment → send to ranking.
