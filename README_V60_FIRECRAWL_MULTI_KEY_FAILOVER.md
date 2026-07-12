# V60 — Firecrawl multi-key failover

This patch updates the V59 Deep Enrichment Railway worker to support multiple Firecrawl API keys safely.

## Railway variables

Preferred multi-key configuration:

```env
FIRECRAWL_API_KEYS=fc-key-1,fc-key-2,fc-key-3
```

Legacy single-key configuration remains supported:

```env
FIRECRAWL_API_KEY=fc-key-1
```

When `FIRECRAWL_API_KEYS` is present, it takes precedence. Commas or newlines are accepted and whitespace is stripped. Duplicate keys are removed.

## Behaviour

- New official-site crawls rotate across enabled keys.
- A `401`, `402`, or `403` disables that key for the current worker process and retries the new request with the next key.
- Once a `/crawl` job is created, its creating key is pinned to that crawl for polling, pagination, timeout cancellation, and user cancellation.
- Independent `/scrape` fallback calls may fail over between keys.
- `429` responses use retry/backoff on the same key rather than disabling it.
- `5xx` responses are retried on the same key.
- Raw keys are never written to status files or API responses. Only masked, hashed labels are exposed.
- The `/enrichment/config` endpoint now reports configured, enabled, and disabled key counts safely.

Multiple keys only add capacity when they point to usable credit pools. Several keys attached to the same exhausted Firecrawl account may all return quota errors.
