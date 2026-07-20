DFP 2.0 Frontend — V114 Preview Reliability Fix

Fixes
- The landing-page Preview now opens the cinematic film directly as a full-screen overlay instead of depending on a route transition.
- /how-it-works still works as a standalone route using the same shared film component.
- Added an explicit preview visibility/background lock so older light-theme rules cannot turn the film into a blank white screen.
- Added explicit contrast protection to the floating workflow Preview modal.
- Escape closes the embedded landing-page preview; Back closes it as well.
- The cinematic rating reviewer remains Rachit.

Implementation
- Added components/HowItWorksFilm.tsx as the shared v88 film component.
- Reworked app/how-it-works/page.tsx into a thin route wrapper.
- Updated app/page.tsx so Preview opens the shared film in-place.
- Added final reliability overrides at the end of app/globals.css.

Build
- Next.js production build passed.
- Existing non-blocking font and React Hook warnings remain unchanged.
