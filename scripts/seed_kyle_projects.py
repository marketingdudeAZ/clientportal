"""Seed Kyle's ClickUp Projects list with current Active + On Radar projects.

Source: directive from Sam (2026-05-13) — each leader maintains a ClickUp
list of projects they own, split into "Active" (currently working) and
"On Radar" (known about, not actively building). Project list was derived
from Kyle's recent commits and handoff docs.

Active vs On Radar is encoded two ways for redundancy:
  - ClickUp tag: `active` or `on-radar` (group/filter by this in the list view)
  - Task name prefix: `[ACTIVE]` or `[ON RADAR]` (visible without filters)

Usage:
    # Dry run — prints what would be created, no API calls:
    python scripts/seed_kyle_projects.py

    # Actually create the tasks:
    python scripts/seed_kyle_projects.py --apply
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# Kyle's Projects list (rpm-marketing workspace 9011805260)
KYLE_PROJECTS_LIST_ID = "901111579531"

ACTIVE = [
    (
        "Property Brief Automation",
        "ClickUp → HubSpot deal + quote pipeline. PR #10 merged 2026-05-07; "
        "iterating on async dispatch, idempotency, RM/RVP signer routing, and "
        "Insertion Order template alignment.",
    ),
    (
        "/accounts Portfolio Dashboard",
        "Tracks 1, 1.5, and 3 live at digital.rpmliving.com/accounts. Track 2 "
        "(Fluency tag sync + Apt IQ ingestion) actively debugging; spend_sheet "
        "API consolidation through 2026-05-11.",
    ),
    (
        "Fluency Connector — Pipeline + Daily Refresh",
        "pipeline_sheet_writer, fluency-tag-sync, fluency_refresh_cron (daily "
        "Render Cron), URL scraper with Cloudflare bypass. Wires HubSpot + "
        "Apt IQ data into Fluency feed.",
    ),
    (
        "Community Brief — Live Editable Dashboard",
        "Rebuilt 2026-05-08 to match /accounts/property pattern. One row per "
        "field, dedicated property for Primary Motivations & Considerations, "
        "unit_noun aligned with HubSpot enum.",
    ),
    (
        "GTM Consent Mode v2 Bridge",
        "Three-part build (2026-05-05 to 05-07): (A) template transformer, "
        "(B) bulk push with rate limiting, (C) audit agent. Solves Default "
        "Consent State firing at Consent Initialization.",
    ),
    (
        "Quote Generator + Deal Creator (IO Process)",
        "Aligned with new Insertion Order process: auto-pin Marketing Services "
        "IO template, populate hs_sender_* fields, RM as Contact Signer, RVP "
        "as default contact, AM → deal/quote owner.",
    ),
    (
        "Onboarding AI Brief + Paid/SEO Keyword Split (Phase 4)",
        "Shipped to prod portal 19843861 on 2026-04-23. AI-drafted client "
        "brief from website + pitch + RFP, local keyword universe, Paid/SEO "
        "classifier routing to rpm_paid_keywords vs rpm_seo_keywords HubDB.",
    ),
]

ON_RADAR = [
    (
        "Blueprint Redesign — Intake-to-Fluency Mapping at Scale",
        "Multi-phase research project: pull every new-build ClickUp ticket "
        "from the last 4 months, diff brief vs shipped campaign, design new "
        "intake schema before touching the Blueprint. Three handoff docs in "
        "docs/handoffs/ — strategic, not in-flight.",
    ),
    (
        "SEO Phase 3 — Keyword Research + Market Trends Self-Serve",
        "Handoff plan written (docs/handoffs/SEO_PHASE3_KEYWORD_RESEARCH_HANDOFF.md). "
        "DataForSEO-backed seed expansion, bulk difficulty check, trend "
        "explorer, competitor gap. Tier-gated Basic+/Standard+. Build not "
        "yet started.",
    ),
    (
        "Fluency Outreach — Programmatic Ingestion Confirmation",
        "Email drafted (docs/handoffs/FLUENCY_OUTREACH_EMAIL.md) to Fluency "
        "CSM to confirm ingestion path + schema + Blueprint mapping before "
        "flipping on automated pushes. Not sent yet.",
    ),
    (
        "HubSpot Gap Review Workflow",
        "Spec ready (docs/handoffs/HUBSPOT_GAP_REVIEW_WORKFLOW.md) for "
        "HubSpot admin to build: when Flask flags intake gaps, set "
        "rpm_gap_review_action property → HubSpot workflow creates task on "
        "company owner with pre-drafted CM email + escalation.",
    ),
    (
        "SEO Workspace Rollout & Adoption",
        "Rebuilt SEO packaging (Local / Lite / Basic / Standard / Premium) "
        "with deliverables tied to tier. Code shipped; rollout to clients + "
        "AM enablement is the open work.",
    ),
]


def preview(projects: list[tuple[str, str]], section: str, tag: str) -> None:
    print(f"\n── {section} ({len(projects)}) " + "─" * (60 - len(section)))
    for name, desc in projects:
        title = f"[{section.upper()}] {name}"
        print(f"\n  • {title}")
        print(f"    tags: [{tag}]")
        print(f"    description: {desc}")


def apply(projects: list[tuple[str, str]], section: str, tag: str) -> tuple[int, int]:
    from clickup_client import create_task  # imported lazily so dry-run has no deps

    created = 0
    failed = 0
    for name, desc in projects:
        title = f"[{section.upper()}] {name}"
        result = create_task(
            KYLE_PROJECTS_LIST_ID,
            title,
            description=desc,
            tags=[tag],
        )
        if result and result.get("id"):
            logger.info("  ✓ created %s (id=%s)", title, result["id"])
            created += 1
        else:
            logger.error("  ✗ failed to create %s", title)
            failed += 1
    return created, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Kyle's ClickUp Projects list")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create the tasks. Without this flag, only previews.",
    )
    args = parser.parse_args()

    if args.apply and not os.getenv("CLICKUP_API_KEY"):
        print("ERROR: CLICKUP_API_KEY not set in environment", file=sys.stderr)
        return 1

    print(f"Target list: {KYLE_PROJECTS_LIST_ID} (Kyle's Projects)")
    print(f"Tasks to seed: {len(ACTIVE)} active + {len(ON_RADAR)} on-radar = {len(ACTIVE) + len(ON_RADAR)} total")

    if not args.apply:
        print("\n=== DRY RUN — no API calls will be made ===")
        preview(ACTIVE, "Active", "active")
        preview(ON_RADAR, "On Radar", "on-radar")
        print("\nRe-run with --apply to create these tasks in ClickUp.")
        return 0

    print("\n=== APPLY MODE — creating tasks in ClickUp ===")
    a_ok, a_fail = apply(ACTIVE, "Active", "active")
    r_ok, r_fail = apply(ON_RADAR, "On Radar", "on-radar")
    total_ok = a_ok + r_ok
    total_fail = a_fail + r_fail
    print(f"\nDone. Created {total_ok} task(s); {total_fail} failure(s).")
    return 0 if total_fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
