"""Fiscus continuous-improvement loop — standalone daily triage engine.

A separate, standalone-runnable service (sibling of ingestion_api/mcp_server/
admin_api). It reads the last day's conversations + feedback + retrieval traces,
triages them, analyses the substantive ones with two diverse LLMs, reconciles the
analyses with a judge, routes each finding to the right remediation channel, and
checks coverage gaps against the AUTHORITATIVE KB tier only (the model-collapse
firewall).

Phase 1 is read-only: it records findings and a report but writes nothing to the
KB. It reuses `app.core.*` / `app.services.*` as a library and talks to Postgres /
Qdrant / the LLM gateway / the embeddings service over the network — it imports nothing
from the FastAPI app object, so `python -m improvement_loop run` works with the
`api` container stopped.
"""
