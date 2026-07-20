# v120 — Search client build fix

Fixes the Next.js type error:

`Module '@/lib/backendClient' has no exported member 'safeSearchJSON'.`

Changes:
- Adds `safeSearchJSON()` for the search-worker service.
- Adds `safeStoryJSON()` for the story/AI service.
- Keeps `safeJSON()` dedicated to the core backend.
- Supports both absolute URLs and service-relative paths.
- Preserves the shared mutation-token header behavior.

No backend or worker deployment is required for this frontend-only fix.
