# Frontend v130 — Resume Old Website Search Dropdown

The Advanced Website Recovery card now contains a persistent old-run selector.

The dropdown shows:

- Last-saved date and time
- Serper or Firecrawl strategy
- Processed / total NGOs
- Saved status
- Original input filename, when available

Actions:

- **Show saved outputs** — opens the selected run's progress and partial downloads without restarting it.
- **Resume selected search** — continues from the next unprocessed NGO.
- **Refresh list** — reloads resumable runs from backend storage.

Eligible states include paused, stopped, cancelled, failed, and restart-interrupted.
