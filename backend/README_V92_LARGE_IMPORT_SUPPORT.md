# Core Backend v92 — large import support

This complete core-backend repository retains the v91 NGO ID, Avika provenance, grouped Shortlisting Pool and PM-dispatch APIs.

Change in v92:

- the old 8,000,000-byte CSV ceiling is replaced with a 100 MB safety cap for large Avika and Lead Pool imports;
- no Karnataka Recovery run state is stored here—the worker remains the source of truth for search checkpoints and automatic resume.
