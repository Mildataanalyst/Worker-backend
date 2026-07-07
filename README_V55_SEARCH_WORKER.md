# DFP2 Backend v55 Search Worker

Deploy this as a separate stateless worker. It serves Repository Builder, Smart Recovery / Website Re-check, and NGO Presence Check. It blocks PM/workspace/ranking endpoints.

Set `DFP2_SERVICE_ROLE=search`. No persistent disk is required; exports persist while service filesystem remains available.
