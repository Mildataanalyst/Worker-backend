# Backend v46 — Shortlist Metadata + Excel Decisions

Adds required source tags and shortlisting comments before leads can be sent for PM shortlisting.

New/changed behavior:
- Lead Pool rows now preserve `source_tag`, `send_for_shortlisting`, and `shortlisting_comment`.
- Approving a lead for shortlisting requires a source tag and comment.
- Sending approved leads to PM ranking blocks rows missing source tag/comment.
- New endpoint: `POST /workspace/{region}/lead-pool/import-decisions` for Excel/CSV-driven decisions.
- Duplicate/already assigned/rated protections remain unchanged; existing PM work is not overwritten.
