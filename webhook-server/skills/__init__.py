"""Layer 2 — shared skills.

The layer ADR 0008 committed to and never built. Applications (routes, agents)
call these; these call Layer 1 connectors (`hubspot_client`, `bigquery_client`,
`clickup_client`, …). Routes must not reach past this layer into a connector.

Why this exists at all: identity resolution was inlined in ~11 places, every
Claude call constructed its own SDK client, and the data-quality rules that the
Atwood/Henry investigations needed lived in a scratch directory. Each of those
is a shared concern wearing a local disguise.

Modules
-------
property_resolver   one identity lookup, one cache
llm_gateway         one Anthropic client (CLAUDE.md rule 2)
data_quality        plausibility, dedup, outlier quarantine
"""
