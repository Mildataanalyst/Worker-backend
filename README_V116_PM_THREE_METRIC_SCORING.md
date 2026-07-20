# Frontend v116 — PM Three-Metric Scoring

## Changes
- Removed the floating bottom-right Preview control from Rankings and NGO Discovery.
- Preserved the existing overall 1–5 ranking and overall reason.
- Added three separate 1–5 metric sliders for PM shortlisting:
  - Child Progression & Alumni Outcomes
  - Learning Model
  - Development Ecosystem
- Added a required reason for every metric with a 100-character minimum.
- Added explicit Re-rank actions for previously submitted NGOs.
- Added an embedded scoring reference modal with the common scale and benchmark table.
- Added admin-configurable evidence and labelled source links for every metric on every assigned NGO.
- Added optional admin configuration for a full scoring-reference document URL.

## Persistence
The frontend sends `metric_scores` with the existing `/workstream/submit` payload and sends task-level `metric_evidence` through `/workstream/admin/update`.
