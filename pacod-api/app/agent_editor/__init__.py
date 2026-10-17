"""Visual agent editor — graph-shaped, versioned agent definitions.

An agent (= a ``profiles`` row) can be described by a node/edge graph built in
the superuser-only editor. A *version* of that graph is compiled into a config
**overlay** applied on top of the existing ``app_settings`` getters (runtime
config, prompts, response cache, reading config): a ``None`` config value means
"inherit", so an agent without versions behaves exactly as before.

Modules:
  registry   — node types + templates (the component library; single source of truth)
  graph      — the graph JSON schema (pydantic) + schema-version migration
  validate   — template topology / config / prompt / model checks → GraphIssue list
  compile    — graph → CompiledOverlay
  effective  — (profile, variant) → EffectiveAgent (base getters + overlay)
  importer   — existing profile settings → an equivalent graph
  service    — persistence: drafts, versions, deploy pointers, boot backfill
"""
