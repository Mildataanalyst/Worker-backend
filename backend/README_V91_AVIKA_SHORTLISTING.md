# DFP 2.0 Core Backend v91 — Avika and Shortlisting Integration

Complete Railway-ready FastAPI repository. It preserves historical work and adds the data flow required by Frontend v162.

## Added

- Avika metadata and source-batch provenance in Lead Pool imports.
- Automatic reviewer summary and shortlisting comment generation.
- Bulk curation states: pending, approved, follow-up and hold.
- Explicit selected-lead dispatch to PMs without a separate password prompt.
- Existing-assignment and existing-rating dedupe.
- Permanent NGO IDs throughout Lead Pool and PM tasks.

Retain the existing Railway volume mounted at `/data`.
