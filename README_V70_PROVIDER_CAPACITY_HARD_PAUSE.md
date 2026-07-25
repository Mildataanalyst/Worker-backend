# V70 — Provider-capacity hard pause

Fast/Deep Website Recovery now pauses the entire run on the first unusable paid-provider key/account rather than silently failing over or checkpointing rows as provider failures.

## Covered providers

- Serper
- Firecrawl
- Claude Haiku / Anthropic during the automatic Avika filter
- Brave Search when enabled

## Behaviour

- HTTP 402 or explicit credit/billing/quota exhaustion: hard pause.
- HTTP 401/403 key rejection: hard pause so a bad/revoked key is visible rather than silently ignored.
- HTTP 429 rate limiting: retry/cooldown only; it is not treated as credit exhaustion.
- 5xx/network errors: bounded retry; they do not trigger a credit pause.
- Reaching the configured Firecrawl run budget: hard pause.

When paused:

1. The current parallel batch is allowed to settle safely.
2. Successful rows from that batch are checkpointed.
3. The NGO that hit the provider failure and any interrupted rows are left pending; they are not written as completed failures.
4. Status becomes `paused / provider_credit_exhausted` with provider, masked key, HTTP status and details.
5. After credits are added or the key is replaced/fixed, Resume re-reads the environment and continues from checkpoints.
6. If Haiku pauses inside Avika filtering, the Avika filter directory and its own checkpoints remain in place; Resume restarts only the unfinished filtering stage after recovery is already complete.

No new environment variable is required. Keep `RUNS_DIR` on a Railway persistent volume.
