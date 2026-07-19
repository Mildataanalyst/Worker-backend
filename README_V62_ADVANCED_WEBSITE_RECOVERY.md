# V74 — Advanced Website Recovery

## What changed

- Smart recovery is now the default re-check strategy.
- Darpan IDs and other registration identifiers are preserved, used for deduplication, searched first, and used as Grade A identity evidence.
- Optional Brave Search API fallback runs only for unresolved rows.
- Candidate domains are deduplicated before verification.
- Official-domain PDFs are retained as evidence and can confirm their parent domain.
- Verification inspects nominated pages, homepages, About, Contact, Compliance, FCRA, annual-report and related pages.
- Optional Firecrawl fallback can render blocked pages and parse PDFs.
- Grade C brand-plus-location matches now require manual review and are no longer accepted automatically.
- Search failures and query-cap skips can no longer become a false “not found” result.
- Re-check input supports up to 30,000 rows, though operational batches of 2,000–5,000 are recommended.

## Recommended input

```csv
name,district,state,darpan_id,email,phone,registered_address
Example Education Trust,Mysuru,Karnataka,KA/2020/1234567,contact@example.org,9876543210,Example address 570001
```

Only `name` is mandatory. `darpan_id`, district, state, organisational email, phone and registered address materially improve identity closure.

## Environment variables

### Search providers

```bash
SERPER_API_KEY=...
# or comma-separated rotation
SERPER_API_KEYS=key1,key2

# Optional fallback for unresolved rows
BRAVE_SEARCH_API_KEY=...
SMART_RECHECK_USE_BRAVE=true
SMART_RECHECK_BRAVE_MAX_QUERIES_PER_ROW=2
```

Smart mode requires at least one of Serper or Brave. Serper remains the recommended primary provider.

### Verification

```bash
# Optional; direct HTTP verification is attempted first
FIRECRAWL_API_KEY=...
# or comma-separated rotation
FIRECRAWL_API_KEYS=key1,key2
SMART_RECHECK_USE_FIRECRAWL=true
SMART_RECHECK_VERIFY_MAX_PAGES=7
SMART_RECHECK_MAX_VERIFY_PER_ROW=3
```

### Scale and budgets

```bash
RECHECK_MAX_ROWS=30000
SMART_RECHECK_MAX_QUERIES_PER_ROW=5
SMART_RECHECK_MAX_TOTAL_QUERIES=150000
SMART_RECHECK_FETCH_TIMEOUT_SECONDS=12
```

For a 5,000-row operational batch, consider a 25,000 total-query cap. Do not classify `skipped_query_cap`, `provider_failure`, or `search_incomplete` rows as having no website.

## Main statuses

- `confirmed_official_site`: exact identifier or multi-attribute identity closure.
- `probable_official_site`: legal-name ownership evidence, but no authoritative identifier closure.
- `possible_site_manual_review`: brand plus geography or similarly incomplete evidence.
- `no_candidate_after_completed_search`: required searches completed successfully, no qualifying candidate found.
- `search_incomplete`: some required searches failed.
- `provider_failure`: all configured provider searches failed.
- `skipped_query_cap`: search not completed because the budget was exhausted.

## Endpoint

```text
POST /repository/recheck/start?strategy=smart
```

The frontend now calls this explicitly, and the backend default is also `smart`.
