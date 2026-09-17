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
import os
import sys
import re
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "webhook-server" / "portal_pages" / "workspace.html"
REPORT_PAGE = REPO / "webhook-server" / "portal_pages" / "workspace_report.html"
FIXTURES = REPO / "tests" / "fixtures" / "workspace"
PORT = 5057

app = Flask(__name__)

# Demo switches for screenshots, flipped at /__preview/<name>?on=1|0.
SWITCHES = {"approvals_empty": False, "cannot_decide": False, "client_editor": False}


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
            review = {"property": {"company_id": p["company_id"], "name": n}, "run_at": "2026-09-01T14:00:00Z", "next_run": "2026-10-01T14:00:00Z", "pages_checked": 42, "assets_checked": 118, "findings": findings}
            out.append(_base_item(
                p, "fair_housing_review", f"{tail}-2026-09", category="compliance", lens="tailor", channels=["website", "listing"], owner="Dana R.",
                title=f"Your monthly Fair Housing review for {n} found {len(findings)} items",
                found=f"We checked {review['pages_checked']} pages and listing descriptions and {review['assets_checked']} images for {n} on Sep 1.",
                expect=f"Suggested copy fixes go to the web team as drafts for {n}; nothing publishes automatically.",
                if_skip=f"The flagged copy and image stay live on {n}'s site until someone changes them.",
                receipts=[{"label": f"{review['pages_checked']} pages, {review['assets_checked']} images checked", "source": "fair_housing_review", "as_of": review["run_at"]}],
                why={"text": f"The monthly review of {n}'s website, listings and images found wording and an image that need a person's call.", "receipts": [{"label": "Monthly review, Sep 1", "source": "fair_housing_review", "as_of": review["run_at"]}]},
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
    for it in profile_update_items():
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
    return {"email": book()["owner_email"], "role": "client" if SWITCHES["client_editor"] else "internal", "verified": True, "can_decide": not SWITCHES["cannot_decide"],
            "companies": [{"company_id": p["company_id"], "uuid": hashlib.md5(p["company_id"].encode()).hexdigest(), "name": p["name"],
                           "city": p["city"], "state": p["state"], "units": p["units"]} for p in props()]}


def _waiting_items() -> list:
    return [it for p in props() for it in _items_for(p) if it["status"] == "to_do" and it["actions"]["approve"]]


def _status(p: dict) -> str | None:
    """Lifecycle status as the company record would carry it; unknown until the first ApartmentIQ read."""
    if p["occupied"] is None:
        return None
    return "Lease-up" if p["occupied"] / p["units"] < 0.85 else "Stabilized"


def dashboard(client: bool = False) -> dict:
    ps = props()
    known = [p for p in ps if p["occupied"] is not None]
    occupied, units_known = sum(p["occupied"] for p in known), sum(p["units"] for p in known)
    waiting = _waiting_items() + ([] if client else profile_update_items())
    actions_taken = sum(_h(p["company_id"] + "auto", 3, 11) for p in known)
    vis = round(sum(p["ai_visibility"] for p in known) / len(known))
    kpis = {
        "occupancy": _metric(round(occupied / units_known, 3), "aptiq"),
        "units_to_lease_90d": _metric(sum(p["units_to_lease_90d"] for p in known), "aptiq_exposure"),
        "leases_this_month": _metric(sum(p["leases_this_month"] for p in known), "hyly", "2026-09-14T11:00:00Z"),
        "cost_per_lease": None,
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
                        "status": _status(p), "profile_completeness": profile_completeness_metric(p["company_id"], client),
                        "health": p["health"], "band": p["band"]} for p in order],
        "activity": activity,
        "waiting": (profile_checkins() if client else []) + [{"kind": "approval", "item_id": it["id"], "company_id": it["company_id"], "title": it["title"],
                                                               "subtitle": it["cost_note"] or (it["why"] or {}).get("text"), "category": it["category"]} for it in waiting],
        "loop_status": {"running": True, "property_count": len(ps), "last_pass": "2026-09-14T08:00:00Z"},
        "gaps": [{"message": "Cedar Falls Commons has no ApartmentIQ read yet, so its occupancy and exposure are left out of the totals.", "field": "kpis.occupancy", "source": "aptiq"},
                 {"message": "Cost per lease comes from Hyly spend, which is not connected yet.", "field": "kpis.cost_per_lease", "source": "hyly"}],
    }


