"""Decision and activity history across properties, from BigQuery `loop_events`.

`workspace_inbox.decision_history` reads one property. The portfolio screens
(Dashboard activity, Approvals stats, Value) need many at once, so this module
runs one parameterized query per screen instead of one per property.

Every reader returns None when BigQuery is not configured, so a caller can say
"history unknown" instead of reporting zero decisions.

Automatic decisions are `recommendation_approved` events written by
`loop_autopilot` (source "loop_autopilot"). Everything else counted here is a
person's `workspace_decision`, net of `workspace_decision_undone`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from skills import workspace_common as wc
from skills import workspace_inbox as wi

logger = logging.getLogger(__name__)

DECISION_TYPES = ("workspace_decision", "workspace_decision_undone", "recommendation_approved")
AUTO_APPROVE_MIN_DECISIONS = 20
AUTO_APPROVE_MIN_RATE = 0.90


def _client():
    import loop_writer
    client = loop_writer._bq()
    if client is None:
        return None, None
    return client, loop_writer._bq_cache.get("table_ref")


def _payload(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}


def _query(sql: str, params: list) -> list:
    from google.cloud import bigquery
    client, _ = _client()
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    return [dict(r.items()) for r in client.query(sql, job_config=cfg).result()]


def decision_events(company_ids: list, uuids: list, since: datetime) -> list | None:
    client, table = _client()
    if client is None:
        return None
    from google.cloud import bigquery
    sql = f"""
      SELECT event_type, company_id, property_uuid, occurred_at, source, `trigger`, payload
      FROM `{table}`
      WHERE event_type IN UNNEST(@types)
        AND occurred_at >= @since
        AND (company_id IN UNNEST(@cids) OR property_uuid IN UNNEST(@uuids))
      ORDER BY occurred_at
    """
    return _query(sql, [
        bigquery.ArrayQueryParameter("types", "STRING", list(DECISION_TYPES)),
        bigquery.ScalarQueryParameter("since", "TIMESTAMP", since),
        bigquery.ArrayQueryParameter("cids", "STRING", [str(c) for c in company_ids if c]),
        bigquery.ArrayQueryParameter("uuids", "STRING", [str(u) for u in uuids if u]),
    ])


def to_decisions(events: list) -> list:
    """Events → decisions `{item_id, source, action, reason, actor, at, company_id,
    uuid, title, outcome, automatic, undone}`, oldest first."""
    out: list = []
    undos: list = []
    for ev in events:
        p = _payload(ev.get("payload"))
        etype = ev.get("event_type")
        if etype == "workspace_decision_undone":
            undos.append((p.get("item_id"), str(ev.get("occurred_at") or "")))
            continue
        if etype == "recommendation_approved":
            if ev.get("source") != "loop_autopilot" and ev.get("trigger") != "autopilot":
                continue          # a portal approval is already a workspace_decision
            rec = p.get("recommendation") or {}
            title = (f"Shift ${wc.to_float(rec.get('amount')) or 0:,.0f} from {rec.get('from_channel')} "
                     f"to {rec.get('to_channel')}" if rec.get("action") == "shift_budget"
                     else "Forecast recommendation approved")
            out.append({"item_id": None, "source": "loop_rec", "action": "approve", "reason": None,
                        "actor": "loop_autopilot", "at": ev.get("occurred_at"),
                        "company_id": ev.get("company_id"), "uuid": ev.get("property_uuid"),
                        "title": title, "outcome": "ok", "automatic": True, "undone": False})
            continue
        out.append({"item_id": p.get("item_id"), "source": p.get("source"), "action": p.get("action"),
                    "reason": p.get("reason"), "actor": p.get("actor"), "at": ev.get("occurred_at"),
                    "company_id": ev.get("company_id"), "uuid": ev.get("property_uuid"),
                    "title": p.get("title"), "outcome": p.get("outcome", "ok"),
                    "automatic": False, "undone": False})
    for item_id, at in undos:
        for d in reversed(out):
            if d["item_id"] == item_id and not d["undone"] and str(d["at"] or "") <= at:
                d["undone"] = True
                break
    return out


def decisions(company_ids: list, uuids: list, since: datetime) -> list | None:
    events = decision_events(company_ids, uuids, since)
    return None if events is None else to_decisions(events)


def effective(decs: list) -> list:
    return [d for d in decs if d.get("outcome", "ok") in ("ok", "partial") and not d.get("undone")]


def auto_approve_candidates(decs: list, *, min_n: int = AUTO_APPROVE_MIN_DECISIONS,
                            min_rate: float = AUTO_APPROVE_MIN_RATE) -> list:
    """Kinds of item a person has decided at least `min_n` times, approving at
    least `min_rate` of them unedited. (The workspace has no edit-before-approve,
    so every approval is unedited.)"""
    by_source: dict = {}
    for d in effective(decs):
        if d.get("automatic") or not d.get("source"):
            continue
        by_source.setdefault(d["source"], []).append(d)
    out = []
    for source, ds in sorted(by_source.items()):
        approvals = sum(1 for d in ds if d.get("action") == "approve")
        if len(ds) >= min_n and approvals / len(ds) >= min_rate:
            label = wi.SOURCE_LABELS.get(source, source)
            out.append(label[:1].upper() + label[1:])
    return out


def recent_events(uuids: list, *, days: int = 30, limit: int = 50) -> list | None:
    client, table = _client()
    if client is None:
        return None
    from google.cloud import bigquery
    sql = f"""
      SELECT event_type, stage, property_uuid, company_id, occurred_at, source, payload
      FROM `{table}`
      WHERE property_uuid IN UNNEST(@uuids)
        AND occurred_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        AND event_type != 'llm_call_completed'
      ORDER BY occurred_at DESC
      LIMIT @lim
    """
    rows = _query(sql, [
        bigquery.ArrayQueryParameter("uuids", "STRING", [str(u) for u in uuids if u]),
        bigquery.ScalarQueryParameter("days", "INT64", int(days)),
        bigquery.ScalarQueryParameter("lim", "INT64", int(limit)),
    ])
    for r in rows:
        r["payload"] = _payload(r.get("payload"))
    return rows


AUTOMATIC_ACTION_TYPES = ("recommendation_approved", "workspace_fair_housing_review")


def automatic_actions(company_ids: list, uuids: list, since: datetime) -> dict | None:
    """Changes shipped without anyone's approval, counted from loop_events.

    Counted: `loop_autopilot` approvals (`recommendation_approved` with source
    loop_autopilot), and monthly Fair Housing reviews that found nothing
    (`workspace_fair_housing_review` with zero findings). Returns
    `{total, autopilot_approvals, fair_housing_reviews_clean, events}` or None
    without BigQuery.
    """
    client, table = _client()
    if client is None:
        return None
    from google.cloud import bigquery
    rows = _query(f"""
      SELECT event_type, company_id, property_uuid, occurred_at, source, `trigger`, payload
      FROM `{table}`
      WHERE event_type IN UNNEST(@types)
        AND occurred_at >= @since
        AND (company_id IN UNNEST(@cids) OR property_uuid IN UNNEST(@uuids))
      ORDER BY occurred_at DESC
    """, [
        bigquery.ArrayQueryParameter("types", "STRING", list(AUTOMATIC_ACTION_TYPES)),
        bigquery.ScalarQueryParameter("since", "TIMESTAMP", since),
        bigquery.ArrayQueryParameter("cids", "STRING", [str(c) for c in company_ids if c]),
        bigquery.ArrayQueryParameter("uuids", "STRING", [str(u) for u in uuids if u]),
    ])
    return count_automatic(rows)


def count_automatic(rows: list) -> dict:
    autopilot, clean, events = 0, 0, []
    for ev in rows:
        p = _payload(ev.get("payload"))
        if ev.get("event_type") == "recommendation_approved":
            if ev.get("source") == "loop_autopilot" or ev.get("trigger") == "autopilot":
                autopilot += 1
                events.append(ev)
        elif ev.get("event_type") == "workspace_fair_housing_review":
            if not (p.get("findings") or []) and not p.get("findings_count"):
                clean += 1
                events.append(ev)
    return {"total": autopilot + clean, "autopilot_approvals": autopilot,
            "fair_housing_reviews_clean": clean, "events": events}
