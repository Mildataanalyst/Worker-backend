# Core Backend v93 — Password-free Avika and Shortlisting Pool

The following internal operator workflows no longer require an admin password:

- Avika-selected NGO import to the Shortlisting Pool
- Lead Pool import, update, curate, decision import, delete and run import
- Approve and send selected NGOs to PM shortlisting
- NGO-ID historical backfill

These actions remain protected by explicit row selection, confirmation, duplicate checks and undo history. Unrelated administrative routes remain password-protected.