def approvals(category: str | None = None, client: bool = False) -> dict:
    if SWITCHES["approvals_empty"]:
        waiting = []
    else:
        # Profile updates are reviewed by RPM staff only; clients never see them in Approvals.
        waiting = _waiting_items() + ([] if client else profile_update_items())
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
    return {"profile_completeness": profile_completeness_metric(company_id, _viewer_client() if request else False),
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
    if request.method in ("POST", "PATCH", "PUT", "DELETE") and _as_client():
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
    if it and it["source"] == "profile_update":
        if _viewer_client():
            return jsonify({"error": "forbidden", "detail": "Profile updates are reviewed by RPM."}), 403
        apply_profile_decision(item_id, body["action"], body.get("reason"))
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
    if item_id.startswith("profile_update:"):
        restored = undo_profile_decision(item_id)
        if restored:
            return jsonify({"item": _public(restored), "undone": True})
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
    return jsonify(dashboard(client=_viewer_client()))


@app.get("/api/workspace/approvals")
def approvals_route():
    return jsonify(approvals(request.args.get("category") or None, client=_viewer_client()))


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
    """Multipart company_id + files, as the API takes them. Fakes storage and
    answers in the contract's shape (tests/workspace_contract.CREATIVE_UPLOAD)."""
    company_id = (request.form.get("company_id") or "").strip()
    p = prop(company_id)
    if not p:
        return jsonify({"error": "company_id is required"}), 400
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no_files", "detail": "Add at least one photo or video."}), 400
    uploaded, skipped = [], []
    for i, f in enumerate(files):
        name = f.filename or f"upload-{i + 1}"
        kind = (f.mimetype or "").split("/")[0]
        if kind not in ("image", "video"):
            skipped.append({"filename": name, "reason": "only photos and videos"})
            continue
        stem = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-") or "upload"
        asset = f"{p['slug']}-{stem}"
        uploaded.append({"filename": name, "file_url": f"https://files.preview.invalid/{p['slug']}/{asset}{Path(name).suffix.lower()}",
                         "thumbnail_url": None, "asset_name": asset, "category": "photos" if kind == "image" else "videos", "subcategory": None})
    if not uploaded:
        return jsonify({"error": "nothing_stored", "detail": "None of those files could be stored.", "skipped": skipped}), 400
    return jsonify({"uploaded": uploaded, "skipped": skipped}), 201


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


# ── RPMI roll-up ─────────────────────────────────────────────────────────────
#
# Built from the same preview book, with coverage deliberately uneven so the
# partial states are what you see first: availability on most of the roster,
# the leasing funnel on a handful. The live endpoint's shape is pinned by
# tests/workspace_contract.py ("rpmi") and tests/test_workspace_rpmi.py.

RPMI_SOURCE_LABELS = [("aptiq", "Availability"), ("ga4", "Analytics"),
                      ("google_ads", "Paid"), ("hyly", "Funnel")]


def _rpmi_sources(p: dict) -> dict:
    """Which feeds this property carries, spread the way the real roster is."""
    n = _h(p["company_id"], 0, 99)
    return {"aptiq": p["occupied"] is not None, "ga4": n >= 25, "google_ads": n >= 30,
            "hyly": p["leases_this_month"] is not None and n >= 85}


def rpmi_data() -> dict:
    import calendar
    rows, month = [], LAST_FULL_MONTH
    _y, _m = (int(part) for part in month.split("-"))
    month_end = "%s-%02d" % (month, calendar.monthrange(_y, _m)[1])
    for p in props():
        have = _rpmi_sources(p)
        gaps, occ = [], _occ(p)
        avail = (p["units"] - p["occupied"]) if have["aptiq"] else None
        if not have["aptiq"]:
            gaps.append({"field": "units_at_risk", "source": "aptiq",
                         "message": "Availability and occupancy aren’t connected here yet, so units "
                                    "at risk is unknown."})
        leases = p["leases_last_month"] if have["hyly"] else None
        if leases is None:
            gaps.append({"field": "leases_last_month", "source": "hyly",
                         "message": "The leasing funnel isn’t connected here, so leases and cost per "
                                    "lease are unknown."})
        for key, message in (("ga4", "Website analytics isn’t connected here yet."),
                             ("google_ads", "Paid search isn’t connected here yet.")):
            if not have[key]:
                gaps.append({"field": key, "source": key, "message": message})
        spend = p["spend_last_month"] if have["google_ads"] else None
        if spend is None:
            gaps.append({"field": "spend_monthly", "source": "hubspot_line_items",
                         "message": "No contracted line items are on file, so monthly spend is unknown."})
        cost = round(spend / leases, 2) if (spend and leases) else None
        rows.append({
            "company_id": p["company_id"], "name": p["name"], "city": p["city"], "state": p["state"],
            "market": None, "status": _status(p), "href": "#/property/" + p["company_id"],
            "units": _metric(p["units"], "hubspot_company"),
            "occupancy": _metric(occ, "aptiq") if have["aptiq"] else None,
            "available_units": _metric(avail, "aptiq") if have["aptiq"] else None,
            "units_at_risk": _metric(avail, "aptiq") if have["aptiq"] else None,
            "leases_last_month": _metric(leases, "hyly", month_end),
            "cost_per_lease": (_metric(cost, "hubspot_line_items+hyly", month_end) if cost else None),
            "spend_monthly": _metric(spend, "hubspot_line_items"),
            "open_recommendations": _metric(len(_items_for(p)), "workspace_inbox"),
            "sources": have, "gaps": gaps,
        })
    rows.sort(key=lambda r: (-(r["units_at_risk"]["value"] if r["units_at_risk"] else -1),
                             (r["name"] or "").lower()))

    def _known(key):
        return [r[key]["value"] for r in rows if r[key]]

    avail_known, spend_known, lease_known = _known("units_at_risk"), _known("spend_monthly"), _known("leases_last_month")
    occ_rows = [r for r in rows if r["occupancy"]]
    units_occ = sum(r["units"]["value"] for r in occ_rows)
    weighted = sum(r["occupancy"]["value"] * r["units"]["value"] for r in occ_rows)
    totals = {
        "units": _metric(sum(r["units"]["value"] for r in rows), "hubspot_company"),
        "occupancy": (dict(_metric(round(weighted / units_occ, 4), "aptiq"), units=units_occ)
                      if units_occ else None),
        "available_units": (dict(_metric(sum(avail_known), "aptiq"), properties=len(avail_known))
                            if avail_known else None),
        "units_at_risk": (dict(_metric(sum(avail_known), "aptiq"), properties=len(avail_known))
                          if avail_known else None),
        "leases_last_month": (dict(_metric(sum(lease_known), "hyly", month_end), properties=len(lease_known))
                              if lease_known else None),
        "spend_monthly": (dict(_metric(sum(spend_known), "hubspot_line_items"), properties=len(spend_known))
                          if spend_known else None),
        "cost_per_lease": (dict(_metric(round(sum(spend_known) / sum(lease_known), 2),
                                        "hubspot_line_items+hyly", month_end), properties=len(lease_known))
                           if spend_known and sum(lease_known) else None),
        "open_recommendations": _metric(sum(r["open_recommendations"]["value"] for r in rows), "workspace_inbox"),
    }
    coverage = {"property_count": len(rows), "sources": [
        {"key": key, "label": label, "count": sum(1 for r in rows if r["sources"][key]),
         "missing": sum(1 for r in rows if not r["sources"][key])}
        for key, label in RPMI_SOURCE_LABELS]}
    no_market = sum(1 for r in rows if not r["market"])
    return {
        "as_of": AS_OF, "scope_label": "RPM Investments · %d managed properties" % len(rows),
        "client_values": ["RPMI", "RPM Investments"],
        "record_count": len(rows) + 7, "property_count": len(rows),
        "period": {"leases_month": month}, "totals": totals, "coverage": coverage,
        "grouping": {"field": "state", "label": "State",
                     "note": "Grouped by state: market is not filled in on most of these properties."},
        "properties": rows,
        "gaps": [{"field": "market", "source": "hubspot_company",
                  "message": "Market is not set on %d of %d properties, so the list groups by state "
                             "instead." % (no_market, len(rows))}],
    }


@app.get("/api/workspace/rpmi")
def rpmi():
    return jsonify(rpmi_data())


# The monthly report page and its API, at the same paths production uses.
@app.get("/workspace/report")
def report_page():
    return Response(REPORT_PAGE.read_text(encoding="utf-8"), mimetype="text/html", headers={"Cache-Control": "no-store"})


LAST_FULL_MONTH = "2026-08"


def _report_without_hyly(p: dict, month: str) -> dict | None:
    """What the API returns for a property outside the Hyly beta: property identity,
    units and listings, plus a gap for every Hyly-only section. Built with the
    report skill itself so the preview can't drift from it."""
    try:
        sys.path.insert(0, str(REPO / "webhook-server"))
        from skills import workspace_report as wr
        from skills.property_resolver import PropertyIdentity
    except Exception:  # pragma: no cover - the preview still runs without the skill
        return None
    ident = PropertyIdentity(company_id=p["company_id"], uuid=hashlib.md5(p["company_id"].encode()).hexdigest(), name=p["name"],
                             unit_count=str(p["units"]))
    cid = p["company_id"]
    listing = [{"impressions": _h(cid + month + "imp", 6000, 22000), "leads": _h(cid + month + "lead", 10, 60),
                "media_views": _h(cid + month + "mv", 300, 1400)}]
    # The skill reads the listing feed only when a warehouse is configured. The preview
    # answers the query itself, so it names a placeholder warehouse for this call only.
    saved = {k: os.environ.get(k) for k in ("BIGQUERY_PROJECT_ID", "BIGQUERY_DATASET_PROD")}
    os.environ.update({"BIGQUERY_PROJECT_ID": "preview", "BIGQUERY_DATASET_PROD": "preview"})
    try:
        return wr.assemble(wr.gather_without_hyly(ident, month, today=date(2026, 9, 14), city=p["city"], state=p["state"],
                                                  ils_query=lambda sql, params: listing))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


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
    month = (request.args.get("month") or "").strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or "2026-08"):
        return jsonify({"error": "invalid_month", "detail": "Use YYYY-MM."}), 400
    if months:
        # Default to the last full month (Round 4).
        month = month or (LAST_FULL_MONTH if LAST_FULL_MONTH in months else months[-1])
        if (company_id, month) not in reports:
            return jsonify({"error": "month_unavailable", "detail": f"No report for {month}."}), 404
        return jsonify(reports[(company_id, month)])
    p = prop(company_id)
    if not p:
        return jsonify({"error": "report_unavailable", "detail": "No report for this property."}), 404
    month = month or LAST_FULL_MONTH
    if month > LAST_FULL_MONTH:
        return jsonify({"error": "month_unavailable", "detail": "That month isn't over yet; the latest report is August 2026."}), 404
    data = _report_without_hyly(p, month)
    if data is None:
        return jsonify({"error": "report_unavailable", "detail": "No report for this property yet."}), 404
    return jsonify(data)


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


