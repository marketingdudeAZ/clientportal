"""Daily onboarding-sheet watchdog — entry point for a Render Cron Job.

A property that sold has to land as a row on the "Accounts For Onboarding" tab
of the RPM Living Account Ingestion sheet, keyed by UUID, before the paid team
can build its accounts. The HubSpot workflow that writes the row (1887923359,
daily 6 AM CT) only enrolls companies that are RPM Managed + have a UUID +
were created after 2026-05-01, and several routine situations never meet that:

  * The portal record (has the UUID and the deal) and the BI record (has RPM
    Managed) are separate until someone merges them. Merged values do not
    trigger enrollment, and an unmerged pair never qualifies at all.
  * A returning property's deal lands on its old, dispositioned record. That
    record was created years ago, so the created-after filter excludes it
    forever, and its UUID matches the old "DISPO" row instead of a new one.
  * A row can exist but be missing the columns the workflow writes.

None of these shows up anywhere. A person notices days later, or doesn't.
So this job checks the result, not the path. A property is EXPECTED on the tab
when either:

  1. a live Sales Pipeline "New Account Build" deal has reached Ready to
     Launch or Closed won (the business has committed; the sheet must follow), or
  2. the company meets the workflow's own criteria, or has the
     `onboarding_sheet_written` flag set (the workflow said it wrote it).

For each expected property it raises one of:

  missing     — no row with the company's UUID, with the likely reason
                (unmerged BI duplicate, BI record not created yet, returning
                property on an old record, workflow did not run...)
  dispo_row   — the only row for the UUID is an old DISPO row
  incomplete  — the row is there but a workflow-written column is blank
                while HubSpot has the value
  no_company  — the deal has no company associated, so nothing can be written

A property is not reported until it is DUE: deal-driven checks give the team
until the earlier of (entered Ready to Launch + ONBOARDING_WATCH_GRACE_DAYS)
and (launch date - ONBOARDING_WATCH_LEAD_DAYS), and never before the first
6 AM workflow run after it qualified.

Every problem is one task on the ClickUp "Ingest Errors" list, keyed by the
[kind:id] suffix in its name, so it is filed once, commented on if the
diagnosis changes, and closed on the first run where it no longer appears.
If the watchdog itself cannot run (sheet unreadable, HubSpot down), it files a
single "cannot run" task instead of guessing.

Read-only against HubSpot and the sheet. It never writes a row or a flag.

Schedule: daily at 14:00 UTC (9 AM CDT / 8 AM CST, after the 6 AM workflow).
Command:
  python webhook-server/onboarding_watch_cron.py

Required env vars on the Render Cron Job service:
  HUBSPOT_API_KEY              — same value as the web service
  CLICKUP_API_KEY              — same value as the web service
  GOOGLE_SERVICE_ACCOUNT_JSON  — the portal SA; needs Viewer on the sheet
Optional:
  ONBOARDING_SHEET_ID              — defaults to the Account Ingestion sheet
  ONBOARDING_SHEET_GID             — defaults to the "Accounts For Onboarding" tab
  ONBOARDING_WATCH_ALERT_LIST_ID   — defaults to "Ingest Errors"
  ONBOARDING_WATCH_CLOSED_STATUS   — default "Closed"
  ONBOARDING_WATCH_SINCE           — default 2026-05-01 (deals created on/after)
  ONBOARDING_WATCH_GRACE_DAYS      — default 2
  ONBOARDING_WATCH_LEAD_DAYS       — default 5
  ONBOARDING_WATCH_MIN_ROWS        — default 500; fewer UUIDs read = broken read

Local use (never writes to ClickUp):
  python webhook-server/onboarding_watch_cron.py --dry-run
  python webhook-server/onboarding_watch_cron.py --dry-run --xlsx export.xlsx

Exit codes:
  0  healthy
  1  at least one problem (tasks filed)
  2  cannot run (configuration, sheet or HubSpot unreadable)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import requests

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass

import hubspot_client as hs  # noqa: E402

logger = logging.getLogger("onboarding_watch_cron")

CENTRAL = ZoneInfo("America/Chicago")
PORTAL_ID = "19843861"
CLICKUP_BASE = "https://api.clickup.com/api/v2"

SHEET_ID = os.environ.get("ONBOARDING_SHEET_ID", "1SIitz4djKVHr-gXrHu5VmA2PyX0zY__qqo9IKIrOJ0M")
SHEET_GID = int(os.environ.get("ONBOARDING_SHEET_GID", "209677137"))
ALERT_LIST_ID = os.environ.get("ONBOARDING_WATCH_ALERT_LIST_ID", "901115438807")
ALERT_CLOSED_STATUS = os.environ.get("ONBOARDING_WATCH_CLOSED_STATUS", "Closed")
ALERT_TAG = "onboarding-watch"

WORKFLOW_ID = "1887923359"
WORKFLOW_CUTOFF = "2026-05-01T00:00:00Z"   # the workflow's created-after filter
WORKFLOW_RUN_HOUR = 6                      # daily, Central
WORKFLOW_SLACK = timedelta(hours=2)        # time the run itself takes to finish

SALES_PIPELINE = "default"
READY_TO_LAUNCH = "266261426"
WATCHED_STAGES = (READY_TO_LAUNCH, "closedwon")
BI_SOURCE = "1358765"

# Sheet header -> HubSpot company property, for the columns the workflow writes.
# Everything else on the row (account name, label, timezone...) is filled by hand.
WORKFLOW_COLUMNS = {
    "address 1": "address",
    "city": "city",
    "state": "state",
    "zip": "zip",
    "domain": "domain",
}

COMPANY_PROPS = ["name", "uuid", "plestatus", "createdate", "domain",
                 "onboarding_sheet_written", *WORKFLOW_COLUMNS.values()]
DEAL_PROPS = ["dealname", "dealstage", "createdate", "launch_date__c",
              f"hs_v2_date_entered_{READY_TO_LAUNCH}", "hs_v2_date_entered_closedwon"]


class CannotRun(RuntimeError):
    """The watchdog cannot see what it needs to vouch for — never a clean run."""


@dataclass
class Issue:
    kind: str                 # missing | dispo_row | incomplete | no_company
    ref: str                  # company id (deal id for no_company)
    name: str
    headline: str
    detail: list[str] = field(default_factory=list)
    priority: int = 2         # ClickUp: 1 urgent, 2 high, 3 normal

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.ref}"


# ── Time ─────────────────────────────────────────────────────────────────────

def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.isdigit():
            return datetime.fromtimestamp(int(value) / 1000, timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def next_workflow_run(after: datetime) -> datetime:
    """The first 6 AM Central run strictly after `after`, plus its run time."""
    local = after.astimezone(CENTRAL)
    run = local.replace(hour=WORKFLOW_RUN_HOUR, minute=0, second=0, microsecond=0)
    if run <= local:
        run += timedelta(days=1)
    return run.astimezone(timezone.utc) + WORKFLOW_SLACK


def deal_due_at(deal: dict, grace_days: int, lead_days: int) -> datetime:
    p = deal.get("properties") or {}
    entered = (_ts(p.get(f"hs_v2_date_entered_{READY_TO_LAUNCH}"))
               or _ts(p.get("hs_v2_date_entered_closedwon"))
               or _ts(p.get("createdate")))
    due = entered + timedelta(days=grace_days)
    try:
        launch = date.fromisoformat((p.get("launch_date__c") or "")[:10])
        due = min(due, datetime(launch.year, launch.month, launch.day, tzinfo=CENTRAL)
                  - timedelta(days=lead_days))
    except ValueError:
        pass
    return max(next_workflow_run(entered), due)


# ── Reading ──────────────────────────────────────────────────────────────────

def rows_from_values(values: list[list]) -> dict[str, dict]:
    """Sheet values (header row first) -> {uuid: {lower-case header: cell}}.

    Columns are found by header name, so inserting a column does not move them.
    """
    if not values:
        raise CannotRun("the onboarding tab is empty")
    header = [str(h or "").strip().lower() for h in values[0]]
    required = {"uuid", "account name", *WORKFLOW_COLUMNS}
    absent = sorted(required - set(header))
    if absent:
        raise CannotRun(f"onboarding tab is missing column(s): {', '.join(absent)}")
    rows: dict[str, dict] = {}
    for raw in values[1:]:
        row = {h: str(v).strip() if v is not None else ""
               for h, v in zip(header, raw) if h}
        uuid = row.get("uuid", "")
        if uuid:
            # Duplicate UUIDs: keep the one that is not a DISPO row.
            if uuid in rows and not _is_dispo(rows[uuid]) and _is_dispo(row):
                continue
            rows[uuid] = row
    return rows


def read_sheet() -> dict[str, dict]:
    import gspread
    from google.oauth2.service_account import Credentials

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise CannotRun("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    info = json.loads(raw) if raw.strip().startswith("{") else json.load(open(raw))
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    try:
        ws = gspread.authorize(creds).open_by_key(SHEET_ID).get_worksheet_by_id(SHEET_GID)
        return rows_from_values(ws.get_all_values())
    except CannotRun:
        raise
    except Exception as e:  # noqa: BLE001
        raise CannotRun(f"cannot read the onboarding sheet as {info.get('client_email')}: "
                        f"{str(e)[:200]}") from e


def read_xlsx(path: str) -> dict[str, dict]:
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True)["Accounts For Onboarding"]
    return rows_from_values([list(r) for r in ws.iter_rows(values_only=True)])


def _search_all(obj: str, filters: list[dict], props: list[str]) -> list[dict]:
    out: list[dict] = []
    after = None
    while True:
        body = {"filterGroups": [{"filters": filters}], "properties": props, "limit": 100}
        if after:
            body["after"] = after
        d = hs._request("POST", f"https://api.hubapi.com/crm/v3/objects/{obj}/search",
                        json=body).json()
        out += d.get("results") or []
        after = ((d.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            return out


def read_hubspot(since: str) -> tuple[list[dict], dict[str, list[str]], dict[str, dict], set[str]]:
    """Deals, deal->company ids, companies by id, and the ids expected by the workflow."""
    deals = _search_all("deals", [
        {"propertyName": "pipeline", "operator": "EQ", "value": SALES_PIPELINE},
        {"propertyName": "dealname", "operator": "CONTAINS_TOKEN", "value": "*New Account Build*"},
        {"propertyName": "dealstage", "operator": "IN", "values": list(WATCHED_STAGES)},
        {"propertyName": "createdate", "operator": "GTE", "value": f"{since}T00:00:00Z"},
    ], DEAL_PROPS)
    # "[TEST] ..." deals are portal test runs that were left in the live pipeline.
    deals = [d for d in deals
             if not (d["properties"].get("dealname") or "").lstrip().upper().startswith("[TEST]")]

    links: dict[str, list[str]] = {}
    for i in range(0, len(deals), 100):
        d = hs._request("POST", "https://api.hubapi.com/crm/v4/associations/deals/companies/batch/read",
                        json={"inputs": [{"id": x["id"]} for x in deals[i:i + 100]]}).json()
        for r in d.get("results") or []:
            links[str(r["from"]["id"])] = [str(t["toObjectId"]) for t in r.get("to") or []]

    qualifying = _search_all("companies", [
        {"propertyName": "plestatus", "operator": "EQ", "value": "RPM Managed"},
        {"propertyName": "uuid", "operator": "HAS_PROPERTY"},
        {"propertyName": "createdate", "operator": "GTE", "value": WORKFLOW_CUTOFF},
    ], COMPANY_PROPS)
    flagged = _search_all("companies", [
        {"propertyName": "onboarding_sheet_written", "operator": "EQ", "value": "true"},
    ], COMPANY_PROPS)
    companies = {c["id"]: c["properties"] for c in qualifying + flagged}
    expected = set(companies)

    wanted = sorted({c for ids in links.values() for c in ids} - set(companies))
    for i in range(0, len(wanted), 100):
        d = hs._request("POST", "https://api.hubapi.com/crm/v3/objects/companies/batch/read",
                        json={"inputs": [{"id": c} for c in wanted[i:i + 100]],
                              "properties": COMPANY_PROPS}).json()
        companies.update({c["id"]: c["properties"] for c in d.get("results") or []})
    return deals, links, companies, expected


def _twin_searches(props: dict) -> list[tuple[str, list[dict]]]:
    """Ways to find the BI record for the same property, strongest first.

    Domain alone missed BI records that arrive without one: the BI record carries
    PLE status, market and street address, the portal record the UUID and the
    deal (9/24 Fluency ingestion miss). Street address + zip, then exact name,
    catch those. Name is last because Salesforce overwrites company names nightly.
    """
    searches = []
    domain = (props.get("domain") or "").strip().lower()
    if domain and domain != "rpmliving.com":
        searches.append(("domain", [{"propertyName": "domain", "operator": "EQ", "value": domain}]))
    address, zip_ = (props.get("address") or "").strip(), (props.get("zip") or "").strip()
    if address and zip_:
        searches.append(("address", [
            {"propertyName": "address", "operator": "EQ", "value": address},
            {"propertyName": "zip", "operator": "EQ", "value": zip_}]))
    name = (props.get("name") or "").strip()
    if name:
        searches.append(("name", [{"propertyName": "name", "operator": "EQ", "value": name}]))
    return searches


def find_twin(company_id: str, props: dict) -> dict | None:
    """The unmerged BI record for the same property: RPM Managed, no UUID, and the
    same domain, street address + zip, or name. `matched_on` says which."""
    for matched_on, filters in _twin_searches(props):
        for c in hs.search_companies(
                filters, properties=["name", "uuid", "plestatus", "hs_object_source_id"], limit=20):
            p = c.get("properties") or {}
            if c["id"] != company_id and p.get("plestatus") == "RPM Managed" and not p.get("uuid"):
                return {"id": c["id"], "matched_on": matched_on, **p}
    return None


def qualified_at(company_id: str, props: dict) -> datetime:
    """When the company last became workflow-eligible: the latest of its create
    date and the last change to plestatus or uuid."""
    d = hs._request("GET", f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
                    params={"propertiesWithHistory": "plestatus,uuid"}).json()
    stamps = [_ts(props.get("createdate"))]
    for hist in (d.get("propertiesWithHistory") or {}).values():
        if hist:
            stamps.append(_ts(hist[0].get("timestamp")))
    return max(s for s in stamps if s)


# ── Evaluating ───────────────────────────────────────────────────────────────

def _is_dispo(row: dict) -> bool:
    return "DISPO" in (row.get("account name") or "").upper()


def _company_url(cid: str) -> str:
    return f"https://app.hubspot.com/contacts/{PORTAL_ID}/record/0-2/{cid}"


def _deal_url(did: str) -> str:
    return f"https://app.hubspot.com/contacts/{PORTAL_ID}/record/0-3/{did}"


def diagnose_missing(cid: str, p: dict, twin: Callable[[str, dict], dict | None]) -> list[str]:
    """Why the workflow did not write this company — the reason decides the fix."""
    created = (p.get("createdate") or "")[:10]
    if not p.get("uuid"):
        return ["The company has no UUID, so there is nothing to key a row on. "
                "Check this is the portal record (it carries the UUID and the deal), "
                "not the BI record."]
    if p.get("plestatus") != "RPM Managed":
        t = twin(cid, p)
        if t:
            basis = {"address": "same street address + zip", "name": "same name — confirm "
                     "it is the same property before merging"}.get(t.get("matched_on"), "same domain")
            return [f"Unmerged BI duplicate: {t.get('name')} ({t['id']}) is RPM Managed "
                    f"with no UUID ({basis}) — {_company_url(t['id'])}",
                    "Merge it into this record (keep this one as primary so the UUID "
                    "survives). Merged values do not trigger the workflow, so after the "
                    "merge either add the row by hand or wait for the next 6 AM run."]
        if p.get("plestatus") == "Dispositioning":
            return ["Returning property: the deal is on the old, dispositioned record. "
                    "BI usually creates a new RPM Managed record for the takeover — "
                    "find it and decide whether Fluency reuses the old UUID or starts fresh."]
        return [f"PLE Status is \"{p.get('plestatus') or 'blank'}\", not RPM Managed. "
                "The BI record normally arrives 1–24 days after the portal record; if it "
                "has not, ask the BI team whether the property is in their feed."]
    if (p.get("createdate") or "") < WORKFLOW_CUTOFF:
        return [f"Record created {created}, before the workflow's 5/1/26 cutoff — the "
                "scheduled workflow will never pick it up. Add the row by hand."]
    if p.get("onboarding_sheet_written") == "true":
        return ["The Onboarding Sheet Written flag is set but no row has this UUID: the "
                "sheet write failed after the flag, or the row was deleted or re-keyed."]
    return [f"The company meets the workflow's criteria but workflow {WORKFLOW_ID} has "
            "not written it. Check the workflow is on and look at its last run."]


def evaluate(now: datetime, rows: dict[str, dict], deals: list[dict],
             links: dict[str, list[str]], companies: dict[str, dict], expected: set[str],
             grace_days: int, lead_days: int,
             twin: Callable[[str, dict], dict | None],
             became_eligible: Callable[[str, dict], datetime]) -> list[Issue]:
    issues: dict[str, Issue] = {}

    def add(issue: Issue) -> None:
        issues.setdefault(issue.key, issue)

    def check_row(cid: str, p: dict, context: list[str]) -> bool:
        """Row-level checks for a company whose UUID is on the tab. False if absent."""
        row = rows.get(p.get("uuid") or "")
        if not row:
            return False
        blank = [h for h, prop in WORKFLOW_COLUMNS.items()
                 if not row.get(h) and (p.get(prop) or "").strip()]
        if blank:
            add(Issue("incomplete", cid, p.get("name") or cid,
                      f"row missing {', '.join(blank)}",
                      [f"The row for UUID {p['uuid']} has blank {', '.join(blank)} while "
                       "HubSpot has values. Fill them from the company record."]
                      + context + [_company_url(cid)], priority=3))
        return True

    # 1. Deals that committed to launching.
    for deal in deals:
        dp = deal["properties"]
        due = deal_due_at(deal, grace_days, lead_days)
        if now < due:
            continue
        stage = "Ready to Launch" if dp.get("dealstage") == READY_TO_LAUNCH else "Closed won"
        context = [f"Deal: {dp.get('dealname')} ({stage}, launch "
                   f"{(dp.get('launch_date__c') or 'not set')[:10]}) — {_deal_url(deal['id'])}"]
        cids = [c for c in links.get(deal["id"], []) if c in companies]
        if not cids:
            add(Issue("no_company", deal["id"], dp.get("dealname") or deal["id"],
                      "deal has no company",
                      ["The deal has no associated company, so no row can be written. "
                       "Associate the portal company record."] + context))
            continue
        present = [c for c in cids if rows.get(companies[c].get("uuid") or "")]
        live = [c for c in present if not _is_dispo(rows[companies[c]["uuid"]])]
        if live:
            for c in live:
                check_row(c, companies[c], context)
            continue
        cid = present[0] if present else next(
            (c for c in cids if companies[c].get("uuid")), cids[0])
        p = companies[cid]
        if present:
            row = rows[p["uuid"]]
            add(Issue("dispo_row", cid, p.get("name") or cid, "only an old DISPO row",
                      [f"UUID {p['uuid']} matches the dispositioned row \"{row.get('account name')}\". "
                       "This is a returning property: decide whether Fluency reuses the old "
                       "UUID (update that row) or the takeover gets a fresh record and row."]
                      + diagnose_missing(cid, p, twin)[:1] + context + [_company_url(cid)]))
        else:
            add(Issue("missing", cid, p.get("name") or cid, "not on Accounts For Onboarding",
                      diagnose_missing(cid, p, twin) + context + [_company_url(cid)]))

    # 2. Companies the workflow itself should have written.
    for cid in sorted(expected):
        p = companies[cid]
        if check_row(cid, p, []) or f"missing:{cid}" in issues or f"dispo_row:{cid}" in issues:
            continue
        flagged = p.get("onboarding_sheet_written") == "true"
        if not flagged and now < next_workflow_run(became_eligible(cid, p)):
            continue
        add(Issue("missing", cid, p.get("name") or cid, "not on Accounts For Onboarding",
                  diagnose_missing(cid, p, twin) + [_company_url(cid)]))

    return sorted(issues.values(), key=lambda i: (i.priority, i.name.lower()))


# ── ClickUp: one task per problem on "Ingest Errors" ─────────────────────────

def _cu(method: str, path: str, **kw) -> dict | None:
    try:
        r = requests.request(method, f"{CLICKUP_BASE}/{path}",
                             headers={"Authorization": os.environ["CLICKUP_API_KEY"]},
                             timeout=20, **kw)
    except requests.RequestException as e:
        logger.error("ClickUp %s %s failed: %s", method, path, e)
        return None
    if not r.ok:
        logger.error("ClickUp %s %s -> %s %s", method, path, r.status_code, r.text[:200])
        return None
    try:
        return r.json()
    except ValueError:
        return {}


def _key_of(task_name: str) -> str | None:
    if task_name.endswith("]") and "[" in task_name:
        return task_name[task_name.rindex("[") + 1:-1]
    return None


def open_tasks() -> dict[str, dict]:
    """This watchdog's open tasks on the alert list, by key."""
    out: dict[str, dict] = {}
    page = 0
    while True:
        d = _cu("GET", f"list/{ALERT_LIST_ID}/task",
                params={"include_closed": "false", "archived": "false",
                        "tags[]": ALERT_TAG, "page": page})
        if d is None:
            raise CannotRun("cannot list tasks on the ClickUp alert list")
        for t in d.get("tasks") or []:
            # include_closed=false still returns "done"-type statuses.
            if (t.get("status") or {}).get("type") in ("done", "closed"):
                continue
            key = _key_of(t.get("name") or "")
            if key:
                out[key] = t
        if d.get("last_page", True) or not d.get("tasks"):
            return out
        page += 1


