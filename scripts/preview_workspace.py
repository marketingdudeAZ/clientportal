#!/usr/bin/env python3
"""Preview the workspace page locally against fixture JSON.

    python3 scripts/preview_workspace.py [--port 5057]
    open "http://127.0.0.1:5057/workspace?t=preview"

The page is served from webhook-server/portal_pages/workspace.html. The API paths
it calls (/api/workspace/*, /api/ask/*) answer from a 35-property preview book
(tests/fixtures/workspace/preview_book.json): every property's screens, items,
drafts and report links are built from that property's own facts.
`?t=preview` puts the page in signed-link mode so it skips Clerk; this server
ignores the token. Nothing here imports or touches the production server.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "webhook-server" / "portal_pages" / "workspace.html"
REPORT_PAGE = REPO / "webhook-server" / "portal_pages" / "workspace_report.html"
FIXTURES = REPO / "tests" / "fixtures" / "workspace"
PORT = 5057

app = Flask(__name__)

# Demo switches for screenshots, flipped at /__preview/<name>?on=1|0.
SWITCHES = {"approvals_empty": False, "cannot_decide": False}


@app.get("/__preview/<name>")
def preview_switch(name: str):
    if name not in SWITCHES:
        abort(404)
    SWITCHES[name] = request.args.get("on", "1") in ("1", "true", "yes")
    return jsonify(SWITCHES)


def _fixture(name: str):
    with open(FIXTURES / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)

# ── Preview book: every property's screens built from its own facts ──────────
#
# The preview used to serve one property's detail data for every property with
# only the name swapped, so drafts, item ids and content pointed at the wrong
# property. Everything below is generated per property from preview_book.json:
# each item, draft, email, content row, asset and report link names the
# property it belongs to. tests/test_workspace_preview_book.py walks all of it.

import hashlib

BOOK_PATH = FIXTURES / "preview_book.json"
AS_OF = "2026-09-14T13:04:00Z"
SEP = ["2026-09", "2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04", "2027-05", "2027-06", "2027-07", "2027-08"]
ENGINES = ["ChatGPT", "Claude", "Perplexity", "Google AI", "Copilot"]
PHOTO_SHOOT_WORDS = ("photo shoot", "photoshoot", "reshoot", "new photography", "schedule a shoot")


def _h(key: str, lo: int, hi: int) -> int:
    n = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    return lo + n % (hi - lo + 1)


def _metric(value, source, as_of=AS_OF):
    return None if value is None else {"value": value, "source": source, "as_of": as_of}


def book() -> dict:
    with open(BOOK_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def props() -> list:
    return book()["properties"]


def prop(company_id: str | None) -> dict | None:
    for p in props():
        if p["company_id"] == company_id:
            return p
    return None


def _occ(p):
    return None if p["occupied"] is None else round(p["occupied"] / p["units"], 3)


def _cpl(p):
    return None if not p["leases_last_month"] else round(p["spend_last_month"] / p["leases_last_month"])


def _trail(at, actor, text, visibility="client"):
    return {"at": at, "actor": actor, "text": text, "visibility": visibility}


# ── items per scenario ───────────────────────────────────────────────────────

def _base_item(p, source, source_id, **kw):
    item = {
        "id": f"{source}:{source_id}", "source": source, "source_id": source_id, "company_id": p["company_id"],
        "title": "", "found": None, "expect": None, "if_skip": None, "receipts": [], "evidence": None, "sparkline": None,
        "channels": [], "lens": "amplify", "start_by": None, "due": None, "status": "to_do", "needs_approval": True,
        "client_visible": True, "owner": None, "comments_count": 0, "cost_note": None, "steps": [], "trail": [],
        "notes": [], "actions": {"approve": True, "not_now": True}, "fair_housing_review": None,
        "why": None, "for_whom": None, "approving_does": [], "category": None, "draft": None, "review": None,
    }
    item.update(kw)
    return item


def _items_for(p: dict) -> list:
    n, cid, tail = p["name"], p["company_id"], p["company_id"][-4:]
    occ = _occ(p)
    out = []
    for scen in p["scenarios"]:
        if scen == "ils_stepdown":
            premium, standard = 3900 + _h(cid + "prem", 0, 8) * 50, 1700 + _h(cid + "std", 0, 6) * 50
            leases6 = _h(cid + "l6", 1, 3)
            save = (premium - standard) * 12
            out.append(_base_item(
                p, "hubdb_rec", f"{tail}1", category="cost", lens="amplify", channels=["ils"], owner="Marcus J.",
                title=f"Step down Apartments.com Premium to Standard at {n}",
                found=f"{n} pays ${premium:,} a month for Apartments.com Premium. Hyly attributes {leases6} leases to it in the last six months.",
                expect=f"Standard costs ${standard:,} a month. We expect lead volume at {n} to hold within 10%, and we check again after 60 days.",
                if_skip=f"Premium renews on Oct 1 at ${premium:,} a month. Nothing else changes at {n}.",
                receipts=[{"label": f"Premium package, ${premium:,}/mo", "source": "hubspot_line_items", "as_of": "2026-09-14T12:30:00Z"},
                          {"label": f"{leases6} leases attributed in 6 months", "source": "hyly", "as_of": "2026-09-14T11:00:00Z"}],
                why={"text": f"{n} is paying for Premium placement that isn't producing leases.",
                     "receipts": [{"label": f"Premium package, ${premium:,}/mo", "source": "hubspot_line_items", "as_of": "2026-09-14T12:30:00Z"},
                                  {"label": f"{leases6} leases attributed in 6 months", "source": "hyly", "as_of": "2026-09-14T11:00:00Z"}]},
                for_whom={"text": f"{n}'s leasing goal: {p['units_to_lease_90d']} units to lease in the next 90 days at {int(round((occ or 0) * 100))}% occupancy today.", "questions": []},
                approving_does=[{"label": f"We email the Apartments.com rep to move {n} to Standard at renewal", "owner": "RPM Digital", "when": "Within 2 business days"},
                                {"label": "The rep confirms the new package and rate", "owner": "vendor", "when": "Before Oct 1"},
                                {"label": "You sign the revised line item; the budget changes only after signature", "owner": "you", "when": "When the rep confirms"}],
                evidence={"columns": ["", "Running now", "Proposed"], "rows": [["Monthly cost", f"${premium:,}", f"${standard:,}"], ["Package", "Premium", "Standard"], ["Leases, last 6 months", str(leases6), "—"]], "more_count": 0},
                cost_note=f"Saves ${save:,} a year", due="2026-10-01",
                trail=[_trail("2026-09-14T08:12:00Z", "vendor audit", f"Opened for {n}"),
                       _trail("2026-09-14T09:40:00Z", "Marcus J.", f"Checked the {n} contract end date: Oct 1", "internal")],
                notes=[{"at": "2026-09-14T09:41:00Z", "actor": "Marcus J.", "text": f"Copy the regional manager on the {n} email.", "visibility": "internal"}],
                _savings=save,
            ))
        elif scen == "vendor_add":
            out.append(_base_item(
                p, "hubdb_rec", f"{tail}2", category="vendor", lens="amplify", channels=["ils"], owner="Dana R.",
                title=f"Add Zillow Promoted at {n}",
                found=f"{n} has {p['units_to_lease_90d']} units to lease in 90 days and no Zillow listing upgrade.",
                expect=f"Zillow Promoted at $480 a month for {n}. We measure leads from it for 60 days before renewing.",
                if_skip=f"{n}'s listings stay on Apartments.com and the property website only.",
                receipts=[{"label": f"{p['units_to_lease_90d']} units to lease, 90 days", "source": "aptiq", "as_of": AS_OF}],
                why={"text": f"{n} has more units opening than its current listings reach.", "receipts": [{"label": f"{p['units_to_lease_90d']} units to lease, 90 days", "source": "aptiq", "as_of": AS_OF}]},
                for_whom={"text": f"{n}'s leasing goal: lift occupancy from {int(round((occ or 0) * 100))}% toward 93% before winter.", "questions": []},
                approving_does=[{"label": f"We draft the Zillow order for {n} at $480 a month", "owner": "RPM Digital", "when": "Within 2 business days"},
                                {"label": "You sign the order; nothing is bought before signature", "owner": "you", "when": "When the draft arrives"},
                                {"label": "Zillow turns on Promoted placement", "owner": "vendor", "when": "Within 5 business days of signature"}],
                cost_note="Adds $480 a month — drafted for signature",
                trail=[_trail("2026-09-14T08:15:00Z", "vendor audit", f"Opened for {n}")],
            ))
        elif scen == "rate_email":
            rate = 2900 + _h(cid + "gold", 0, 6) * 50
            out.append(_base_item(
                p, "call_prep", f"{tail}3", category="vendor", lens="amplify", channels=["ils"], owner="Marcus J.",
                title=f"Send the Apartments.com rate email for {n}",
                found=f"{n} pays ${rate:,} a month for Apartments.com Gold. Comparable RPM properties in {p['city']} pay about $500 less for the same package.",
                expect=f"A $500 a month reduction at {n}'s October renewal.",
                if_skip=f"{n}'s package renews at ${rate:,} a month.",
                receipts=[{"label": f"Gold package, ${rate:,}/mo", "source": "hubspot_line_items", "as_of": "2026-09-14T12:30:00Z"}],
                why={"text": f"{n} pays more than comparable RPM properties in {p['city']} for the same Apartments.com package.", "receipts": [{"label": f"Gold package, ${rate:,}/mo", "source": "hubspot_line_items", "as_of": "2026-09-14T12:30:00Z"}]},
                for_whom={"text": f"{n}'s leasing goal: keep the same listing reach for less.", "questions": []},
                approving_does=[{"label": f"RPM Digital sends the drafted email about {n} from your account manager's address", "owner": "RPM Digital", "when": "Within 1 business day"},
                                {"label": "The rep replies with a revised rate", "owner": "vendor", "when": "Before the October renewal"},
                                {"label": "You approve the new rate before anything is signed", "owner": "you", "when": "When the rep replies"}],
                draft={"kind": "email", "title": f"{n} — Apartments.com package review",
                       "body": f"Hi [Rep],\n\nWe've been reviewing ILS performance across our portfolio and want to talk about the {n} package before it renews in October. {n} is on Gold at ${rate:,} a month; comparable RPM properties in {p['city']} pay about $500 less for the same placement. Could we set up 20 minutes this week to review {n}'s rate?\n\nThanks,\nMarcus"},
                cost_note="Est. $6,000 a year", due="2026-10-15",
                trail=[_trail("2026-09-14T12:46:00Z", "vendor audit", f"Drafted the email for {n}")],
                _savings=6000,
            ))
        elif scen == "content_faq":
            out.append(_base_item(
                p, "content_brief", f"{tail}4", category="content", lens="tailor", channels=["website", "seo"], owner="Dana R.",
                title=f"FAQ page: pet policy and breed restrictions at {n}",
                found=f"Perplexity named {n} in 0 of 4 questions about pet policy. The answers cited other {p['city']} properties.",
                expect=f"{n} cited in Perplexity pet-policy answers within 30 days of publishing.",
                if_skip=f"Pet-policy questions about {p['city']} apartments keep going to other properties' pages.",
                receipts=[{"label": "0 of 4 Perplexity questions", "source": "geo_brand_mentions", "as_of": "2026-09-14T08:14:00Z"}],
                why={"text": f"Renters ask AI engines about pets before they tour, and nothing on {n}'s site answers it.", "receipts": [{"label": "0 of 4 Perplexity questions", "source": "geo_brand_mentions", "as_of": "2026-09-14T08:14:00Z"}]},
                for_whom={"text": f"Renters with pets comparing apartments in {p['city']}.",
                          "questions": [f"Does {n} allow large dogs?", f"pet friendly apartments {p['city'].lower()} {p['state'].lower()}",
                                        f"{n.lower()} pet rent and deposit", f"breed restrictions apartments {p['city'].lower()}"]},
                approving_does=[{"label": f"RPM Digital publishes the FAQ to {n}'s site", "owner": "RPM Digital", "when": "Within 5 business days"},
                                {"label": f"We re-check the AI answers about {n}", "owner": "RPM Digital", "when": "30 days after publishing"}],
                draft={"kind": "faq", "title": f"Pets at {n}",
                       "body": f"Can I bring my pet to {n}?\nYes. {n} welcomes up to two pets per home.\n\nIs there a weight limit?\nThere's no weight limit for dogs at {n}. Some breeds are restricted under our insurance policy; the leasing team can confirm before you apply.\n\nWhat does it cost?\nA one-time pet fee and monthly pet rent apply per pet. Assistance animals are not pets and have no fees.\n\nIs there a dog park?\n{n} has an on-site pet area. Ask the leasing team for current hours."},
                trail=[_trail("2026-09-14T08:14:00Z", "visibility audit", f"Drafted for {n}")],
            ))
        elif scen == "creative_build":
            out.append(_base_item(
                p, "video_variant", f"{tail}5", category="creative", lens="express", channels=["creative", "paid_search"], owner="Creative Services",
                title=f"Build new pool and clubhouse ad creative for {n} from existing assets",
                found=f"{n}'s two running ad creatives are 140 days old and click-through fell from 3.1% to 1.4%. The asset library already has 18 usable pool and clubhouse photos and one tour video.",
                expect=f"Fresh ads for {n} built from those assets: three new crops, a 15-second video cut, and two ad variants that highlight the pool and clubhouse.",
                if_skip=f"{n}'s current ads keep running at their current click-through.",
                receipts=[{"label": "CTR 3.1% → 1.4% over 140 days", "source": "google_ads", "as_of": "2026-09-13T08:00:00Z"},
                          {"label": "18 usable amenity photos, 1 tour video", "source": "hubspot", "as_of": "2026-09-13T20:00:00Z"}],
                why={"text": f"{n}'s ads are worn out, and the assets to replace them already exist.", "receipts": [{"label": "CTR 3.1% → 1.4% over 140 days", "source": "google_ads", "as_of": "2026-09-13T08:00:00Z"}]},
                for_whom={"text": f"Renters searching for apartments with a pool in {p['city']}; the ads highlight {n}'s pool and clubhouse.", "questions": []},
                approving_does=[{"label": f"RPM Digital builds three crops, a 15-second cut and two ad variants from {n}'s existing assets", "owner": "RPM Digital", "when": "Within 7 business days"},
                                {"label": "Every variant passes the Fair Housing screen and AI-edit disclosure check before it runs", "owner": "RPM Digital", "when": "Before launch"},
                                {"label": "You see the finished variants here before they replace the current ads", "owner": "you", "when": "When they're ready"}],
                trail=[_trail("2026-09-13T20:00:00Z", "creative audit", f"Opened for {n}")],
            ))
        elif scen == "fh_review":
            findings = [
                {"kind": "copy", "location": f"{p['domain']}/neighborhood", "excerpt": "Perfect for young professionals starting out",
                 "reason": "Describes the kind of resident the property wants, which Fair Housing rules treat as a preference based on age and familial status.",
                 "severity": "high", "suggested_fix": f"Close to downtown offices, the riverwalk and nightlife — easy commutes from {n}."},
                {"kind": "image", "location": "Asset library: skyereserve-lobby-ai-edit-02", "excerpt": "Lobby photo with AI-extended ceiling and added furniture",
                 "reason": "The image is AI-edited and has no disclosure. A state disclosure rule may apply; this check is flagged pending confirmation.",
                 "severity": "low", "suggested_fix": "Add the 'Image digitally enhanced' caption wherever this photo runs, or use the unedited original."},
            ]
            review = {"property": {"company_id": p["company_id"], "name": n}, "run_at": "2026-09-01T06:00:00Z", "next_run": "2026-10-01T06:00:00Z", "pages_checked": 42, "assets_checked": 118, "findings": findings}
            out.append(_base_item(
                p, "fair_housing_review", f"{tail}-2026-09", category="compliance", lens="tailor", channels=["website", "listing"], owner="Dana R.",
                title=f"Your monthly Fair Housing review for {n} found {len(findings)} items",
                found=f"We checked {review['pages_checked']} pages and listing descriptions and {review['assets_checked']} images for {n} on Sep 1.",
                expect=f"Suggested copy fixes go to the web team as drafts for {n}; nothing publishes automatically.",
                if_skip=f"The flagged copy and image stay live on {n}'s site until someone changes them.",
                receipts=[{"label": f"{review['pages_checked']} pages, {review['assets_checked']} images checked", "source": "fair_housing_review", "as_of": review["run_at"]}],
                why={"text": f"The monthly review of {n}'s website, listings and images found wording and an image that need a person's call.", "receipts": [{"label": f"Run {review['run_at'][:10]}", "source": "fair_housing_review", "as_of": review["run_at"]}]},
                for_whom={"text": f"Everyone who reads {n}'s website and listings. Fair Housing rules apply to all of it, including edits made on site.", "questions": []},
                approving_does=[{"label": f"RPM Digital drafts the suggested copy fixes for {n}'s web team", "owner": "RPM Digital", "when": "Within 2 business days"},
                                {"label": "The web team reviews and publishes the fixes; nothing publishes automatically", "owner": "RPM Digital", "when": "Within 5 business days"},
                                {"label": "We re-check these pages in the next monthly review", "owner": "RPM Digital", "when": review["next_run"][:10]}],
                fair_housing_review={"severity": "high", "terms": ["young professionals"]},
                review=review,
                trail=[_trail(review["run_at"], "monthly Fair Housing review", f"Found {len(findings)} items on {n}")],
            ))
    return out


def all_items() -> list:
    items = []
    for p in props():
        items += _items_for(p)
        items += _content_items(p)
    return items


def item(item_id: str) -> dict | None:
    for it in all_items():
        if it["id"] == item_id:
            return it
    return None


def _public(it: dict) -> dict:
    return {k: v for k, v in it.items() if not k.startswith("_")}


# ── content (rows exist only once a draft exists) ────────────────────────────

def _content_items(p: dict) -> list:
    n, tail = p["name"], p["company_id"][-4:]
    if p["occupied"] is None:
        return []
    rows = [
        ("in_review", "FAQ page", f"Move-in costs and application fees at {n}", "ChatGPT (1 of 6 answers cite outdated fees)",
         [f"what does it cost to move into {n.lower()}", f"application fee apartments {p['city'].lower()}"], None),
        ("published", "FAQ page", f"Parking and garage options at {n}", "Perplexity (was 0, now 3 of 4 questions)",
         [f"does {n.lower()} have covered parking", f"apartments with garage parking {p['city'].lower()}"], "2026-07-20"),
    ]
    out = []
    for i, (status, kind, title, gap, qs, published) in enumerate(rows, start=6):
        out.append(_base_item(
            p, "content_brief", f"{tail}{i}", category="content", lens="tailor", channels=["website", "seo"],
            status="in_motion" if status == "in_review" else "done", needs_approval=False,
            actions={"approve": False, "not_now": False}, owner="Dana R.", title=title,
            found=f"{gap.split(' (')[0]} answers about {title.split(' at ')[0].lower()} didn't cite {n}.",
            why={"text": f"Renters ask this before they tour {n}, and the old answer was missing or out of date.", "receipts": []},
            for_whom={"text": f"Renters comparing apartments in {p['city']}.", "questions": qs},
            approving_does=[{"label": f"RPM Digital publishes the page to {n}'s site", "owner": "RPM Digital", "when": "Within 5 business days" if status == "in_review" else f"Published {published}"}],
            draft={"kind": "faq", "title": title, "body": f"{title}\n\n{n} answers this for renters in plain terms. The leasing team confirms current details before you apply."},
            trail=[_trail("2026-09-10T08:00:00Z", "content engine", f"Drafted for {n}")],
            _content={"status": status, "type": kind, "gap_source": gap, "published_at": published},
        ))
    return out


def content(company_id: str) -> dict:
    p = prop(company_id)
    rows = []
    for it in _items_for(p) + _content_items(p):
        if it["category"] != "content":
            continue
        meta = it.get("_content") or {"status": "draft_ready", "type": "FAQ page", "gap_source": "Perplexity (0 of 4 questions)", "published_at": None}
        prio = "done" if meta["status"] == "published" else ("high" if meta["status"] == "draft_ready" else "med")
        fw = it["for_whom"] or {}
        keyword = next((q for q in fw.get("questions", []) if not q.endswith("?")), None)
        rows.append({"id": f"row:{it['id']}", "priority": prio, "type": meta["type"], "title": it["title"], "keyword": keyword,
                     "gap_source": meta["gap_source"], "status": meta["status"], "published_at": meta["published_at"], "item_id": it["id"],
                     "why": it["why"], "for_whom": it["for_whom"],
                     "approving_does": it["approving_does"] if meta["status"] == "draft_ready" else []})
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("draft_ready", "published", "in_review")}
    impact = [{"text": f"Parking FAQ at {p['name']}: Perplexity citations went from 0 to 3 in 14 days."}] if any(r["status"] == "published" for r in rows) else []
    gaps = [] if rows else [{"message": f"No drafts yet for {p['name']}. Rows appear once a draft exists.", "field": "rows"}]
    return {"as_of": AS_OF, "counts": {"recommendations": counts["draft_ready"], "published": counts["published"], "in_review": counts["in_review"]},
            "rows": rows, "impact": impact, "gaps": gaps}


# ── screens ──────────────────────────────────────────────────────────────────

def me_data() -> dict:
    return {"email": book()["owner_email"], "role": "internal", "verified": True, "can_decide": not SWITCHES["cannot_decide"],
            "companies": [{"company_id": p["company_id"], "uuid": hashlib.md5(p["company_id"].encode()).hexdigest(), "name": p["name"],
                           "city": p["city"], "state": p["state"], "units": p["units"]} for p in props()]}


def _waiting_items() -> list:
    return [it for p in props() for it in _items_for(p) if it["status"] == "to_do" and it["actions"]["approve"]]


def _status(p: dict) -> str | None:
    """Lifecycle status as the company record would carry it; unknown until the first ApartmentIQ read."""
    if p["occupied"] is None:
        return None
    return "Lease-up" if p["occupied"] / p["units"] < 0.85 else "Stabilized"


def dashboard() -> dict:
    ps = props()
    known = [p for p in ps if p["occupied"] is not None]
    occupied, units_known = sum(p["occupied"] for p in known), sum(p["units"] for p in known)
    leases_lm, spend_lm = sum(p["leases_last_month"] for p in known), sum(p["spend_last_month"] for p in known)
    waiting = _waiting_items()
    actions_taken = sum(_h(p["company_id"] + "auto", 3, 11) for p in known)
    vis = round(sum(p["ai_visibility"] for p in known) / len(known))
    kpis = {
        "occupancy": _metric(round(occupied / units_known, 3), "aptiq"),
        "units_to_lease_90d": _metric(sum(p["units_to_lease_90d"] for p in known), "aptiq_exposure"),
        "leases_this_month": _metric(sum(p["leases_this_month"] for p in known), "hyly", "2026-09-14T11:00:00Z"),
        "cost_per_lease": _metric(round(spend_lm / leases_lm), "hubspot_line_items+hyly", "2026-09-01T06:00:00Z"),
        "ai_visibility": _metric(vis, "geo_brand_mentions", "2026-09-14T08:14:00Z"),
        "actions_taken": _metric(actions_taken, "automatic workspace_decision and loop events, last 30 days"),
        "waiting_on_you": _metric(len(waiting), "workspace_inbox"),
    }
    order = sorted(ps, key=lambda p: (-(sum(1 for it in waiting if it["company_id"] == p["company_id"])), p["health"] if p["health"] is not None else 999))
    activity = [
        {"at": "2026-09-14T13:02:00Z", "text": "Audited the Apartments.com package — LYV Broadway", "company_id": "18234410021", "kind": "audit", "visibility": "client"},
        {"at": "2026-09-14T12:46:00Z", "text": "Drafted a rate email to Apartments.com — Remi West Dallas", "company_id": "18234410112", "kind": "draft", "visibility": "client"},
        {"at": "2026-09-14T12:04:00Z", "text": "Ran a visibility check — The Bromley at Brighton Crossing", "company_id": "26136316506", "kind": "check", "visibility": "client"},
        {"at": "2026-09-14T09:30:00Z", "text": "Noted for the owner call: renewal concern at Skye Reserve", "company_id": "18234410087", "kind": "flag", "visibility": "internal"},
        {"at": "2026-09-01T06:12:00Z", "text": "Monthly Fair Housing review — no issues — Remi West Dallas", "company_id": "18234410112", "kind": "check", "visibility": "client"},
        {"at": "2026-09-13T08:10:00Z", "text": "Updated the exposure forecast — Arbor Hills Flats", "company_id": "18234410203", "kind": "forecast", "visibility": "client"},
    ]
    return {
        "greeting_name": "Dana", "as_of": AS_OF, "scope_label": f"Your {len(ps)} properties", "kpis": kpis,
        "health_tiles": [{"company_id": p["company_id"], "name": p["name"], "score": p["health"], "band": p["band"]} for p in order],
        "properties": [{"company_id": p["company_id"], "name": p["name"], "units": p["units"],
                        "occupancy": _metric(_occ(p), "aptiq"), "to_lease_90d": _metric(p["units_to_lease_90d"], "aptiq_exposure"),
                        "leases_month": _metric(p["leases_this_month"], "hyly", "2026-09-14T11:00:00Z"),
                        "status": _status(p), "health": p["health"], "band": p["band"]} for p in order],
        "activity": activity,
        "waiting": [{"item_id": it["id"], "company_id": it["company_id"], "title": it["title"], "subtitle": it["cost_note"] or (it["why"] or {}).get("text"), "category": it["category"]} for it in waiting],
        "loop_status": {"running": True, "property_count": len(ps), "last_pass": "2026-09-14T08:00:00Z"},
        "gaps": [{"message": "Cedar Falls Commons has no ApartmentIQ read yet, so its occupancy and exposure are left out of the totals.", "field": "kpis.occupancy", "source": "aptiq"}],
    }


def approvals(category: str | None = None) -> dict:
    if SWITCHES["approvals_empty"]:
        waiting = []
    else:
        waiting = _waiting_items()
    rows = [{"item_id": it["id"], "company_id": it["company_id"], "property": prop(it["company_id"])["name"], "action": it["title"],
             "category": it["category"], "savings_per_year": _metric(it.get("_savings"), "hubspot_line_items") if it.get("_savings") else None,
             "can_edit": False} for it in waiting]
    shown = [r for r in rows if not category or r["category"] == category]
    return {"as_of": AS_OF, "waiting": len(rows), "interrupts_count": 0, "approved_this_month": 12, "interrupts": [],
            "batch": {"label": "Waiting on you — September 2026", "rows": shown},
            "stats": {"approval_rate": _metric(0.89, "workspace_decisions"), "edit_rate": _metric(0.23, "workspace_decisions"),
                      "auto_approve_candidates": ["Visibility audits", "Content briefs"]},
            "gaps": []}


def property_overview(company_id: str) -> dict:
    p = prop(company_id)
    n = p["name"]
    items = _items_for(p)
    occ = _occ(p)
    exposure = None
    if p["units_to_lease_90d"] is not None:
        base = p["units_to_lease_90d"]
        exposure = {"months": [{"month": m, "units_to_lease": max(4, round(base * (1 - i * 0.065)))} for i, m in enumerate(SEP)],
                    "source": "aptiq_exposure", "as_of": AS_OF, "basis": "AptIQ exposure within 30, 60 and 90 days, extended by the lease-expiration schedule"}
    stepdown = next((it for it in items if it["id"].startswith("hubdb_rec:") and it["category"] == "cost"), None)
    email = next((it for it in items if it.get("draft") and it["draft"]["kind"] == "email"), None)
    action = stepdown or next((it for it in items if it["status"] == "to_do"), None)
    findings = []
    if stepdown:
        findings.append({"text": stepdown["found"], "receipts": stepdown["receipts"], "item_id": stepdown["id"]})
    if occ is not None:
        findings.append({"text": f"{n} is {int(round(occ * 100))}% occupied with {p['units_to_lease_90d']} units to lease in the next 90 days.",
                         "receipts": [{"label": "Occupancy and exposure", "source": "aptiq", "as_of": AS_OF}], "item_id": None})
    loop = []
    for it in items:
        loop.append({"lens": it["lens"], "status": "waiting", "at": it["trail"][0]["at"] if it["trail"] else None, "text": it["title"], "item_id": it["id"]})
    loop.append({"lens": "evolve", "status": "upcoming", "at": "2026-10-14", "text": f"30 days of before-and-after for {n} once the open decisions are made.", "item_id": None})
    engines = _engine_scores(p)
    return {
        "as_of": AS_OF, "name": n, "city": p["city"], "state": p["state"], "units": p["units"], "objective": "Stabilize" if (occ or 1) >= 0.9 else "Lease up",
        "objective_reason": None if occ is None else f"{int(round(occ * 100))}% occupied",
        "health": None if p["health"] is None else {"score": p["health"], "band": p["band"], "source": "redlight", "as_of": "2026-09-14T08:00:00Z"},
        "kpis": {"ai_visibility": _metric(p["ai_visibility"], "geo_brand_mentions"), "renewal_rate": _metric(None if occ is None else round(0.48 + _h(company_id + "ren", 0, 14) / 100, 2), "aptiq"),
                 "units_to_lease": _metric(p["units_to_lease_90d"], "aptiq_exposure"), "lead_to_lease": _metric(None if occ is None else round(_h(company_id + "l2l", 60, 110) / 1000, 3), "hyly")},
        "exposure_forecast": exposure,
        "vendor_audit": [{"vendor": "Apartments.com", "package": "Premium" if stepdown else "Gold", "monthly": _metric(3900 if stepdown else 2900, "hubspot_line_items"), "verdict": "unknown", "basis": "No market-rate source connected"},
                         {"vendor": "Google Ads", "package": "Search", "monthly": _metric(round(p["units"] * 3 / 50) * 50, "hubspot_line_items"), "verdict": "unknown", "basis": "No market-rate source connected"}],
        "visibility_by_engine": [{"engine": e, "score": _metric(s, "geo_brand_mentions")} for e, s in engines.items()],
        "findings": findings,
        "recommended_action": None if not action else {"item_id": action["id"], "label": action["title"]},
        "draft_email": None if not email else {"subject": email["draft"]["title"], "preview": email["draft"]["body"].split("\n\n")[1][:180] + "…", "item_id": email["id"]},
        "loop": loop,
        "links": {"media_plan": f"#/property/{company_id}/media-plan", "visibility": f"#/property/{company_id}/visibility", "content": f"#/property/{company_id}/content",
                  "creative": f"#/property/{company_id}/creative", "report": f"/workspace/report?company_id={company_id}"},
        "gaps": [{"message": "We don't have a market-rate source for ILS packages yet, so no package is marked over or fair.", "field": "vendor_audit.verdict"}],
    }


# ── media plan: always-on flat, flighted follows exposure; notes never contradict pending items ──

ALWAYS_ON = ("Google Business Profile", "SEO / content", "Website", "Apartments.com base listing")
FLIGHTED = ("Paid search", "Performance Max", "Meta", "Apartments.com Premium upgrade")


def plan_notes(p: dict, channels: list, pending: list) -> list:
    """Generated from the mix and the property's pending recommendations.

    Rule: a channel with a pending recommendation is described as pending and
    is never described as kept, continuous or running all year.
    """
    n = p["name"]
    pending_channels = {}
    for it in pending:
        if "Apartments.com" in it["title"] and it["category"] in ("cost", "vendor"):
            pending_channels.setdefault("Apartments.com", []).append(it)
    notes = [f"Always-on channels stay flat all year at {n}: Google Business Profile, SEO / content, the website and the base Apartments.com listing."
             if "Apartments.com" not in pending_channels else
             f"Always-on channels stay flat all year at {n}: Google Business Profile, SEO / content and the website."]
    flighted = [c for c in channels if c["mode"] == "flighted" and c["annual"]]
    if flighted:
        peak = max(range(12), key=lambda i: sum(c["monthly"][i] for c in flighted))
        notes.append(f"Flighted spend at {n} peaks in {MONTH_NAMES[int(SEP[peak][5:7]) - 1]}, following the exposure forecast.")
    for it in pending_channels.get("Apartments.com", []):
        notes.append(f"Apartments.com is shown at today's rate while \"{it['title']}\" waits on your decision. Approving it changes this plan.")
    return notes


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def media_plan(company_id: str) -> dict:
    p = prop(company_id)
    ov = property_overview(company_id)
    months = [{"month": m["month"], "units_to_lease": m["units_to_lease"]} for m in (ov["exposure_forecast"] or {"months": [{"month": m, "units_to_lease": None} for m in SEP]})["months"]]
    weights = [m["units_to_lease"] or 0 for m in months]
    avg = (sum(weights) / 12) or 1
    u = p["units"]
    flat = {"Google Business Profile": 150, "SEO / content": 450 if u < 300 else 800, "Website": 120, "Apartments.com base listing": 900}
    peak = {"Paid search": round(u * 4.5 / 50) * 50, "Performance Max": round(u * 2.5 / 50) * 50, "Meta": round(u * 1.5 / 50) * 50, "Apartments.com Premium upgrade": 1500}
    channels = []
    for name in ALWAYS_ON:
        monthly = [flat[name]] * 12
        channels.append({"channel": name, "mode": "always_on", "monthly": monthly, "source": "hubspot_line_items"})
    for name in FLIGHTED:
        monthly = [int(round(peak[name] * (w / avg) / 50) * 50) if w else 0 for w in weights]
        channels.append({"channel": name, "mode": "flighted", "monthly": monthly, "source": "hubspot_line_items"})
    allocated = sum(sum(c["monthly"]) for c in channels)
    for c in channels:
        c["annual"] = sum(c["monthly"])
        c["monthly_avg"] = round(c["annual"] / 12)
        c["share"] = round(c["annual"] / allocated, 3) if allocated else None
        c["cpl_target"] = {"Paid search": 32, "Performance Max": 45, "Meta": 58}.get(c["channel"])
    pending = [it for it in _items_for(p) if it["status"] == "to_do"]
    envelope = int(round(allocated * 1.07 / 500) * 500)
    return {"as_of": AS_OF, "fiscal_year": "FY 2026-27", "envelope": _metric(envelope, "hubspot_line_items", "2026-09-01T12:00:00Z"),
            "objective": ov["objective"], "generated_at": "2026-09-01T08:00:00Z", "months": months, "channels": channels,
            "allocated": _metric(allocated, "hubspot_line_items", "2026-09-01T12:00:00Z"), "notes": plan_notes(p, channels, pending),
            "gaps": [{"message": "SEO, website and listing channels have no cost-per-lead target.", "field": "channels[].cpl_target"}]}


# ── visibility: the audit itself ─────────────────────────────────────────────

def _engine_scores(p):
    return {e: None if p["ai_visibility"] is None else max(20, min(95, p["ai_visibility"] + _h(p["company_id"] + e, -14, 12))) for e in ENGINES[:4]}


def visibility(company_id: str) -> dict:
    p = prop(company_id)
    n, city = p["name"], p["city"]
    if p["ai_visibility"] is None:
        return {"as_of": AS_OF, "score": None, "change": None, "last_audit": None, "next_audit": "2026-10-01", "engines": [], "comp_stack": None,
                "citation_sources": [], "recommendations": [], "alerts": [], "prompts": [], "fanout": [], "writing": [],
                "gaps": [{"message": f"{n} hasn't had its first AI visibility audit yet.", "field": "prompts", "source": "geo_prompts"}]}
    content_rows = content(company_id)["rows"]
    by_topic = {}
    for r in content_rows:
        t = r["title"].lower()
        topic = "Pets" if "pet" in t else "Costs and fees" if "cost" in t or "fee" in t else "Parking" if "parking" in t else "Floor plans"
        by_topic[topic] = r
    prompts_spec = [
        ("Pets", "decide", f"Which apartments in {city} allow large dogs?"),
        ("Pets", "compare", f"Best pet friendly apartments in {city}"),
        ("Costs and fees", "decide", f"What does it cost to move into {n}?"),
        ("Costs and fees", "compare", f"Apartments in {city} with low move-in fees"),
        ("Parking", "decide", f"Does {n} have covered parking?"),
        ("Neighborhood", "learn", f"What's near {n}?"),
        ("Floor plans", "compare", f"One-bedroom apartments in {city} with a washer and dryer"),
    ]
    engines = ENGINES
    prompts = []
    for i, (topic, intent, text) in enumerate(prompts_spec):
        row = {}
        for e in engines:
            k = _h(company_id + text + e, 0, 99)
            named = k < p["ai_visibility"] + (20 if topic == "Parking" else 0) - (35 if topic == "Pets" and e == "Perplexity" else 0)
            row[e] = {"named": bool(named), "cited": bool(named and k % 3 != 0)}
        prompts.append({"id": f"prompt:{company_id}:{i + 1}", "text": text, "topic": topic, "intent": intent, "engines": row})
    fanout = []
    for topic, qs in (("Pets", [f"pet friendly apartments {city.lower()}", f"{n.lower()} pet policy", f"breed restrictions apartments {city.lower()}"]),
                      ("Costs and fees", [f"{n.lower()} application fee", f"move in specials {city.lower()} apartments"]),
                      ("Parking", [f"{n.lower()} parking", f"apartments with garages {city.lower()}"])):
        for j, q in enumerate(qs):
            r = by_topic.get(topic)
            fanout.append({"query": q, "engine": engines[j % len(engines)], "count": _h(company_id + q, 2, 14), "topic": topic,
                           "content": None if not r else {"item_id": r["item_id"], "title": r["title"], "status": r["status"]}})
    writing = []
    for topic, r in by_topic.items():
        # What we're writing reports drafted | in_review | published (a content row's draft_ready is "drafted" here).
        writing.append({"title": r["title"], "topic": topic, "answers": [f["query"] for f in fanout if f.get("topic") == topic],
                        "status": "drafted" if r["status"] == "draft_ready" else r["status"], "item_id": r["item_id"]})
    scores = {e: max(20, min(95, p["ai_visibility"] + _h(company_id + e, -14, 12))) for e in engines}
    hits = {e: sum(1 for pr in prompts if pr["engines"][e]["named"]) for e in engines}
    comps = [f"The Reserve at {city}", f"{city} Station", f"Solana {city}"]
    return {
        "as_of": AS_OF, "score": _metric(p["ai_visibility"], "geo_brand_mentions", "2026-09-01T08:14:00Z"),
        "change": {"value": _h(company_id + "chg", -3, 6), "window": "month", "source": "geo_brand_mentions"},
        "last_audit": "2026-09-01", "next_audit": "2026-10-01",
        "engines": [{"engine": e, "score": _metric(scores[e], "geo_brand_mentions", "2026-09-01T08:14:00Z"), "queries_hit": hits[e], "queries_total": len(prompts)} for e in engines],
        "comp_stack": {"competitors": comps, "rows": [{"surface": "AI visibility", "values": dict({"self": p["ai_visibility"]}, **{c: _h(c, 40, 80) for c in comps})}]},
        "citation_sources": [{"source": "Apartments.com", "share": 0.34, "share_source": "geo_sources"}, {"source": f"{p['domain']}", "share": 0.28, "share_source": "geo_sources"},
                             {"source": "Zillow", "share": 0.18, "share_source": "geo_sources"}, {"source": "Google Maps", "share": 0.12, "share_source": "geo_sources"},
                             {"source": "Other", "share": 0.08, "share_source": "geo_sources"}],
        "recommendations": [{"text": w["title"], "action": {"type": "open_content", "item_id": w["item_id"]}} for w in writing],
        "alerts": [] if p["units_to_lease_90d"] is None else [{"kind": "exposure", "text": f"{p['units_to_lease_90d']} units to lease at {n} in 90 days. Every question an AI engine can't answer about {n} is a renter we don't reach."}],
        "prompts": prompts, "fanout": fanout, "writing": writing,
        "gaps": [],
    }


def creative(company_id: str, kind: str | None = None) -> dict:
    p = prop(company_id)
    s = p["slug"]
    specs = [("pool-03", ["photo", "amenity"], "generated", 1840, 0.048, 6, None), ("exterior-01", ["photo", "exterior"], "inherited", 1240, 0.032, 4, None),
             ("one-bedroom-renovated", ["photo", "interior"], "uploaded", 980, 0.021, 2, None), ("exterior-06", ["photo", "exterior"], "inherited", 620, 0.003, 0, "underperforming"),
             ("clubhouse-tour", ["video", "amenity"], "generated", 2210, 0.037, 5, None), ("one-bedroom-floorplan", ["floor plan"], "uploaded", None, None, None, None)]
    tag_for = {"photos": "photo", "videos": "video", "floor_plans": "floor plan", "ad_creative": "ad", "documents": "document"}
    assets = []
    for i, (name, tags, origin, imp, ctr, leads, flag) in enumerate(specs):
        if kind and tag_for.get(kind) not in tags:
            continue
        assets.append({"id": f"asset:{p['company_id'][-4:]}{i}", "name": f"{s}-{name}", "type": tags[0], "thumbnail_url": None, "file_url": None, "tags": tags, "origin": origin,
                       "impressions": _metric(imp, "google_ads", "2026-09-13T08:00:00Z"), "ctr": _metric(ctr, "google_ads", "2026-09-13T08:00:00Z"),
                       "leads": _metric(leads, "hyly", "2026-09-13T11:00:00Z"), "flag": flag})
    return {"as_of": AS_OF, "counts": {"assets": len(assets), "tracked_in_ads": sum(1 for a in assets if a["impressions"])}, "top": None, "lowest": None, "assets": assets,
            "gaps": [{"message": "Per-asset ad results come from a manual export until Google Ads is connected.", "field": "assets[].ctr", "source": "google_ads"}]}


def signals_data(company_id: str | None = None) -> dict:
    out = []
    for p in props():
        if company_id and p["company_id"] != company_id:
            continue
        if p["health"] is not None and p["health"] < 60:
            out.append({"id": f"occupancy_drop:{p['company_id']}:2026-09-14", "company_id": p["company_id"], "property_name": p["name"], "kind": "occupancy_drop", "severity": "high",
                        "title": "Occupancy down 3.1 points in 30 days", "detail": f"{p['name']} is {int(round(_occ(p) * 100))}% occupied today.",
                        "metric": _metric(_occ(p), "aptiq"), "change": {"from": round(_occ(p) + 0.031, 3), "to": _occ(p), "window_days": 30}, "detected_at": "2026-09-14T13:10:00Z", "work_item_id": None})
        if p["company_id"] == "18234410150":
            out.append({"id": f"spend_pacing:{p['company_id']}:2026-09-14", "company_id": p["company_id"], "property_name": p["name"], "kind": "spend_pacing", "severity": "medium",
                        "title": "Paid search has spent 71% of the month by day 14", "detail": f"{p['name']} is on pace to run out around Sep 20. Pacing is an internal signal; nothing is paused automatically.",
                        "metric": _metric(0.71, "hubspot_line_items"), "change": {"from": 0.47, "to": 0.71, "window_days": 14}, "detected_at": "2026-09-14T12:40:00Z", "work_item_id": None})
    counts = {sev: sum(1 for s in out if s["severity"] == sev) for sev in ("high", "medium", "low")}
    return {"as_of": AS_OF, "counts": counts, "signals": out, "gaps": [{"message": "Lead signals need GA4, which is not connected for most of these properties.", "source": "ga4"}]}


def value_data() -> dict:
    rows = [
        {"change": "Stepped Apartments.com down from Premium to Standard after 2 leases in six months", "company_id": "18234410203", "property": "Arbor Hills Flats", "annual_value": _metric(26400, "workspace_decisions", "2026-08-12T15:00:00Z"), "decided_by": "you", "decided_at": "2026-08-12T15:00:00Z"},
        {"change": "Moved flat annual budget into the weeks ahead of each lease-expiration cluster", "company_id": None, "property": "All 35 properties", "annual_value": _metric(61200, "workspace_decisions", "2026-07-01T15:00:00Z"), "decided_by": "team", "decided_at": "2026-07-01T15:00:00Z"},
        {"change": "Stopped 6 ad creatives that had run past 90 days and returned no leads", "company_id": None, "property": "5 properties", "annual_value": _metric(28300, "workspace_decisions", "2026-06-09T08:00:00Z"), "decided_by": "automatic", "decided_at": "2026-06-09T08:00:00Z"},
        {"change": "Renegotiated the Zillow rate at the October renewal", "company_id": "18234410087", "property": "Skye Reserve", "annual_value": _metric(7200, "workspace_decisions", "2025-10-02T15:00:00Z"), "decided_by": "you", "decided_at": "2025-10-02T15:00:00Z"},
        {"change": "Rewrote 41 amenity descriptions so AI engines can read them", "company_id": None, "property": "6 properties", "annual_value": None, "decided_by": "automatic", "decided_at": "2026-05-18T08:00:00Z"},
    ]
    return {"as_of": AS_OF, "period": "Sep 2025 – Aug 2026",
            "headline": {"savings_captured": _metric(214000, "workspace_decisions"), "savings_identified": _metric(340000, "hubdb_rec"), "changes_shipped": _metric(412, "workspace_decisions")},
            "rows": rows, "totals": {"annual_value": _metric(214000, "workspace_decisions"), "changes": 412, "automatic_share": _metric(0.94, "workspace_decisions")},
            "gaps": [{"message": "31 changes have no savings recorded on their recommendation, so they count as changes but add nothing to the total.", "field": "rows[].annual_value"}]}


def search_data(q: str) -> dict:
    q = q.strip().lower()
    results = []
    if len(q) < 2:
        return {"as_of": AS_OF, "results": []}
    for p in props():
        if q in p["name"].lower() or q in p["city"].lower():
            results.append({"type": "property", "id": p["company_id"], "title": p["name"], "subtitle": f"{p['city']}, {p['state']} · {p['units']} units", "company_id": p["company_id"], "href": "#/property"})
    for it in _waiting_items():
        if q in it["title"].lower():
            results.append({"type": "work_item", "id": it["id"], "title": it["title"], "subtitle": f"{prop(it['company_id'])['name']} · waiting on you", "company_id": it["company_id"], "href": f"#/item/{it['id']}"})
    for path in FIXTURES.glob("report_*.json"):
        d = json.loads(path.read_text(encoding="utf-8"))
        name = d["property"]["name"]
        if q in name.lower() or q in "report":
            results.append({"type": "report", "id": f"report:{d['property']['company_id']}:{d['month']}", "title": f"{d.get('month_label', d['month'])} report", "subtitle": name, "company_id": str(d["property"]["company_id"]), "href": "#/reports"})
    return {"as_of": AS_OF, "results": results[:20]}


def requests_recent_data(company_id: str) -> dict:
    p = prop(company_id) or props()[0]
    n = p["name"]
    return {"as_of": AS_OF, "recent": [
        {"title": f"Update the pool-closure notice for {n}", "status": "done", "status_date": "2026-09-11", "work_item_id": f"portal_ticket:{p['company_id'][-4:]}1"},
        {"title": f"New clubhouse tour cut for {n} from existing video", "status": "in_progress", "status_date": "2026-09-09", "work_item_id": f"portal_ticket:{p['company_id'][-4:]}2"},
        {"title": f"Fix {n}'s Google Business hours", "status": "done", "status_date": "2026-09-02", "work_item_id": f"portal_ticket:{p['company_id'][-4:]}3"},
    ]}


def decision_for(it: dict) -> dict:
    kind = {"RPM Digital": "queued", "vendor": "person", "you": "person"}
    done = dict(it, status="in_motion", needs_approval=False, actions={"approve": False, "not_now": False})
    now = datetime.now(timezone.utc)
    return {
        "item": _public(done), "decided_by": book()["owner_email"], "decided_at": now.isoformat(),
        "in_motion": [{"label": s["label"], "kind": kind.get(s["owner"], "queued"), "status": "pending", "detail": None, "note": s["when"], "action": None} for s in it["approving_does"]],
        "written_down": f"Approved unedited for {prop(it['company_id'])['name']}. It counts toward this kind of change's record.",
        "record": None, "undo": {"available": True, "until": (now + timedelta(minutes=10)).isoformat(), "reason": None},
        "check_back": None,
    }


PREVIEW_ROLE_HEADER = "X-Workspace-Preview-Role"
INTERNAL_ONLY_PATHS = ("/api/workspace/portfolio", "/api/workspace/signals")


def _as_client() -> bool:
    return request.headers.get(PREVIEW_ROLE_HEADER, "").strip().lower() == "client"


def _client_filter(obj):
    """What a client-role caller gets: no internal entries, no Fair Housing review flag."""
    if isinstance(obj, list):
        return [_client_filter(x) for x in obj if not (isinstance(x, dict) and x.get("visibility") == "internal")]
    if isinstance(obj, dict):
        return {k: _client_filter(v) for k, v in obj.items() if k != "fair_housing_review"}
    return obj


@app.before_request
def _preview_role_gate():
    # Mirrors the API contract: the header is honored on reads only, and /me ignores it.
    if request.method == "GET" and _as_client() and request.path.startswith(INTERNAL_ONLY_PATHS):
        return jsonify({"error": "forbidden", "detail": "Internal only."}), 403
    if request.method == "POST" and _as_client():
        return jsonify({"error": "preview_read_only", "detail": "Previewing as a client is read-only."}), 403
    return None


@app.after_request
def _preview_role_filter(resp):
    if (request.method == "GET" and _as_client() and resp.is_json and resp.status_code == 200
            and request.path.startswith(("/api/workspace/", "/api/ask/")) and request.path != "/api/workspace/me"):
        resp.set_data(json.dumps(_client_filter(resp.get_json())))
    return resp


@app.get("/")
def root():
    return Response('<a href="/workspace?t=preview">Open the workspace preview</a>', mimetype="text/html")


@app.get("/workspace")
def workspace():
    # Read on every request so edits to the page show on reload.
    return Response(PAGE.read_text(encoding="utf-8"), mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.get("/api/workspace/me")
def me():
    return jsonify(me_data())


@app.get("/api/workspace/portfolio")
def portfolio():
    return jsonify(_fixture("portfolio"))


def _all_items(work: dict) -> list[dict]:
    groups = work.get("groups") or {}
    return list(groups.get("late") or []) + list(groups.get("this_week") or [])


@app.get("/api/workspace/work")
def work():
    data = _fixture("work")
    status = request.args.get("status", "to_do")
    if status != "all":
        groups = data["groups"]
        for key in ("late", "this_week"):
            groups[key] = [it for it in groups.get(key) or [] if it.get("status") == status]
    return jsonify(data)


@app.get("/api/workspace/work/<path:item_id>")
def work_item(item_id: str):
    it = item(item_id)
    if it:
        return jsonify(_public(it))
    detail = _fixture("item")
    if item_id == detail["id"]:
        return jsonify(detail)
    for it in _all_items(_fixture("work")):
        if it["id"] == item_id:
            return jsonify(it)
    abort(404)


@app.post("/api/workspace/work/<path:item_id>/decision")
def decision(item_id: str):
    body = request.get_json(silent=True) or {}
    if body.get("action") not in ("approve", "not_now"):
        return jsonify({"error": "action must be approve or not_now"}), 400
    if body.get("action") == "not_now" and not body.get("reason"):
        return jsonify({"error": "reason is required when the action is not_now"}), 400
    it = item(item_id)
    if it:
        return jsonify(decision_for(it))
    data = copy.deepcopy(_fixture("decision"))
    now = datetime.now(timezone.utc)
    data["decided_at"] = now.isoformat()
    data["undo"]["until"] = (now + timedelta(minutes=10)).isoformat()
    return jsonify(data)


@app.get("/api/workspace/property")
def property_():
    return jsonify(_fixture("property"))


@app.get("/api/workspace/performance")
def performance():
    return jsonify(_fixture("performance"))


@app.get("/api/workspace/plan")
def plan():
    return jsonify(_fixture("plan"))


@app.get("/api/workspace/client-view")
def client_view():
    return jsonify(_fixture("client_view"))


@app.get("/api/workspace/signals")
def signals():
    return jsonify(signals_data(request.args.get("company_id") or None))


@app.post("/api/workspace/signals/<path:signal_id>/start-work")
def start_work(signal_id: str):
    body = request.get_json(silent=True) or {}
    if not body.get("company_id"):
        return jsonify({"error": "company_id is required"}), 400
    if signal_id not in {s["id"] for s in signals_data()["signals"]}:
        abort(404)
    return jsonify({"work_item_id": f"portal_ticket:{body['company_id'][-4:]}9", "clickup_task_id": "86b2k8a2c"}), 201


@app.post("/api/workspace/requests/draft")
def request_draft():
    body = request.get_json(silent=True) or {}
    if not str(body.get("text") or "").strip():
        return jsonify({"error": "text is required"}), 400
    return jsonify(_fixture("request_draft"))


@app.post("/api/workspace/requests")
def request_file():
    body = request.get_json(silent=True) or {}
    tickets = body.get("tickets") or []
    if not tickets:
        return jsonify({"error": "tickets are required"}), 400
    created, failed = [], []
    for i, t in enumerate(tickets):
        if not str(t.get("title") or "").strip():
            failed.append({"draft_id": t.get("draft_id"), "reason": "A ticket needs a title."})
        else:
            created.append({"draft_id": t.get("draft_id"), "work_item_id": f"portal_ticket:{4471 + i}", "clickup_task_id": f"86b2k7x{i}"})
    return jsonify({"as_of": _fixture("request_created")["as_of"], "created": created, "failed": failed})


@app.get("/api/workspace/requests")
def requests_recent():
    return jsonify(requests_recent_data(request.args.get("company_id")))


@app.get("/api/workspace/search")
def search():
    return jsonify(search_data(request.args.get("q") or ""))


@app.post("/api/workspace/work/<path:item_id>/undo")
def undo(item_id: str):
    body = request.get_json(silent=True) or {}
    if not body.get("company_id"):
        return jsonify({"error": "company_id is required"}), 400
    it = item(item_id)
    if not it or it["category"] == "vendor":
        # Demonstrates the 409 path: a vendor email has already gone out.
        return jsonify({"error": "not_undoable", "reason": "The email to the vendor already went out, so it can't be pulled back from here."}), 409
    return jsonify({"item": _public(it), "undone": True})


def _company_or_404():
    company_id = (request.args.get("company_id") or "").strip()
    if not prop(company_id):
        abort(404)
    return company_id


@app.get("/api/workspace/dashboard")
def dashboard_route():
    return jsonify(dashboard())


@app.get("/api/workspace/approvals")
def approvals_route():
    return jsonify(approvals(request.args.get("category") or None))


@app.get("/api/workspace/property-overview")
def property_overview_route():
    return jsonify(property_overview(_company_or_404()))


@app.get("/api/workspace/media-plan")
def media_plan_route():
    return jsonify(media_plan(_company_or_404()))


@app.get("/api/workspace/visibility")
def visibility_route():
    return jsonify(visibility(_company_or_404()))


@app.get("/api/workspace/content")
def content_route():
    return jsonify(content(_company_or_404()))


@app.get("/api/workspace/creative")
def creative_route():
    return jsonify(creative(_company_or_404(), request.args.get("type") or None))


@app.post("/api/workspace/creative/upload")
def creative_upload():
    # Accepts multipart uploads and fakes success; nothing is stored.
    company_id = (request.form.get("company_id") or "").strip()
    if not prop(company_id):
        return jsonify({"error": "company_id is required"}), 400
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify({"error": "files are required"}), 400
    slug = prop(company_id)["slug"]
    uploaded = [{"asset_id": f"asset:{company_id[-4:]}u{i}", "name": f"{slug}-{Path(f.filename or 'upload').stem}".lower().replace(" ", "-"),
                 "filename": f.filename, "status": "processing"} for i, f in enumerate(files)]
    return jsonify({"uploaded": uploaded, "failed": []}), 201


@app.post("/api/workspace/visibility/create-brief")
def create_brief():
    body = request.get_json(silent=True) or {}
    if not body.get("company_id"):
        return jsonify({"error": "company_id is required"}), 400
    keyword = str(body.get("hub_keyword") or "").strip()
    if not 2 <= len(keyword) <= 120:
        return jsonify({"error": "hub_keyword is required", "detail": "2 to 120 characters"}), 400
    data = _fixture("create_brief")
    data["hub_keyword"] = keyword
    return jsonify(data), 202


@app.post("/api/workspace/media-plan/regenerate")
def media_plan_regenerate():
    body = request.get_json(silent=True) or {}
    if not body.get("company_id"):
        return jsonify({"error": "company_id is required"}), 400
    return jsonify(_fixture("media_plan_regenerate")), 201


@app.get("/api/workspace/value")
def value():
    return jsonify(value_data())


# The monthly report page and its API, at the same paths production uses.
@app.get("/workspace/report")
def report_page():
    return Response(REPORT_PAGE.read_text(encoding="utf-8"), mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.get("/api/workspace/report")
def report_api():
    company_id = (request.args.get("company_id") or "").strip()
    if not company_id:
        return jsonify({"error": "company_id required"}), 400
    reports = {}
    for path in sorted(FIXTURES.glob("report_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        reports[(str(data["property"]["company_id"]), data["month"])] = data
    months = sorted(m for (cid, m) in reports if cid == company_id)
    if not months:
        return jsonify({"error": "report_unavailable", "detail": "No report for this property yet."}), 404
    month = (request.args.get("month") or "").strip() or months[-1]
    if (company_id, month) not in reports:
        return jsonify({"error": "month_unavailable", "detail": f"No report for {month}."}), 404
    return jsonify(reports[(company_id, month)])


@app.get("/api/ask/questions")
def ask_questions():
    return jsonify(_fixture("ask_questions"))


@app.post("/api/ask/<key>")
def ask_answer(key: str):
    keys = [q["key"] for q in _fixture("ask_questions")["questions"]]
    if key not in keys:
        return jsonify({"error": "Unknown question", "question": key, "available": keys}), 404
    data = _fixture("ask_answer")
    data["question"] = key
    data["label"] = next(q["label"] for q in _fixture("ask_questions")["questions"] if q["key"] == key)
    return jsonify(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Workspace preview against the 35-property preview book.")
    parser.add_argument("--port", type=int, default=PORT, help=f"port to listen on (default {PORT})")
    args = parser.parse_args()
    print(f"  workspace preview: http://127.0.0.1:{args.port}/workspace?t=preview")
    app.run(host="127.0.0.1", port=args.port, debug=False)
