# V68 — Smart Recovery hard deadlines

This release prevents one slow or trickling website from freezing an entire Smart Recovery run.

- Each streamed HTML/PDF fetch has an environment-configurable wall-clock deadline (`SMART_RECHECK_HARD_FETCH_DEADLINE_SEC`, default 20 seconds), in addition to the existing socket-inactivity timeout.
- Each NGO has a total processing watchdog (`SMART_RECHECK_MAX_ROW_SECONDS`, default 120 seconds), covering retries, URL variants, candidate verification and subpage checks.
- An NGO that exceeds the row watchdog is checkpointed as `row_timeout`, included in the retry queue, and processing continues with the next NGO.
- Status responses expose the current NGO elapsed time, watchdog time remaining and cumulative timeout count.
- Regression tests cover a slow-trickle response and continuation after a row timeout.