def _body(issue: Issue, stamp: str) -> str:
    return "\n".join(issue.detail + [
        "",
        f"Found by the onboarding watchdog at {stamp} UTC. It closes this task on "
        "the first run where the property is on the tab and complete.",
        "Runbook: docs/RUNBOOKS/onboarding-watchdog.md",
    ])


def sync_tasks(issues: list[Issue], stamp: str) -> None:
    existing = open_tasks()
    for issue in issues:
        body = _body(issue, stamp)
        task = existing.pop(issue.key, None)
        if task is None:
            t = _cu("POST", f"list/{ALERT_LIST_ID}/task", json={
                "name": f"{issue.name} — {issue.headline} [{issue.key}]"[:250],
                "description": body, "priority": issue.priority, "tags": [ALERT_TAG]})
            logger.error("filed %s: %s", issue.key, (t or {}).get("url"))
        elif issue.detail[0] not in (task.get("description") or task.get("text_content") or ""):
            # Same property, new reason (e.g. the merge happened; now it is waiting
            # on the workflow). Say so once instead of every day.
            _cu("POST", f"task/{task['id']}/comment",
                json={"comment_text": "The diagnosis changed:\n\n" + body})
            _cu("PUT", f"task/{task['id']}", json={"description": body})
            logger.error("updated %s", issue.key)
        else:
            logger.error("still open %s: %s", issue.key, task.get("url"))
    for key, task in existing.items():
        _cu("POST", f"task/{task['id']}/comment",
            json={"comment_text": f"Resolved: the run at {stamp} UTC no longer finds this problem."})
        _cu("PUT", f"task/{task['id']}", json={"status": ALERT_CLOSED_STATUS})
        logger.info("closed %s", key)


