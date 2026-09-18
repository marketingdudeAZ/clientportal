# MCP endpoint — portal context for an external agent platform

`POST /mcp` on the existing Flask service. Read-only. Off until a token is set.

## What it exposes

| Tool | Returns |
|---|---|
| `find_property` | identity, plus which data sources exist for that property |
| `get_property_brief` | the community brief, override-wins, internal fields stripped |
| `get_availability` | occupancy, leased percent, exposure, available units |
| `get_leasing_funnel` | lead → tour → application → lease by channel, one month |
| `get_spend_authorization` | authorized monthly spend by service, from the signed deal |
| `get_open_work` | open tickets, newest change first; `changed_within_days` narrows to recent movement |
| `get_active_deals` | deals for the property: stage, pipeline, amount, created and close dates |
| `get_market_comps` | market narrative, plus comp-set rents when a comp set id is supplied |
| `get_attribution` | first-touch leads/tours/applications/leases by source, median days to lease |
| `get_metric_rules` | the counting rules to apply before reporting any number |
| `get_compliance_rules` | housing advertising constraints (Special Ad Category) |

Eleven tools. Tools live in `webhook-server/skills/mcp_context.py` (Layer 2). The protocol
lives in `webhook-server/routes/mcp.py`. A missing source returns `null` plus a
`gaps` entry naming the source and the reason — never a zero, never an estimate.

## Turn it on

One env var on the existing Render service:

```
MCP_BEARER_TOKEN=<long random token>
```

Generate one: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`

Per-consumer tokens instead, so one can be rotated without cutting off the rest:

```
MCP_TOKENS=vendor:<token-a>,internal:<token-b>
```

`MCP_ENABLED=false` force-disables it even with a token set. With no token the
endpoint 404s — an unconfigured door is a closed door.

## Connect the agent platform

In its **Add MCP server** dialog:

- **Name:** `RPM Portal Context`
- **URL:** `https://<portal-host>/mcp`
- **Authentication:** Bearer Token → the token above

The client calls `tools/list` on connect. Each tool's description carries the
rules that apply to it, so the agent inherits them without extra prompting.

Verify:

```bash
curl -s https://<portal-host>/mcp/health          # no token needed
curl -s -X POST https://<portal-host>/mcp \
  -H 'content-type: application/json' \
  -H 'authorization: Bearer <token>' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -c 400
```

## Why it is read-only

Live changes stay on the portal's approval flow, where the Fair Housing check
and the signed-deal spend authorization run. Because nothing here writes, R1
(code never writes `uuid`) cannot be violated through this path, and a single
shared machine token is an acceptable credential.

## Operating notes

- Every call logs the token label, the method, the tool and its duration. Watch
  for `mcp auth rejected` — a stale token, or someone probing.
- Rotate by adding a second entry to `MCP_TOKENS`, switching the platform over,
  then removing the old one.
- Known limits to state plainly rather than paper over: multi-touch and
  influenced attribution are not connected (the object sits outside the metric
  library allowlist), deal stage-change history is not exposed, per-field ticket
  history is not exposed, and comp-set rents need a comp set id that is not yet
  stored on the property record.
- `get_spend_authorization` builds the portfolio spend sheet on a cold call
  (~750 rows), `get_active_deals` reads HubSpot associations, and
  `get_leasing_funnel` / `get_attribution` query BigQuery. Both inherit the
  portal's caches, but agents fan out: if you widen past a pilot, add a
  per-token quota before HubSpot starts returning 429s.
- Transport: replies are SSE when the client's `Accept` includes
  `text/event-stream`, otherwise JSON. Both are valid; clients accept either.
  Batched JSON-RPC is refused with a clear message.

---

# The return path: posting a finding

`/mcp` lets an agent READ the portal. This is the other direction — an agent
posts what it found and it becomes a card in the portal's Approvals queue,
beside the rules the portal runs itself. Same token table, same kill switch:
`MCP_ENABLED=false` closes both, and with no token configured both 404.

    POST /api/agent/findings      post one finding, or up to 25 in a batch
    GET  /api/agent/findings       read back what is live for a property

## Posting one

```bash
curl -sS https://<render-host>/api/agent/findings \
  -H 'authorization: Bearer <token>' \
  -H 'content-type: application/json' \
  -H 'idempotency-key: nightly-2026-09-18-atwood-budget' \
  -d '{
    "property": "30912193455",
    "rule_key": "search_budget_capped",
    "category": "cost",
    "severity": "high",
    "confidence": 8,
    "channels": ["paid_search"],
    "found": "Search lost 41% of impressions to budget over the last 30 days.",
    "expect": "Recovering that share should add roughly 120 clicks a month.",
    "if_skip": "The campaign keeps stopping before midday.",
    "receipts": [{"label": "Impression share lost to budget", "value": "41%",
                  "source": "Google Ads", "as_of": "2026-09-17"}],
    "action": {"kind": "budget_change", "params": {"daily_budget": 95}}
  }'
```

`property` accepts a company id, a uuid, a domain or a property name — the same
set a person can type. Use the company id from the MCP tools when you have it.

A batch posts `{"property": "...", "findings": [ {...}, {...} ]}`; a finding may
carry its own `property` to override.

## Status codes, and what to do about each

| Code | Meaning | What the agent should do |
|---|---|---|
| 200 | accepted, or a batch with per-item outcomes | nothing; it is queued |
| 422 | the finding is wrong as posted | fix it; re-posting it unchanged will fail again |
| 503 | the portal could not accept it | retry; the finding was never judged |
| 401 | token missing or wrong | stop; rotate |
| 404 | the feature is off, or the property is unknown (GET) | stop |
| 413 | more than 25 findings in one request | split the batch |

The 422/503 split is load-bearing. A rejection means the content is wrong and
the agent should stop; a 503 means our Fair Housing checker or warehouse was
unreachable and the finding was never judged. In a batch these are counted
separately (`rejected` vs `unavailable`, plus `retry_unavailable`) so retrying
does not re-post the genuinely bad ones forever.

## What is refused, and why

- **An action with no receipts.** A card asking someone to change live spend
  without showing the numbers is worse than no card.
- **Copy that fails the Fair Housing check**, which runs BEFORE storage, not at
  publish time. Housing is a Special Ad Category. Protected-class vocabulary on
  its own is kept but flagged for a person; a real violation is refused.
- **Anything steering by audience or geography** — radius, ZIP, lookalike,
  retargeting, demographics. Refused whoever produced it.
- **A duplicate.** The property, the rule and the finding text are what make two
  posts the same thing — deliberately not the numbers, so a value drifting a
  dollar overnight does not create a second card. Send an `Idempotency-Key`
  header (or `idempotency_key`) to control it yourself.

## What the portal will not let an agent decide

`requires_signed_deal` is set by the portal, not the caller: a budget change or
a new ad group always needs a signature, and a posted finding claiming otherwise
is overridden. Nothing here executes. The finding is stored as a loop event and
waits for a human in the portal; only an approval fires a webhook back out.

## Readback

```bash
curl -sS 'https://<render-host>/api/agent/findings?property=30912193455' \
  -H 'authorization: Bearer <token>'
```

Returns only findings posted by the calling token, in the shape the queue shows
them. A warehouse that cannot be read comes back as a `gaps` entry, never as an
empty list — so "nothing posted" and "we could not look" stay distinguishable.

Posted findings also rank in the portal's own queue: `agent_findings` is a
producer in `skills/reco_engine.py`, so an agent's card competes on the same
severity, confidence and rent-exposure scale as every internal rule rather than
jumping the line.
