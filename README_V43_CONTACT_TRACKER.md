# Backend v43 — Contact Tracker

Adds the outreach execution layer after Final Output.

## New flow

Final Output → Contact Tracker

Contact Tracker stores execution fields only. It does not mutate PM ratings, Final Output, or Lead Pool records.

## New endpoints

- `POST /ranking/final/send-to-contact-tracker`
- `GET /contact-tracker?region=Karnataka`
- `POST /contact-tracker/update`
- `POST /contact-tracker/remove`
- `GET /contact-tracker/export.csv?region=Karnataka`
- `GET /contact-tracker/summary?region=Karnataka`

## Send-to-tracker behavior

Rows are selected from Final Output by `buckets` or `ngo_refs`.

Default if nothing is supplied: Final Shortlist.

Dedupes by:

1. `ngo_ref`
2. website domain
3. normalized NGO name + district

Existing tracker rows are merged for missing contact/source data and skipped, not duplicated.

## Contact statuses

- `not_started`
- `contacted`
- `connected`
- `not_connected`
- `meeting_scheduled`
- `meeting_done`
- `follow_up_needed`
- `not_interested`
- `on_hold`

## Tests

Adds `tests/test_contact_tracker.py`.
