"""Create HubSpot company properties for the onboarding/discovery lifecycle.

Adds properties needed for:
  - State-machine tracking (rpm_onboarding_status + _changed_at)
  - Community/Regional manager fallback contacts (auto-derived names from
    first.last@rpmliving.com email convention)
  - Gap-review email workflow (HubSpot Workflow trigger fields)

Run ONCE per environment. Idempotent — re-running skips existing properties.

The gap-review fields are READ by HubSpot Workflows (not by code that creates
tasks). The Flask app only writes rpm_gap_review_action; HubSpot's workflow
engine watches for the change and creates the owner task. See
docs/handoffs/HUBSPOT_GAP_REVIEW_WORKFLOW.md for the workflow spec.
"""

import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import HUBSPOT_API_KEY

API_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

# Onboarding lifecycle stages — keep in sync with state machine in
# routes/onboarding.py and the HubSpot workflow that watches transitions.
ONBOARDING_STATUS_OPTIONS = [
    {"label": "Not Started",             "value": "not_started",             "displayOrder": 0},
    {"label": "Intake Sent",             "value": "intake_sent",             "displayOrder": 1},
    {"label": "Intake In Progress",      "value": "intake_in_progress",      "displayOrder": 2},
    {"label": "Intake Complete",         "value": "intake_complete",         "displayOrder": 3},
    {"label": "Brief Drafting",          "value": "brief_drafting",          "displayOrder": 4},
    {"label": "Brief Review",            "value": "brief_review",            "displayOrder": 5},
    {"label": "Brief Confirmed",         "value": "brief_confirmed",         "displayOrder": 6},
    {"label": "Strategy In Build",       "value": "strategy_in_build",       "displayOrder": 7},
    {"label": "Awaiting Client Approval","value": "awaiting_client_approval","displayOrder": 8},
    {"label": "Live",                    "value": "live",                    "displayOrder": 9},
    {"label": "Escalated",               "value": "escalated",               "displayOrder": 10},
]

GAP_REVIEW_ACTION_OPTIONS = [
    {"label": "None",                    "value": "none",                    "displayOrder": 0},
    {"label": "Send CM Email",           "value": "send_cm_email",           "displayOrder": 1},
    {"label": "Send RM Email",           "value": "send_rm_email",           "displayOrder": 2},
    {"label": "Escalate",                "value": "escalate",                "displayOrder": 3},
]

GAP_REVIEW_STATUS_OPTIONS = [
    {"label": "None",                    "value": "none",                    "displayOrder": 0},
    {"label": "Sent",                    "value": "sent",                    "displayOrder": 1},
    {"label": "Responded",               "value": "responded",               "displayOrder": 2},
    {"label": "Overdue",                 "value": "overdue",                 "displayOrder": 3},
    {"label": "Escalated",               "value": "escalated",               "displayOrder": 4},
]


COMPANY_PROPERTIES = [
    # ── State machine ──────────────────────────────────────────────────────
    {
        "name": "rpm_onboarding_status",
        "label": "RPM Onboarding Status",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "companyinformation",
        "description": "Current stage in the onboarding/discovery → fulfillment lifecycle.",
        "options": ONBOARDING_STATUS_OPTIONS,
    },
    {
        "name": "rpm_onboarding_status_changed_at",
        "label": "RPM Onboarding Status Changed At",
        "type": "datetime",
        "fieldType": "date",
        "groupName": "companyinformation",
        "description": "ISO timestamp of the most recent onboarding-status transition. Used by HubSpot workflows for SLA breach detection (5-7 day target).",
    },

    # ── Fallback contacts (community + regional manager) ───────────────────
    # RPM email convention is first.last@rpmliving.com so the names are
    # auto-derived on form submit. The _name fields exist as overrides for
    # non-standard formats (hyphenated last names, middle initials, etc.).
    {
        "name": "community_manager_email",
        "label": "Community Manager Email",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": "Community Manager email — used for gap-review fallback when intake form quality is low.",
    },
    {
        "name": "community_manager_name",
        "label": "Community Manager Name",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": "Auto-derived from first.last@rpmliving.com; can be overridden manually.",
    },
    {
        "name": "regional_manager_email",
        "label": "Regional Manager Email",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": "Regional Manager email — escalation tier above the Community Manager.",
    },
    {
        "name": "regional_manager_name",
        "label": "Regional Manager Name",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": "Auto-derived from first.last@rpmliving.com; can be overridden manually.",
    },

    # ── Gap-review trigger fields (read by HubSpot Workflow, NOT by code
    # that creates tasks). The Flask app only writes rpm_gap_review_action;
    # HubSpot workflow engine creates the company-owner task in response.
    {
        "name": "rpm_gap_review_action",
        "label": "RPM Gap Review Action",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "companyinformation",
        "description": "Trigger value for the HubSpot workflow that creates the owner task. Set by the Flask app when a gap is detected; reset to 'none' when workflow completes.",
        "options": GAP_REVIEW_ACTION_OPTIONS,
    },
    {
        "name": "rpm_gap_review_token",
        "label": "RPM Gap Review Token",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": "Unique token appended to the /onboarding/gap-response/<token> link in the owner-drafted email. Single-use; expires 7 days after issue.",
    },
    {
        "name": "rpm_gap_review_questions",
        "label": "RPM Gap Review Questions",
        "type": "string",
        "fieldType": "textarea",
        "groupName": "companyinformation",
        "description": "JSON list of gap questions surfaced to the Community Manager. Set by the gap-review engine on intake submit.",
    },
    {
        "name": "rpm_gap_review_email_sent_at",
        "label": "RPM Gap Review Email Sent At",
        "type": "datetime",
        "fieldType": "date",
        "groupName": "companyinformation",
        "description": "Timestamp the company owner sent the gap-review email (logged via HubSpot email engagement on the company).",
    },
    {
        "name": "rpm_gap_review_response_at",
        "label": "RPM Gap Review Response At",
        "type": "datetime",
        "fieldType": "date",
        "groupName": "companyinformation",
        "description": "Timestamp the Community Manager submitted the response form. Used by HubSpot workflow to close the loop and stop reminder cadence.",
    },
    {
        "name": "rpm_gap_review_status",
        "label": "RPM Gap Review Status",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "companyinformation",
        "description": "Current state of the gap-review email workflow.",
        "options": GAP_REVIEW_STATUS_OPTIONS,
    },
]


def create_property(object_type, prop):
    """Create a single CRM property. Skips if it already exists."""
    url = f"{API_BASE}/crm/v3/properties/{object_type}"
    resp = requests.post(url, headers=HEADERS, json=prop)

    if resp.status_code == 201:
        print(f"  CREATED: {object_type}.{prop['name']}")
        return True
    elif resp.status_code == 409:
        print(f"  EXISTS:  {object_type}.{prop['name']}")
        return True
    else:
        print(f"  FAILED:  {object_type}.{prop['name']} ({resp.status_code})")
        try:
            print(f"    {resp.json().get('message', resp.text[:200])}")
        except Exception:
            print(f"    {resp.text[:200]}")
        return False


def main():
    if not HUBSPOT_API_KEY:
        print("ERROR: HUBSPOT_API_KEY not set")
        sys.exit(1)

    print("Creating onboarding-lifecycle Company properties...")
    for prop in COMPANY_PROPERTIES:
        create_property("companies", prop)

    print("\nDone.")
    print("\nNext: configure the HubSpot workflow that watches")
    print("      rpm_gap_review_action and creates the owner task —")
    print("      see docs/handoffs/HUBSPOT_GAP_REVIEW_WORKFLOW.md")


if __name__ == "__main__":
    main()
