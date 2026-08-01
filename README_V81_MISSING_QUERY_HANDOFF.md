# DFP2 Worker v81

Fixes a production-critical handoff defect in `missing_query_only` mode.

When the one missing logical query completes but does not verify a website, the record now becomes `enhanced_search_required`, is marked retry-required, and is exported to the Next retry CSV with `recovery_mode_override=enhanced_search`. It is no longer incorrectly labelled `no_owned_site_after_enhanced_recovery`.
