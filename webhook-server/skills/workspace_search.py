"""Workspace search — `GET /api/workspace/search?q=&company_id=`.

Scoped to what the caller may open, decided here and not by the page:

* properties — internal callers search every managed property's name (the
  cached portfolio list) and, for a numeric query, resolve it as a company id or
  uuid through the Property Resolver. Clients search only the companies
  `feature_access.companies_for` grants them.
* work items and reports — read per property, so they are searched within
  `company_id` when given, or across a client's own properties (at most five).
  An internal caller without `company_id` gets properties and questions only,
  and a gap says why.
* questions — the Ask preset questions (skills/question_registry).

Ranking: exact title match, then prefix, then word prefix, then substring; ties
by type (property, work item, report, question) and title. At most 20 results.
"""

from __future__ import annotations

import logging

from skills import workspace_common as wc
from skills import workspace_inbox as wi

logger = logging.getLogger(__name__)

MAX_RESULTS = 20
MAX_CLIENT_PROPERTIES = 5
TYPE_ORDER = {"property": 0, "work_item": 1, "report": 2, "question": 3}


def score(query: str, text: str) -> int:
    q, t = query.lower().strip(), (text or "").lower()
    if not q or not t:
        return 0
    if t == q:
        return 100
    if t.startswith(q):
        return 80
    if any(w.startswith(q) for w in t.replace("·", " ").split()):
        return 60
    if q in t:
        return 40
    return 0


def _result(kind: str, rid: str, title: str, subtitle: str | None, company_id: str | None,
            href: str, s: int) -> dict:
    return {"type": kind, "id": rid, "title": title, "subtitle": subtitle,
            "company_id": company_id, "href": href, "_score": s}


def _property_results(q: str, email: str, internal: bool, gaps: list) -> list:
    out = []
    if internal:
        from skills import workspace_portfolio
        try:
            rows = workspace_portfolio.managed_properties()
        except Exception as exc:  # noqa: BLE001
            gaps.append(wc.gap(None, f"Property list unavailable ({type(exc).__name__})", source="hubspot"))
            rows = []
        for p in rows:
            s = score(q, p.get("name") or "")
            if s:
                cid = str(p.get("hubspot_company_id") or "")
                out.append(_result("property", cid, p.get("name"), ", ".join(
                    x for x in (p.get("city"), p.get("state")) if x) or None, cid,
                    f"#/property?company_id={cid}", s))
        if q.strip().isdigit() and not out:
            try:
                from skills import property_resolver
                ident = property_resolver.resolve(q.strip())
                out.append(_result("property", ident.company_id, ident.name or ident.company_id,
                                   ident.market, ident.company_id,
                                   f"#/property?company_id={ident.company_id}", 90))
            except Exception:  # noqa: BLE001 — no match is not an error here
                pass
    else:
        import hubspot_client
        from feature_access import companies_for
        for cid in sorted(companies_for(email)):
            try:
                props = hubspot_client.get_company(cid, ["name", "city", "state"])
            except Exception:  # noqa: BLE001
                continue
            s = score(q, props.get("name") or "")
            if s:
                out.append(_result("property", cid, props.get("name"), ", ".join(
                    x for x in (props.get("city"), props.get("state")) if x) or None, cid,
                    f"#/property?company_id={cid}", s))
    return out


def _property_scoped_results(q: str, company_ids: list, internal: bool, gaps: list) -> list:
    out = []
    for cid in company_ids:
        try:
            ctx = wi.load_context(cid)
        except Exception as exc:  # noqa: BLE001
            gaps.append(wc.gap(None, f"Property {cid} could not be read ({type(exc).__name__})"))
            continue
        items, item_gaps = wi.collect(ctx)
        for g in item_gaps:
            if g.get("source") and not g.get("_internal"):
                gaps.append(g)
        for item in items:
            view = wi.view_item(item, internal)
            s = score(q, view["title"])
            if s:
                out.append(_result("work_item", view["id"], view["title"],
                                   f"{ctx.name} · {view['status'].replace('_', ' ')}", cid,
                                   f"#/item/{view['id']}?company_id={cid}", s))
        run = wc.to_date(ctx.props.get("red_light_run_date"))
        if run:
            title = f"Red Light report · {run:%B %Y}"
            s = max(score(q, title), score(q, "report"))
            if s:
                out.append(_result("report", f"red_light:{cid}:{run:%Y-%m}", title, ctx.name, cid,
                                   f"#/reports?company_id={cid}&month={run:%Y-%m}", s))
    return out


def _question_results(q: str, company_id: str | None) -> list:
    from skills import question_registry
    out = []
    for question in question_registry.ordered():
        s = max(score(q, question.label), score(q, question.blurb) // 2)
        if s:
            suffix = f"?company_id={company_id}" if company_id else ""
            out.append(_result("question", question.key, question.label, question.blurb, company_id,
                               f"#/ask/{question.key}{suffix}", s))
    return out


def search(email: str, q: str, *, internal: bool, company_id: str | None = None) -> dict:
    from feature_access import companies_for

    q = (q or "").strip()
    if len(q) < 2:
        raise wc.WorkspaceError(400, "q must be at least 2 characters")
    gaps: list = []
    results = _property_results(q, email, internal, gaps)

    if company_id:
        scoped = [company_id]
    elif internal:
        scoped = []
        gaps.append(wc.gap(None, "Work items and reports are searched within one property; "
                                 "pass company_id to include them."))
    else:
        scoped = sorted(companies_for(email))[:MAX_CLIENT_PROPERTIES]
    results += _property_scoped_results(q, scoped, internal, gaps)
    results += _question_results(q, company_id)

    results.sort(key=lambda r: (-r["_score"], TYPE_ORDER[r["type"]], (r["title"] or "").lower()))
    return {"results": wc.public(results[:MAX_RESULTS]), "gaps": wc.gaps_for(gaps, internal)}
