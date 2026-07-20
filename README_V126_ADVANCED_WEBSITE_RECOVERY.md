# V126 — Advanced Website Recovery UI

- The website re-check action now explicitly invokes `strategy=smart`.
- The upload accepts Darpan-ready identity columns.
- The panel explains staged Serper search, optional Brave fallback and compliance/PDF verification.
- Preview displays Darpan ID, evidence grade/type/page and search provider.
- Summary separates confirmed/probable, manual-review and incomplete records.
- Maximum accepted re-check input is 30,000 rows; 2,000–5,000-row operational batches are recommended.

Sample columns:

```csv
name,district,state,darpan_id,email,phone,registered_address
```