CANNOT_RUN_KEY = "watchdog:cannot-run"


def report_cannot_run(reason: str, stamp: str) -> None:
    task = open_tasks().get(CANNOT_RUN_KEY) if os.environ.get("CLICKUP_API_KEY") else None
    body = (f"The onboarding watchdog could not run at {stamp} UTC: {reason}\n\n"
            "Until this is fixed nothing is checking that sold properties reach the "
            "Accounts For Onboarding tab.\nRunbook: docs/RUNBOOKS/onboarding-watchdog.md")
    if task:
        _cu("POST", f"task/{task['id']}/comment", json={"comment_text": body})
    else:
        _cu("POST", f"list/{ALERT_LIST_ID}/task", json={
            "name": f"Onboarding watchdog cannot run [{CANNOT_RUN_KEY}]",
            "description": body, "priority": 1, "tags": [ALERT_TAG]})


# ── Entry point ──────────────────────────────────────────────────────────────

def run(now: datetime, xlsx: str | None = None) -> list[Issue]:
    rows = read_xlsx(xlsx) if xlsx else read_sheet()
    floor = int(os.environ.get("ONBOARDING_WATCH_MIN_ROWS", "500"))
    if len(rows) < floor:
        raise CannotRun(f"read only {len(rows)} UUIDs from the tab (floor {floor}) — "
                        f"a broken read, not {len(rows)} missing properties")
    try:
        deals, links, companies, expected = read_hubspot(
            os.environ.get("ONBOARDING_WATCH_SINCE", "2026-05-01"))
    except hs.HubSpotError as e:
        raise CannotRun(f"HubSpot read failed: {e}") from e
    logger.info("tab %d UUIDs; %d launch-committed deals; %d workflow-expected companies",
                len(rows), len(deals), len(expected))
    return evaluate(now, rows, deals, links, companies, expected,
                    int(os.environ.get("ONBOARDING_WATCH_GRACE_DAYS", "2")),
                    int(os.environ.get("ONBOARDING_WATCH_LEAD_DAYS", "5")),
                    find_twin, qualified_at)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print problems; write nothing")
    ap.add_argument("--xlsx", help="read the tab from a local .xlsx export instead of the sheet")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s onboarding_watch_cron: %(message)s")

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    alerting = not args.dry_run
    if alerting and not os.environ.get("CLICKUP_API_KEY"):
        logger.error("CLICKUP_API_KEY not set on this cron service — cannot alert")
        return 2
    try:
        if not os.environ.get("HUBSPOT_API_KEY"):
            raise CannotRun("HUBSPOT_API_KEY not set")
        issues = run(now, args.xlsx)
    except CannotRun as e:
        logger.error("cannot run: %s", e)
        if alerting:
            try:
                report_cannot_run(str(e), stamp)
            except Exception:  # noqa: BLE001
                logger.exception("alerting failed")
        return 2

    for i in issues:
        logger.error("problem [%s] %s — %s", i.key, i.name, i.headline)
        for line in i.detail:
            logger.error("    %s", line)
    if alerting:
        try:
            sync_tasks(issues, stamp)
        except Exception:  # noqa: BLE001
            logger.exception("alerting failed")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