# ══ Round 5: spend sheet and property profile ═════════════════════════════════
# "Round 5 — Spend sheet and Property profile" in the build plan. Shapes come from
# the API's own pure helpers (skills.workspace_spend, skills.workspace_profile,
# community_brief.used_in), fed with preview facts, so the preview can't drift from
# tests/workspace_contract.py. Internal columns and fields are removed server-side
# for a client viewer and in preview-as-client, never hidden by the page.

R5_NOW = datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)
SPEND_AS_OF = "2026-09-15T06:10:00Z"
STAFF_NAME, CLIENT_NAME = "Dana R.", "Morgan Lee"


def _api_modules():
    sys.path.insert(0, str(REPO / "webhook-server"))
    import community_brief as cb
    from skills import workspace_common as wcm, workspace_profile as wpr, workspace_spend as wsp, workspace_cache
    return cb, wcm, wpr, wsp, workspace_cache


def _ws_error(exc) -> tuple[dict, int]:
    status = getattr(exc, "status", None) or getattr(exc, "code", None) or 400
    body = {"error": getattr(exc, "error", None) or getattr(exc, "message", None) or str(exc)}
    detail = getattr(exc, "detail", None)
    if detail:
        body["detail"] = detail
    return body, int(status)


# The four properties with August reports use those reports' vendor splits, so the
# sheet, plan.json and the reports agree. Google Ads splits into search and PMax;
# Apartments.com is the CoStar package and Zillow is billed separately, so neither is
# a channel column (spend_sheet.py) — channels + Zillow + the package equal the plan.
REPORT_SPLITS = {
    "18234410021": {"search": 1762, "pmax": 1077, "seo": 800, "paid_social": 243, "_costar": 200, "zillow_per_month": 556},
    "26136316506": {"search": 6900, "pmax": 4120, "paid_social": 1200, "_costar": 1000, "zillow_per_month": 1900},
    "18234410112": {"search": 1925, "paid_social": 300, "_costar": 3100, "zillow_per_month": 500},
    "18234410087": {"search": 3700, "pmax": 2200, "paid_social": 2100, "_costar": 3200},
}
MARKETS = {"Carrollton": "Dallas–Fort Worth", "Dallas": "Dallas–Fort Worth", "Plano": "Dallas–Fort Worth", "Frisco": "Dallas–Fort Worth",
           "Irving": "Dallas–Fort Worth", "McKinney": "Dallas–Fort Worth", "Denton": "Dallas–Fort Worth", "Fort Worth": "Dallas–Fort Worth",
           "Houston": "Houston", "Cypress": "Houston", "Austin": "Austin", "Round Rock": "Austin", "San Antonio": "San Antonio",
           "Tampa": "Tampa Bay", "Orlando": "Orlando", "Jacksonville": "Jacksonville", "Atlanta": "Atlanta", "Brighton": "Denver",
           "Denver": "Denver", "Aurora": "Denver", "Phoenix": "Phoenix", "Mesa": "Phoenix", "Charlotte": "Charlotte", "Nashville": "Nashville"}
MANAGERS = ("Dana R.", "Marcus T.", "Priya S.", "Jordan K.")


def _manager(p: dict) -> str:
    return "Dana R." if p["company_id"] in REPORT_SPLITS else MANAGERS[_h(p["company_id"] + "mgr", 0, len(MANAGERS) - 1)]


