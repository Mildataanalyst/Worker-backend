# v49 — Admin delete all Approved Leads

Adds a password-protected backend action for clearing the Approved Leads bucket from Lead Pool memory.

## New endpoint

`POST /workspace/{region}/approved-leads/delete-all`

Payload:

```json
{
  "password": "ADMIN_PASSWORD",
  "confirm": true
}
```

Behaviour:
- Requires `ADMIN_PASSWORD` through the existing admin check.
- Requires `confirm: true` to prevent accidental scripted deletion.
- Deletes only Lead Pool rows whose `curation_status` is `approved_for_ranking` or `approved_with_comment`.
- Does not delete PM ranking submissions or workstream data.
- Creates an undo snapshot, so the action can be reversed from the Admin Undo / Redo panel.
