# v135 — Smart Recovery background continuity and elapsed time

- Shows total active processing time, current-NGO time, progress %, throughput, and estimated remaining duration.
- Persists the last recovery run ID in browser storage.
- Reconnects the Smart Recovery status panel after route changes, refreshes, and browser reopen.
- Falls back to the worker's active-run registry when no saved browser run ID is available.
- Clarifies in the UI that shortlisting/navigation does not stop the worker job.

Smart Recovery remains a worker-side background thread. Do not redeploy/restart the worker or delete the active run directory while it is running.
