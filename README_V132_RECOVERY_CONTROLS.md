# Frontend v132 — Smart Recovery run controls + run-deletion UI

Builds on v131 (run deletion). Adds full run management to the Smart Recovery
rerun panel on app/ngo-discovery/page.tsx:

- Pause  -> POST /repository/recheck/pause/{run_id}   (shown when can_pause)
- Resume -> POST /repository/recheck/resume/{run_id}  (shown when can_resume)
- End run-> POST /repository/recheck/stop/{run_id}     (shown when can_stop; confirms)
- Partial downloads gated on status.downloads (Results/Audit/Summary/Skipped/Remaining)
- Send to Lead Pool once partial output exists
- Live counters: Serper queries used + Firecrawl credits used + ETA, read from
  status fields queries_used / firecrawl_credits(_used) / eta_at
- Polling keeps running while paused; stops on completed/stopped/cancelled/error

History of Smart Recovery runs already appears in the Bulk/Recovery archive list
(module 'no_website_recheck', titled 'Recovery rerun'), each with its own
downloads, Send to Lead Pool, and Delete button.

Also retains v131: per-run Delete button + disk-usage badge.
CSS added to app/globals.css.
