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
