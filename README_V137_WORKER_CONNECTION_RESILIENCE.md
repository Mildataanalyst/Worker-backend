# Frontend v137 — Worker connection resilience

- A single failed status poll no longer leaves a permanent red server error.
- Successful polls clear stale connection errors automatically.
- Smart Recovery distinguishes run failure from monitoring-connection failure.
- Displays time since the last successful worker update.
- Retries transient failures with bounded backoff.
- The floating Runs button marks stale state as `connection lost` rather than falsely claiming `live`.
- The Runs drawer preserves last-known progress while reconnecting and provides an explicit Reconnect action.
- Active-run registry polling reduced from every 3 seconds to every 5 seconds to avoid duplicate pressure alongside detailed status polling.
