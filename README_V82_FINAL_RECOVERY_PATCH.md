# DFP2 Worker v82

Production patch for Karnataka Recovery.

- `missing_query_only` is now a handoff stage: every non-verified outcome becomes `enhanced_search_required`, including former manual-review and fetch-pending outcomes.
- Commercial, property, travel, map, directory and marketplace results are rejected before fetch when they lack exact source-record identity.
- Commercial/similarly named sites cannot be auto-verified merely because they share a brand token, address or pincode.
- Foreign same-name domains and programme/CSR pages remain fail-closed without an explicit supplied relationship.
- Multiple source records claiming one domain are downgraded to manual identity review.
- Only safely verified sites can enter the Avika export.

Compatible with Core Backend v91 and Frontend v162.
