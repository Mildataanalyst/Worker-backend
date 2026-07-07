# Backend v52 — NGO Presence Check: identity-only scoring

This update tightens the NGO Presence Check so it does **not** score child focus, programme fit, education relevance, or DFP fit.

The scoring now only answers two questions:

1. Did we find the correct official NGO website with enough identity evidence?
2. How strong is the NGO's digital presence?

Digital presence considers official-site quality plus capped signals from social profiles, directories, articles/documents, and other third-party footprint. Third-party channels cannot override weak identity evidence for the official website.
