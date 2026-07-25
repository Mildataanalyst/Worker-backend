# V69 — Fast Recovery concurrency + Avika-filtered output

## What changed

1. Fast Recovery now runs NGO rows concurrently.
2. Multiple Serper keys are leased round-robin with a per-key in-flight cap.
3. A used-up/invalid key is disabled automatically after a permanent credit/auth response.
4. HTTP 429 is treated as temporary: only that key enters cooldown; other keys continue.
5. Fast Recovery automatically sends recovered websites through the exact Bulk Discovery Avika classifier.
6. The filtered repository CSV is exported as `dfp2_repository_output.csv`.
7. Lead Pool import prefers the filtered repository output instead of raw website-recovery rows.
8. `fast` and `deep` strategy names are accepted by the worker, matching the frontend.

## Railway variables

Recommended initial settings for 5–10 free Serper keys:

```env
SERPER_API_KEYS=key1,key2,key3,key4,key5
FAST_RECOVERY_CONCURRENCY=24
SERPER_CONCURRENCY_PER_KEY=3
SERPER_429_COOLDOWN_SEC=20
BULK_SEARCH_CONCURRENCY=16
FAST_RECOVERY_RUN_AVIKA_FILTER=true
FAST_RECOVERY_FILTER_TIMEOUT_SEC=14400
```

The worker automatically limits effective Fast Recovery concurrency to:

`min(FAST_RECOVERY_CONCURRENCY, live_key_count × SERPER_CONCURRENCY_PER_KEY)`

Some keys may already have fewer than 2,500 credits remaining. No balance guess is made. A key is used until Serper returns a permanent exhausted/invalid response, then it is removed for the rest of that run.

## Outputs

- `results`: raw website-recovery result for every input NGO.
- `repository`: Avika-filtered reviewable output; this is the only file that should be sent to Lead Pool.
- `avika_audit`: classifier audit.
- `avika_rejected`: classifier rejected rows.
- `avika_input`: recovered websites handed to the Bulk Discovery classifier.