def _split_spend(p: dict) -> dict:
    """Monthly amounts for one property, summing exactly to the book's monthly spend.
    `_costar` is the Apartments.com package amount (shown as the package name)."""
    cid, total = p["company_id"], p["spend_last_month"]
    if cid in REPORT_SPLITS:
        return dict(REPORT_SPLITS[cid])
    if total is None:
        return {}
    out = {}
    if total >= 3000:
        out["seo"] = 800 if total >= 6000 else 500
    if _h(cid + "rep", 0, 2) == 0:
        out["reputation"] = 150
    if _h(cid + "soc", 0, 3) == 0:
        out["social_posting"] = 250
    if _h(cid + "geo", 0, 5) == 0:
        out["geofence"] = 400
    rest = total - sum(out.values())
    for key, share in (("pmax", 0.20), ("paid_social", 0.08), ("_costar", 0.16), ("zillow_per_month", 0.09)):
        if key == "zillow_per_month" and _h(cid + "zil", 0, 2) == 0:
            continue
        out[key] = int(round(rest * share / 5.0) * 5)
    out["search"] = total - sum(out.values())
    return out


def _package(amount) -> str:
    if not amount:
        return ""
    tier = "Basic" if amount <= 500 else "Silver" if amount <= 1500 else "Gold" if amount <= 3500 else "Platinum"
    return f"{tier} (${amount:,}/mo)"


def _spend_raw(p: dict) -> dict:
    """One row as spend_sheet.py builds it from HubSpot deals, line items and the company record."""
    cid, name = p["company_id"], p["name"]
    split = _split_spend(p)
    new = p["occupied"] is None
    channels = {k: v for k, v in split.items() if not k.startswith("_") and k != "zillow_per_month"}
    fee = None if new else (450 if cid == "18234410021" else int(max(350, round(sum(channels.values()) * 0.1 / 25) * 25)))
    raw = {"company_id": cid, "property_name": name, "market": MARKETS.get(p["city"], p["city"]), "marketing_manager": _manager(p),
           "ple_status": "Onboarding" if new else "Active", "deal_id": str(9_000_000 + int(cid[-6:])),
           "deal_name": f"{name} — FY26-27 digital", "deal_stage": "Contract sent" if new else "Closed won",
           "deal_amount": None if new else (sum(channels.values()) + (fee or 0)) * 12, "close_date": None if new else "2026-08-22",
           "quote_status": "Sent" if new else "Signed", "quote_title": f"{name} digital marketing FY26-27",
           "costar_package": _package(split.get("_costar")), "cx_bundle": bool(_h(cid + "cx", 0, 2) == 0) and not new,
           "zillow_per_month": split.get("zillow_per_month"), "zillow_per_lease": None, "mgmt_fee": fee}
    raw.update(channels)
    return raw


def spend_sheet(q: str = "", market: str = "", manager: str = "", status: str = "", sort: str = "property_name",
                direction: str = "asc", page: int = 1, page_size: int = 50, client: bool = False) -> dict:
    cb, wcm, wpr, wsp, cache = _api_modules()
    raw = [_spend_raw(p) for p in props()]
    original = cache.spend_rows
    cache.spend_rows = lambda: (copy.deepcopy(raw), SPEND_AS_OF)
    try:
        d = wsp.build_spend_sheet(book()["owner_email"], internal=not client, real_internal=True, q=q or None, market=market or None,
                                  manager=manager or None, status=status or None, sort=sort or None, direction=direction,
                                  page=page, page_size=page_size)
    finally:
        cache.spend_rows = original
    d["gaps"].append({"message": "Cedar Falls Commons is still onboarding, so it has no line items yet.", "field": "rows.values", "source": "spend_sheet"})
    if any(r["company_id"] == "18234410087" for r in d["rows"]) or d["count"] > len(d["rows"]):
        d["gaps"].append({"message": "Skye Reserve's $1,200 ApartmentList.com listing has no spend sheet column; the August report counts it in the $12,400 total.",
                          "field": "rows.values", "source": "spend_sheet"})
    return d


# ── Property profile ──────────────────────────────────────────────────────────

STALE_DAYS = 90
SOURCE_KEYS = {"the website": "site_scrape", "ApartmentIQ": "aptiq", "the company record": "hubspot_company", "the rent-tier estimate": "site_scrape"}


def _brief_sections():
    cb = _api_modules()[0]
    return cb.SECTIONS


LYV_PROFILE = {
    "name": ("LYV Broadway", "resolved", None, None, "the company record"),
    "address": ("1250 W Broadway St", "resolved", None, None, "the company record"),
    "city": ("Carrollton", "resolved", None, None, "the company record"),
    "state": ("TX", "resolved", None, None, "the company record"),
    "zip": ("75006", "resolved", None, None, "the company record"),
    "domain": ("lyvbroadway.com", "resolved", None, None, "the company record"),
    "voice_tier": (["lifestyle"], "override", STAFF_NAME, "2026-07-08T15:00:00Z", "the rent-tier estimate"),
    "unit_noun": (["apartment"], "resolved", None, None, "the website"),
    "advertised_name": ("LYV Broadway Apartments", "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
    "short_name": (None, "empty", None, None, None),
    "former_property_name": (None, "empty", None, None, None),
    "taglines": ("Your space. Your pace. LYV Broadway.", "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
    "brand_adjectives": ("Modern, social, walkable, easygoing", "override", CLIENT_NAME, "2026-08-12T16:30:00Z", None),
    "differentiators": ("Rooftop lounge with downtown views; a block from Downtown Carrollton Station on the DART Green Line; renovated one-bedrooms with in-home washers and dryers", "override", STAFF_NAME, "2026-08-12T16:30:00Z", None),
    "romance": ("Bright white quartz kitchens, oversized windows and a rooftop lounge that catches the sunset over Downtown Carrollton.", "override", CLIENT_NAME, "2026-03-02T15:00:00Z", None),
    "residents_love": ("The rooftop, the dog park and being able to take the train to work", "override", CLIENT_NAME, "2026-04-11T15:00:00Z", None),
    "residents_dislike": ("Garage gate is slow at rush hour; package room fills up around holidays", "override", STAFF_NAME, "2026-07-21T15:00:00Z", None),
    "target_resident": ("People who commute downtown by train and want a walkable neighborhood", "override", STAFF_NAME, "2026-07-21T15:00:00Z", None),
    "lifecycle_state": ("stabilized", "resolved", None, None, "ApartmentIQ"),
    "year_built": ("2019", "resolved", None, None, "ApartmentIQ"),
    "floor_plans": ([{"plan": "S1", "beds": 0, "baths": 1, "sqft": 548}, {"plan": "A1", "beds": 1, "baths": 1, "sqft": 712},
                     {"plan": "A2", "beds": 1, "baths": 1, "sqft": 804}, {"plan": "B1", "beds": 2, "baths": 2, "sqft": 1096}],
                    "resolved", None, None, "ApartmentIQ"),
    "unit_level_details": ("A1 homes on floors 4–6 have the renovated finishes; B1 corner homes have wraparound balconies", "override", STAFF_NAME, "2026-08-20T15:00:00Z", None),
    "property_amenities": ("Resort-style pool, fitness center, clubhouse, dog park", "override", STAFF_NAME, "2026-01-15T15:00:00Z", "the website"),
    "unit_features": (None, "empty", None, None, None),
    "neighborhood": ("Downtown Carrollton", "override", STAFF_NAME, "2026-07-08T15:00:00Z", "the website"),
    "nearby_neighborhoods": ("Old Downtown Carrollton, Josey Ranch, Farmers Branch", "override", CLIENT_NAME, "2026-08-12T16:30:00Z", None),
    "landmarks": ("Downtown Carrollton, Downtown Carrollton Station (DART Green Line), Josey Ranch Lake", "override", STAFF_NAME, "2026-07-08T15:00:00Z", "the website"),
    "neighborhood_highlights": (None, "empty", None, None, None),
    "nearby_employers": ("Carrollton-Farmers Branch ISD, Western Extrusions, Halliburton Carrollton", "resolved", None, None, "the website"),
    "competitors": ("The Reserve at Carrollton, Carrollton Station, Solana Carrollton", "override", STAFF_NAME, "2026-02-20T15:00:00Z", "ApartmentIQ"),
    "goals": ("Hold 93% leased through the winter; lift renewals to 55%", "override", CLIENT_NAME, "2026-05-30T15:00:00Z", None),
    "initiatives": ("A1 ad variants from the renovated photos; landing page that matches the Apartments.com listing", "override", STAFF_NAME, "2026-09-02T15:00:00Z", None),
    "challenges": ("One-bedrooms are the slowest homes to lease", "override", STAFF_NAME, "2026-09-02T15:00:00Z", None),
    "priorities": ("Cut lead-to-tour time; keep cost per lease under $350", "override", STAFF_NAME, "2026-09-02T15:00:00Z", None),
    "onsite_developments": ("Package lockers installed in August; EV chargers coming in October", "override", CLIENT_NAME, "2026-08-28T15:00:00Z", None),
    "local_partnerships": (None, "empty", None, None, None),
    "onsite_events": (None, "empty", None, None, None),
    "website_priorities": ("Floor plan pages first; add the pet FAQ", "override", STAFF_NAME, "2026-09-02T15:00:00Z", None),
    "marketing_budget": ("$4,638 a month", "override", STAFF_NAME, "2026-09-01T15:00:00Z", None),
    "pms": ("Yardi Voyager", "override", STAFF_NAME, "2025-11-03T15:00:00Z", None),
    "cms": ("RPM WordPress", "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
    "chatbot": (None, "empty", None, None, None),
    "website_last_updated": ("2026-08-14", "override", STAFF_NAME, "2026-08-14T15:00:00Z", None),
    "building_style": ("Mid-rise, podium parking", "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
    "asset_class": ("A-", "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
    "elise_ai": (None, "empty", None, None, None),
    "crm": ("Hyly", "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
    "host_name": ("RPM Web Hosting", "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
    "must_include": ("Pet-friendly; DART Green Line access", "override", CLIENT_NAME, "2026-05-02T15:00:00Z", None),
    "forbidden_phrases": ("Luxury (we're lifestyle tier); \"best in Carrollton\"", "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
    "motivations_considerations": ("Renters compare commute time and pet rules before anything else", "override", CLIENT_NAME, "2026-08-12T16:30:00Z", None),
    "excluded_neighborhoods": ("None", "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
    "client_expectations": ("Monthly owner call; cost per lease trend in every report", "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
    "tracking": ([{"channel": "Google Ads", "tag": "AW-5520194431", "status": "firing"}, {"channel": "GA4", "tag": "G-LYV24BRD", "status": "firing"},
                  {"channel": "Meta", "tag": "Pixel 814492", "status": "firing"}], "override", STAFF_NAME, "2026-08-14T15:00:00Z", None),
    "documents": (None, "empty", None, None, None),
}

PROFILES: dict = {}
PROFILE_UNDO: dict = {}
CHECKINS: dict = {}


def _reset_profiles():
    PROFILES.clear()
    PROFILE_UNDO.clear()
    CHECKINS.clear()


def _light_profile(p: dict) -> dict:
    """A consistent, lighter profile for the other 34 properties, from the book's own facts."""
    n, city, cid = p["name"], p["city"], p["company_id"]
    new = p["occupied"] is None
    stale = "2026-04-01T15:00:00Z" if _h(cid + "stale", 0, 2) == 0 else "2026-08-01T15:00:00Z"
    base = {
        "name": (n, "resolved", None, None, "the company record"), "city": (city, "resolved", None, None, "the company record"),
        "state": (p["state"], "resolved", None, None, "the company record"), "domain": (p["domain"], "resolved", None, None, "the company record"),
        "unit_noun": (["apartment"], "resolved", None, None, "the website"),
    }
    if new:
        return base
    base.update({
        "voice_tier": (["standard"] if (p["health"] or 0) < 65 else ["lifestyle"], "resolved", None, None, "the website"),
        "advertised_name": (n, "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
        "taglines": (f"Home, made easy at {n}.", "override", STAFF_NAME, stale, None),
        "property_amenities": ("Pool, fitness center, clubhouse", "resolved", None, None, "the website"),
        "neighborhood": (city, "resolved", None, None, "the website"),
        "lifecycle_state": ("lease_up" if _status(p) == "Lease-up" else "stabilized", "resolved", None, None, "ApartmentIQ"),
        "competitors": (f"The Reserve at {city}, {city} Station, Solana {city}", "resolved", None, None, "the website"),
        "goals": (f"Hold occupancy at {n} through the winter", "override", STAFF_NAME, stale, None),
        "pms": ("Yardi Voyager", "override", STAFF_NAME, "2026-07-08T15:00:00Z", None),
    })
    return base


def _as_string(f, value) -> str | None:
    wpr = _api_modules()[2]
    s = wpr.normalize_value(f, value)
    return s if s.strip() else None


def _seed_profile(cid: str) -> dict:
    """Per field: the override and resolved strings (as HubSpot holds them) and the audit entries (newest first)."""
    p = prop(cid)
    seed = LYV_PROFILE if cid == "18234410021" else _light_profile(p)
    state = {}
    for title, fields in _brief_sections():
        for f in fields:
            value, kind, by, at, source = seed.get(f.key, (None, "empty", None, None, None))
            text = _as_string(f, value)
            override = text if (kind == "override" and f.hs_override) else None
            resolved = text if (kind == "resolved" and f.hs_resolved) else None
            if kind == "override" and source and f.hs_resolved:
                resolved = "(the value from " + source + ")"
            entries = [{"at": at, "by": by, "action": "edited", "old_value": None, "new_value": text, "note": None}] if (kind == "override" and at) else []
            state[f.key] = {"override": override, "resolved": resolved, "entries": entries,
                            "pending": None, "suggestion": None, "review": None}
    if cid == "18234410021":
        state["taglines"]["pending"] = {"proposed_value": "Modern Carrollton living, minutes from the Green Line.", "by": CLIENT_NAME,
                                        "at": "2026-09-12T15:20:00Z", "item_id": "profile_update:0021-taglines"}
        state["property_amenities"]["suggestion"] = {
            "id": "sug-0021-amenities", "source": "site_scrape",
            "value": "Saltwater pool with cabanas, 24-hour fitness center, rooftop lounge, dog park and pet spa, package lockers, EV charging",
            "reason": "We found this amenity list on lyvbroadway.com on Sep 3. The profile's list is from January and leaves out the rooftop lounge and pet spa."}
        state["landmarks"]["suggestion"] = {
            "id": "sug-0021-landmarks", "source": "geo_claim",
            "value": "Downtown Carrollton, Downtown Carrollton Station (DART Green Line), Josey Ranch Lake, Sandy Lake Park",
            "reason": "ChatGPT and Perplexity answers name Sandy Lake Park near LYV Broadway, and the profile doesn't list it. Add it if it's right; dismiss it and we'll correct the answer."}
    return state


def _profile_state(cid: str) -> dict:
    if cid not in PROFILES:
        PROFILES[cid] = _seed_profile(cid)
    return PROFILES[cid]


def _resolved_source(f) -> str:
    wpr = _api_modules()[2]
    return wpr.resolved_source(f)


def _field_out(f, st: dict, client: bool) -> dict:
    cb, wcm, wpr, _, _ = _api_modules()
    last = st["entries"][0] if st["entries"] else None
    has_override, has_resolved = st["override"] is not None, st["resolved"] is not None
    if has_override:
        value = st["override"]
        prov = {"kind": "override", "by": last["by"] if last else None, "at": last["at"] if last else None, "source": "profile_edit",
                "overrides": _resolved_source(f) if has_resolved else None}
    elif has_resolved:
        value = st["resolved"]
        prov = {"kind": "resolved", "by": None, "at": None, "source": _resolved_source(f), "overrides": None}
    else:
        value, prov = None, {"kind": "empty", "by": None, "at": None, "source": None, "overrides": None}
    last_at = datetime.fromisoformat(last["at"].replace("Z", "+00:00")) if last and last.get("at") else None
    out = {"key": f.key, "label": f.label, "hint": f.hint or None, "type": f.type, "options": list(f.options or []), "section": f.section,
           "value": value, "provenance": prov, "ad_facing": cb.is_ad_facing(f.key), "used_in": cb.used_in(f.key), "internal": bool(f.internal),
           "editable": bool(f.hs_override),
           "stale": bool(prov["kind"] == "override" and (last_at is None or R5_NOW - last_at >= timedelta(days=STALE_DAYS))),
           "last_updated": last["at"] if last else None,
           "pending": copy.deepcopy(st["pending"]), "suggestion": None if st["pending"] else copy.deepcopy(st["suggestion"]),
           "review_outcome": None if st["pending"] else copy.deepcopy(st["review"])}
    if not client:
        text_field = f.type not in cb.TABLE_TYPES
        out["fair_housing_review"] = wcm.fair_housing_review(value) if (value and text_field) else None
    return out


def _checked_in_this_month(cid: str) -> bool:
    at = CHECKINS.get(cid)
    return bool(at and (at.year, at.month) == (R5_NOW.year, R5_NOW.month))


def profile(cid: str, client: bool = False) -> dict:
    cb, wcm, wpr, _, _ = _api_modules()
    p = prop(cid)
    state = _profile_state(cid)
    sections, views = [], []
    for title, fields in _brief_sections():
        out = [_field_out(f, state[f.key], client) for f in fields if not (client and f.internal)]
        if not out:
            continue
        views += out
        sections.append({"key": wpr.section_key(title), "title": title, "completeness": wpr.completeness(out), "fields": out})
    stale = [v["key"] for v in views if v["stale"]]
    stamps = [v["last_updated"] for v in views if v["last_updated"]]
    gaps = []
    if p["occupied"] is None:
        gaps.append({"message": f"{p['name']} is onboarding, so most of its profile hasn't been filled in yet.", "field": "sections"})
    if not any(v["suggestion"] and v["suggestion"]["source"] == "geo_claim" for v in views):
        gaps.append({"message": "No AI answers conflict with this profile yet, so there are no suggestions from them.", "field": "suggestion", "source": "geo_claims"})
    return {"property": {"company_id": cid, "name": p["name"], "last_updated": max(stamps) if stamps else None},
            "completeness": wpr.completeness(views),
            "checkin": {"due": bool(stale) and not _checked_in_this_month(cid), "stale_fields": stale},
            "sections": sections, "gaps": gaps}


def profile_completeness_pct(cid: str, client: bool = False) -> int | None:
    return profile(cid, client)["completeness"]["pct"]


def profile_completeness_metric(cid: str, client: bool = False) -> dict | None:
    pct = profile_completeness_pct(cid, client)
    return None if pct is None else {"value": pct, "source": "community_brief", "as_of": R5_NOW.isoformat().replace("+00:00", "Z"), "weighted": True}


def _field_def(key: str):
    return _api_modules()[0].FIELDS.get(key)


def _profile_update_item(cid: str, key: str) -> dict | None:
    cb, wcm, wpr, _, _ = _api_modules()
    st, f, p = _profile_state(cid)[key], _field_def(key), prop(cid)
    pend = st["pending"]
    if not pend or not f:
        return None
    current = st["override"] if st["override"] is not None else (st["resolved"] or "")
    proposed = pend["proposed_value"] or ""
    used = cb.used_in(key)
    used_labels = [cb.USED_IN_LABELS[u] for u in used if u in cb.USED_IN_LABELS]
    it = _base_item(
        p, "profile_update", f"{cid[-4:]}-{key}", category="content", lens="express", channels=["ads", "website"],
        title=f"Profile update: {f.label}",
        found=f"Proposed {f.label}: {proposed}" if proposed else f"Proposed clearing {f.label}",
        if_skip=f"The profile keeps: {current}" if current else f"{f.label} stays empty",
        receipts=[{"label": f"Proposed by {pend['by']}", "source": "workspace_profile_update", "as_of": pend["at"]}],
        evidence={"columns": ["Current value", "Proposed value"], "rows": [[current or None, proposed or None]], "more_count": 0},
        steps=[{"when": None, "label": "Written to the property profile", "channel": None, "kind": "auto", "status": "pending"},
               {"when": None, "label": "Reaches ads on the next daily feed sync", "channel": None, "kind": "queued", "status": "pending"}],
        why={"text": f"{pend['by']} edited an ad-facing field on {p['name']}'s profile. Ad-facing edits wait for RPM review before they reach ads.",
             "receipts": [{"label": f"Proposed by {pend['by']}", "source": "workspace_profile_update", "as_of": pend["at"]}]},
        for_whom={"text": "Used in: " + ", ".join(used_labels) if used_labels else "Not used in ads or reports", "questions": []},
        approving_does=[{"label": "Written to the property profile", "owner": "RPM Digital", "when": "When you approve"},
                        {"label": "Reaches ads on the next daily feed sync", "owner": "RPM Digital", "when": "The next day"}],
        owner=STAFF_NAME, status="to_do", needs_approval=True, client_visible=False, actions={"approve": True, "not_now": True},
        trail=[_trail(pend["at"], pend["by"], "Proposed this change", "internal")],
    )
    it["fair_housing_review"] = wcm.fair_housing_review(it["title"], it["found"], it["if_skip"])
    it["profile_update"] = {"field_key": key, "field_label": f.label, "current_value": current or None, "proposed_value": proposed or None,
                            "proposed_by": pend["by"], "proposed_at": pend["at"], "used_in": used, "diff": wpr.diff_lines(current, proposed),
                            "status": "pending", "decided_by": None, "decided_at": None, "reason": None}
    return it


def profile_update_items() -> list:
    out = []
    for p in props():
        for key, st in _profile_state(p["company_id"]).items():
            if st["pending"]:
                it = _profile_update_item(p["company_id"], key)
                if it:
                    out.append(it)
    return out


def profile_checkins() -> list:
    """Client dashboards' monthly check-in to-dos (skills.workspace_profile.checkin_card). Not approvals."""
    out = []
    for p in props():
        if p["occupied"] is None:
            continue
        ci = profile(p["company_id"], client=True)["checkin"]
        if ci["due"] and (p["company_id"] == "18234410021" or _h(p["company_id"] + "chk", 0, 4) == 0):
            n = len(ci["stale_fields"])
            out.append({"kind": "profile_checkin", "item_id": None, "company_id": p["company_id"], "title": "Review your property profile",
                        "subtitle": f"{p['name']} · {n} field{'s' if n != 1 else ''} not updated in 90+ days", "category": None, "stale_count": n})
    return out


def _save_field(cid: str, key: str, value, client: bool, by: str) -> tuple[dict, int]:
    """The API's edit flow (skills.workspace_profile.edit_field) against preview state."""
    cb, wcm, wpr, _, _ = _api_modules()
    if not prop(cid):
        return {"error": "Unknown property"}, 404
    f = cb.FIELDS.get(key)
    if f is None:
        return {"error": "Unknown field"}, 404
    if f.internal and client:
        return {"error": "This field is internal"}, 403
    if not f.hs_override:
        return {"error": f"{f.label} comes from HubSpot and isn't edited here"}, 400
    try:
        value = wpr.validate(f, wpr.normalize_value(f, value))
    except Exception as exc:  # WorkspaceError
        return _ws_error(exc)
    st = _profile_state(cid)[key]
    now = R5_NOW.isoformat().replace("+00:00", "Z")
    review = wcm.fair_housing_review(value) if (value and f.type not in cb.TABLE_TYPES) else None
    result = wpr.fair_housing_result(review, not client)
    if review and review["severity"] == "high":
        return {"field": _field_out(f, st, client), "outcome": "blocked", "fair_housing": result, "message": wpr.blocked_message(review)}, 200
    if client and cb.is_ad_facing(key):
        st["pending"] = {"proposed_value": value, "by": by, "at": now, "item_id": f"profile_update:{cid[-4:]}-{key}"}
        st["entries"].insert(0, {"at": now, "by": by, "action": "proposed", "old_value": st["override"], "new_value": value, "note": "Sent to RPM for review"})
        return {"field": _field_out(f, st, client), "outcome": "pending_review", "fair_housing": result,
                "message": "Sent to RPM for review. The current value stays live until RPM approves it."}, 200
    old = st["override"]
    st["override"] = value or None
    st["review"] = None
    st["entries"].insert(0, {"at": now, "by": by, "action": "edited", "old_value": old, "new_value": value, "note": None})
    return {"field": _field_out(f, st, client), "outcome": "saved", "fair_housing": result,
            # "live in ads tomorrow" only when an ad-facing field saves immediately; context and internal fields just save.
            "message": "Saved · live in ads tomorrow" if cb.is_ad_facing(key) else "Saved"}, 200


def apply_profile_decision(item_id: str, action: str, reason: str | None) -> dict | None:
    """Approve writes the proposed value (the audit names proposer and approver); reject leaves the live value."""
    if not item_id.startswith("profile_update:"):
        return None
    tail, key = item_id.split(":", 1)[1].split("-", 1)
    cid = next((p["company_id"] for p in props() if p["company_id"].endswith(tail)), None)
    it = _profile_update_item(cid, key) if cid else None
    if not it:
        return None
    st = _profile_state(cid)[key]
    pend = st["pending"]
    now = R5_NOW.isoformat().replace("+00:00", "Z")
    PROFILE_UNDO[item_id] = (cid, key, copy.deepcopy(st))
    if action == "approve":
        editor = f"{STAFF_NAME} (approved; proposed by {pend['by']})"
        st["entries"].insert(0, {"at": now, "by": editor, "action": "approved", "old_value": st["override"], "new_value": pend["proposed_value"], "note": f"Proposed by {pend['by']}"})
        st.update(override=pend["proposed_value"], pending=None,
                  review={"status": "approved", "at": now, "reason": None, "proposed_value": pend["proposed_value"]})
    else:
        labels = {"wrong_data": "Wrong read of the data", "already_handled": "Already handled", "not_priority": "Not a priority this month",
                  "discuss_on_call": "Discuss on our call"}
        st["entries"].insert(0, {"at": now, "by": STAFF_NAME, "action": "rejected", "old_value": None, "new_value": pend["proposed_value"], "note": labels.get(reason, reason)})
        st.update(pending=None, review={"status": "rejected", "at": now, "reason": labels.get(reason, reason), "proposed_value": pend["proposed_value"]})
    return it


def undo_profile_decision(item_id: str) -> dict | None:
    snap = PROFILE_UNDO.pop(item_id, None)
    if not snap:
        return None
    cid, key, st = snap
    _profile_state(cid)[key] = st
    return _profile_update_item(cid, key)


def _viewer_client() -> bool:
    return _as_client() or SWITCHES.get("client_editor", False)


@app.get("/api/workspace/spend-sheet")
def spend_sheet_route():
    a = request.args
    page, page_size = a.get("page") or "1", a.get("page_size") or "50"
    if not page.isdigit() or not page_size.isdigit():
        return jsonify({"error": "Invalid page"}), 400
    try:
        return jsonify(spend_sheet(a.get("q", ""), a.get("market", ""), a.get("manager", ""), a.get("status", ""), a.get("sort") or "property_name",
                                   "desc" if a.get("dir") == "desc" else "asc", int(page), int(page_size), client=_viewer_client()))
    except Exception as exc:  # WorkspaceError from the API's own validation
        body, status = _ws_error(exc)
        return jsonify(body), status


@app.get("/api/workspace/profile")
def profile_route():
    return jsonify(profile(_company_or_404(), client=_viewer_client()))


@app.patch("/api/workspace/profile/field")
def profile_field_route():
    body = request.get_json(silent=True) or {}
    cid = str(body.get("company_id") or "").strip()
    if not cid or not body.get("key"):
        return jsonify({"error": "company_id and key are required"}), 400
    client = _viewer_client()
    data, status = _save_field(cid, str(body["key"]), body.get("value"), client, CLIENT_NAME if client else STAFF_NAME)
    return jsonify(data), status


@app.post("/api/workspace/profile/checkin")
def profile_checkin_route():
    body = request.get_json(silent=True) or {}
    cid = str(body.get("company_id") or "").strip()
    if not prop(cid):
        return jsonify({"error": "company_id is required"}), 400
    confirmed = body.get("confirmed")
    if not isinstance(confirmed, list) or not all(isinstance(k, str) for k in confirmed):
        return jsonify({"error": "confirmed must be a list of field keys"}), 400
    client = _viewer_client()
    state, now = _profile_state(cid), R5_NOW.isoformat().replace("+00:00", "Z")
    done, skipped = [], []
    for k in dict.fromkeys(confirmed):
        f = _field_def(k)
        if f is None:
            skipped.append({"key": k, "reason": "Unknown field"})
            continue
        if f.internal and client:
            skipped.append({"key": k, "reason": "Internal field"})
            continue
        if state[k]["override"] is None and state[k]["resolved"] is None:
            skipped.append({"key": k, "reason": "No value to confirm"})
            continue
        current = state[k]["override"] if state[k]["override"] is not None else state[k]["resolved"]
        state[k]["entries"].insert(0, {"at": now, "by": CLIENT_NAME if client else STAFF_NAME, "action": "reviewed_no_change",
                                       "old_value": current, "new_value": current, "note": None})
        done.append(k)
    if done:
        CHECKINS[cid] = R5_NOW
    return jsonify({"company_id": cid, "confirmed": done, "skipped": skipped, "checkin": profile(cid, client)["checkin"]})


def _find_suggestion(sid: str):
    for p in props():
        for key, st in _profile_state(p["company_id"]).items():
            if st["suggestion"] and st["suggestion"]["id"] == sid:
                return p["company_id"], key, st
    return None


@app.post("/api/workspace/profile/suggestions/<sid>/accept")
def suggestion_accept_route(sid: str):
    found = _find_suggestion(sid)
    if not found:
        return jsonify({"error": "Suggestion not found"}), 404
    cid, key, st = found
    client = _viewer_client()
    data, status = _save_field(cid, key, st["suggestion"]["value"], client, CLIENT_NAME if client else STAFF_NAME)
    if status == 200 and data["outcome"] != "blocked":
        st["suggestion"] = None
        data["field"]["suggestion"] = None
    return jsonify(data), status


@app.post("/api/workspace/profile/suggestions/<sid>/dismiss")
def suggestion_dismiss_route(sid: str):
    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "reason is required"}), 400
    found = _find_suggestion(sid)
    if not found:
        return jsonify({"error": "Suggestion not found"}), 404
    cid, key, st = found
    now = R5_NOW.isoformat().replace("+00:00", "Z")
    st["entries"].insert(0, {"at": now, "by": CLIENT_NAME if _viewer_client() else STAFF_NAME, "action": "suggestion_dismissed",
                             "old_value": None, "new_value": st["suggestion"]["value"], "note": reason})
    st["suggestion"] = None
    return jsonify({"suggestion_id": sid, "dismissed": True, "reason": reason})


@app.get("/api/workspace/profile/history")
def profile_history_route():
    cid, key = _company_or_404(), request.args.get("key", "")
    f = _field_def(key)
    if not f:
        return jsonify({"error": "Unknown field"}), 404
    if f.internal and _viewer_client():
        return jsonify({"error": "This field is internal"}), 403
    kinds = {"edited": "edit", "reviewed_no_change": "reviewed", "approved": "approved", "proposed": "proposed", "rejected": "rejected"}
    entries = [{"at": e["at"], "by": e["by"], "kind": kinds[e["action"]], "old_value": e.get("old_value"), "new_value": e.get("new_value"),
                "note": e.get("note")} for e in _profile_state(cid)[key]["entries"] if e["action"] in kinds]
    return jsonify({"company_id": cid, "key": key, "label": f.label, "entries": entries, "gaps": []})



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Workspace preview against the 35-property preview book.")
    parser.add_argument("--port", type=int, default=PORT, help=f"port to listen on (default {PORT})")
    args = parser.parse_args()
    print(f"  workspace preview: http://127.0.0.1:{args.port}/workspace?t=preview")
    app.run(host="127.0.0.1", port=args.port, debug=False)
