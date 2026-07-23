"""Curated per-type ticket-form specs — the source of truth for the portal's
Request Work forms, authored from RPM's real ClickUp intake forms.

Why this exists: ClickUp shares custom fields across a Space, so
`GET /list/{id}/field` returns the whole 90+ field union, not the curated
subset a given intake FORM shows. That produced unusable mega-forms. Instead we
pin the exact fields/order/sections/helper here (from the live forms) and only
resolve each field's *options* live from ClickUp so they never drift.

Each field entry:
  name        ClickUp custom-field name (matched case-insensitively to resolve
              the field id + live options). Identity fields use a synthetic name.
  role        "prefill" — the portal fills it from the property record and hides
              it from the client; "client" — shown on the form.
  section     Section header it renders under ("" = top, no header).
  required    Whether the client must fill it.
  helper      Helper text shown under the label (from the real form).
  type        Optional input override when the field isn't a ClickUp custom
              field (e.g. identity fields we render ourselves).
  profile_key community_brief field key this writes back to (closed loop). None
              = ticket-only, never touches the property profile.
  show_if     (controlling_field_name, [values]) — render only when that field
              holds one of the values. Used for General's Category follow-ups.

PROFILE_UPDATING_TYPES carry the "this updates your property profile" notice.
"""

from __future__ import annotations

# Identity fields the portal pre-fills from the property record. Rendered by us,
# hidden from the client, and stamped onto the ClickUp task. `src` is the
# HubSpot company property (or "derive:am" for the region→AM lookup).
PREFILL = {
    "Property Name":   {"src": "name"},
    "Property Code":   {"src": "property_code"},
    "Property URL":    {"src": "website"},
    "Market":          {"src": "market"},
    "Digital Region":  {"src": "market"},
    "Account Manager": {"src": "derive:am"},
    "Requested By":    {"src": "submitter"},
}

# Region → Account Manager, from the AM↔Region table on every form.
AM_BY_REGION = {
    "austin": "Dustin", "atlanta": "Dustin", "rpmi": "Dustin", "southeast (tn)": "Dustin",
    "florida": "Juliana", "houston": "Juliana", "mid-atlantic": "Juliana",
    "midwest": "Juliana", "san antonio": "Juliana",
    "dallas": "Logan", "mountain": "Logan", "west": "Logan",
}


def _f(name, role="client", section="", required=False, helper="",
       type=None, profile_key=None, show_if=None):
    return {"name": name, "role": role, "section": section, "required": required,
            "helper": helper, "type": type, "profile_key": profile_key, "show_if": show_if}


# Shared identity header — pre-filled on every form (client never sees these).
def _identity(region_label="Digital Region"):
    return [
        _f("Property Name", "prefill", required=True),
        _f("Requested By", "prefill", required=True),
        _f("Property Code", "prefill", required=True),
        _f("Property URL", "prefill"),
        _f(region_label, "prefill", required=True),
        _f("Account Manager", "prefill", required=True),
    ]


# ── The 7 built forms (Ad Updates pending its list id) ───────────────────────

FORM_SPECS: dict[str, dict] = {

    "new_account_build": {
        "title": "New Account Onboarding",
        "intro": "For new properties coming online — not budget changes. Spend "
                 "levels by channel should already be determined before you submit.",
        "updates_profile": True,
        "fields": [
            *_identity(),
            _f("Earliest Launch Date", required=True, section="Property Information",
               helper="Choose 'Future Date' if launch is more than 7 days out. ASAP defaults to our SLA."),
            _f("Property Address", required=True, section="Property Information"),
            _f("Property Type", required=True, section="Property Information"),
            _f("Property Email Address", required=True, section="Property Information"),
            _f("Property Website Platform", required=True, section="Property Information",
               helper="e.g. RentCafe, Jonah, Perq, Knock, RealPage, Fervor.", profile_key="cms"),
            _f("Property Website Bot", required=True, section="Property Information",
               helper="e.g. CRMIQ, Knock, Funnel, Client Hosted, None.", profile_key="chatbot"),
            _f("Property Status", required=True, section="Property Information", profile_key="lifecycle_state"),
            _f("Client / Portfolio Name", required=True, section="Property Information"),
            _f("RM's Email", required=True, section="Property Information", helper="Insertion Order (IO) approver."),
            _f("RVP's Email", required=True, section="Property Information", helper="CC'd on the IO email for visibility."),
            # Property Details — these feed the property profile (closed loop).
            _f("Top Amenities / Selling Points", required=True, section="Property Details",
               helper="Top features / lifestyle benefits that set the property apart. List as many as possible.",
               profile_key="differentiators"),
            _f("Local Hotspots / Attractions (List at least 4)", required=True, section="Property Details",
               helper="4–5 most pertinent local attractions by name (employers, malls, parks, hospitals, beaches).",
               profile_key="landmarks"),
            _f("Campaign Focus", required=True, section="Property Details",
               helper="Main goal of the campaign. Any specific floor plans / layouts to focus on?",
               profile_key="goals"),
            _f("Top Competitors", required=True, section="Property Details",
               helper="Three local competitors and their website URLs.", profile_key="competitors"),
            _f("Local / Colloquial Terms (SoCal, Old Town, etc.)", required=True, section="Property Details"),
            _f("Keywords to Target", required=True, section="Property Details"),
            _f("Voice & Tone", section="Property Details",
               helper="What brand voice should the campaign reflect?", profile_key="brand_adjectives"),
            _f("Target Audience", required=True, section="Property Details",
               helper="Target demographic — who are we writing to?", profile_key="target_resident"),
            _f("Property Floor Plan Type", required=True, section="Property Details",
               helper="Select all floor-plan types offered at this community."),
            _f("SharePoint URL", section="Property Details",
               helper="Link to the exact image/folder of approved assets. Leave blank if none."),
            # Account Budget — per-channel $ amounts (currency).
            _f("Paid Search", section="Account Budget · Paid"),
            _f("Google Display", section="Account Budget · Paid",
               helper="Creative required for this build. If not completed, submit a Creative request."),
            _f("PMax", section="Account Budget · Paid"),
            _f("Paid Social", section="Account Budget · Paid"),
            _f("Geofence", section="Account Budget · Paid", helper="$250 one-time setup fee."),
            _f("Retargeting", section="Account Budget · Paid"),
            _f("TikTok", section="Account Budget · Paid"),
            _f("Programmatic", section="Account Budget · Paid"),
            _f("SEO - Onboard", section="Account Budget · SEO"),
            _f("Email Drip Campaign - New Build", section="Account Budget · Email & Social",
               helper="$125/mo + $225 one-time setup fee."),
            _f("eBlast", section="Account Budget · Email & Social", helper="$700 one-time fee."),
            _f("Organic Social", section="Account Budget · Email & Social"),
            _f("Is there anything else that would be helpful for us to know?",
               helper="Neighborhoods to avoid, notes from client calls, anything else."),
        ],
    },

    "budget_update": {
        "title": "Budget Changes",
        "intro": "For properties already online. Enter budgets only for the "
                 "channels you want added or removed. A 20% or $250 (whichever is "
                 "greater) management fee applies to paid channels.",
        "updates_profile": True,
        "fields": [
            *_identity(),
            _f("Earliest Launch Date", required=True, section="Property Information",
               helper="Choose 'Future Date' if launch is more than 7 days out."),
            _f("RM's Email", required=True, section="Property Information", helper="Insertion Order (IO) approver."),
            _f("RVP's Email", required=True, section="Property Information", helper="CC'd on the IO email."),
            # Updatable property details (closed loop) — only if changed.
            _f("Top Amenities", section="Property Details (update only if changed)",
               helper="Top features / lifestyle benefits that set the property apart.", profile_key="differentiators"),
            _f("Local Hotspots / Attractions", section="Property Details (update only if changed)",
               helper="4–5 most pertinent local attractions by name.", profile_key="landmarks"),
            _f("Campaign Focus", section="Property Details (update only if changed)",
               helper="Main goal of the campaign; floor plans to focus on.", profile_key="goals"),
            _f("Top Competitors", section="Property Details (update only if changed)",
               helper="Three local competitors and their URLs.", profile_key="competitors"),
            _f("Local / Colloquial Terms", section="Property Details (update only if changed)"),
            _f("Keywords", section="Property Details (update only if changed)"),
            _f("Target Audience", section="Property Details (update only if changed)",
               helper="Target demographic — who are we writing to?", profile_key="target_resident"),
            _f("SharePoint URL", required=True, section="Property Details (update only if changed)",
               helper="Link to the exact image/folder the team should pull from."),
            # Channel action dropdowns (New Channel / Increase / Decrease / Cancel).
            _f("Paid Search", section="Account Budget · Paid"),
            _f("Google Display", section="Account Budget · Paid", helper="Creative required; else submit a Creative request."),
            _f("P Max", section="Account Budget · Paid", helper="Images + logo needed to launch."),
            _f("Paid Social", section="Account Budget · Paid"),
            _f("Geofence", section="Account Budget · Paid", helper="Creative required; else submit a Creative request."),
            _f("Retargeting", section="Account Budget · Paid"),
            _f("TikTok", section="Account Budget · Paid"),
            _f("Programmatic", section="Account Budget · Paid"),
            _f("SEO - Budget Update", section="Account Budget · SEO"),
            _f("Email Drip Campaign - Budget Update", section="Account Budget · Email & Social"),
            _f("eBlast", section="Account Budget · Email & Social", helper="$700 one-time fee."),
            _f("Organic Social - Budget Update", section="Account Budget · Email & Social", helper="$500 one-time setup fee."),
            _f("Is there anything else that would be helpful for us to know?"),
        ],
    },

    "campaign_review": {
        "title": "Digital Marketing Review",
        "intro": "For a property experiencing lead-generation issues or a full "
                 "review of its digital marketing performance. For a budget "
                 "recommendation, use a General Ticket instead.",
        "updates_profile": False,
        "fields": [
            _f("Property Name", "prefill", required=True),
            _f("Requested By", "prefill", required=True),
            _f("Account Manager", "prefill", required=True),
            _f("Digital Region", "prefill", required=True),
            _f("Property Code", "prefill", required=True, section="Property Overview"),
            _f("Occupancy", required=True, section="Property Overview", helper="Current occupancy %."),
            _f("Occupancy Trend", required=True, section="Property Overview"),
            _f("Reason for Request", required=True, section="Property Overview",
               helper="Why is this property in review? Tours down? Unqualified leads? Construction delays? Be specific."),
            _f("What is the current pricing compared to comps?", required=True, section="Property Overview",
               helper="Current pricing vs the comp set. You can attach a screenshot below."),
            _f("Please provide the current Digital budget breakdown for this property.", required=True,
               section="Property Overview", helper="List channels and spend. Management fees not needed."),
            _f("Due Diligence Done", required=True, section="Property Overview",
               helper="What's already been looked into? Ops checked for onsite issues? Knock pending applications?"),
            _f("If there are any files related to your request, please upload those here.",
               helper="Screenshots or supporting files."),
        ],
    },

    "new_business": {
        "title": "New Business",
        "intro": "For a comparative analysis of a prospective property against "
                 "local competitors when pitching new business.",
        "updates_profile": False,
        "fields": [
            _f("Property Name", "prefill", required=True),
            _f("Requested By", "prefill", required=True),
            _f("Requested Due Date", required=True,
               helper="Preferred due date. If less than 3 days out, we default to SLA."),
            _f("Pitch Date", required=True),
            _f("Account Manager", "prefill", required=True),
            _f("Digital Region", "prefill", required=True),
            _f("Property URL", required=True, section="Property Information"),
            _f("Property Address", required=True, section="Property Information"),
            _f("Property Status", required=True, section="Property Information"),
            _f("Property Type", required=True, section="Property Information"),
            _f("Unit Count", required=True, section="Property Information"),
            _f("What digital tactics are they currently running?", required=True, section="Property Information"),
            _f("Metrics that Matter", required=True, section="Property Information",
               helper="What metrics matter to the ownership group?"),
            _f("Competitor Website #1", section="Competitor Information",
               helper="Provide competitor property URLs to compare against."),
            _f("Competitor Website #2", section="Competitor Information"),
            _f("Competitor Website #3", section="Competitor Information"),
            _f("Is there any other information you think we should know?"),
        ],
    },

    "rebrand": {
        "title": "Rebrands",
        "intro": "For a property undergoing a rebrand. Include all relevant "
                 "details so everything stays connected and tracking properly.",
        "updates_profile": True,
        "fields": [
            _f("Property Name", "prefill", required=True),   # current name
            _f("Requested By", "prefill", required=True),
            _f("Date of Rebrand Launch", required=True),
            _f("Account Manager", "prefill", required=True),
            _f("Digital Region", "prefill", required=True),
            _f("Property Code", "prefill", required=True, section="Current Property Information"),
            _f("Property URL", "prefill", section="Current Property Information"),
            _f("Rebranded Property Name", required=True, section="Rebrand Information",
               profile_key="advertised_name"),
            _f("Rebrand URL", required=True, section="Rebrand Information",
               helper="The URL once the rebrand takes place."),
            _f("New Property Email", required=True, section="Rebrand Information"),
            _f("Website Platform", required=True, section="Rebrand Information",
               helper="Is the website platform changing? e.g. switching from Jonah to RentCafe."),
            _f("Link to Rebrand Creative", required=True, section="Rebrand Information"),
            _f("Is there any other information we need to know?"),
        ],
    },

    "dispo_cancel": {
        "title": "Dispos / Cancellations",
        "intro": "For properties dispo'ing or fully cancelling ALL in-house "
                 "digital services. To cancel only certain channels, use a Budget "
                 "Update instead. Cancellations require a 30-day opt-out notice.",
        "updates_profile": False,
        "fields": [
            _f("Property Name", "prefill", required=True),
            _f("Requested By", "prefill", required=True),
            _f("Dispo/Cancellation Date", required=True,
               helper="Day the property is dispo'ing. Cancellations require a 30-day opt-out window."),
            _f("Account Manager", "prefill", required=True),
            _f("Digital Region", "prefill", required=True),
            _f("Property Code", "prefill", required=True, section="Property Information"),
            _f("RM's Email", required=True, section="Property Information"),
            _f("RVP's Email", required=True, section="Property Information"),
            _f("Is this a cancellation or a dispo?", required=True, section="Property Information",
               helper="Cancellation: still under RPM Management, moving digital to another agency. "
                      "Disposition: property is no longer with RPM Management."),
            _f("Is there any other information we need to know?"),
        ],
    },

    "general": {
        "title": "General Ticket",
        "intro": "For requests that don't fit another form. For a "
                 "disposition/cancellation or a rebrand, use those dedicated forms.",
        "updates_profile": False,
        "fields": [
            _f("Property Name", "prefill", required=True),
            _f("Requested By", "prefill", required=True),
            _f("Property Code", "prefill", required=True),
            _f("Account Manager", "prefill", required=True),
            _f("Market", "prefill", required=True),
            _f("Category", required=True, helper="What kind of request is this?"),
            # Category-driven follow-ups (mirror the ClickUp form's conditional reveals).
            _f("Billing - Can you explain the issue/request in more detail?",
               show_if=("Category", ["Billing Issues"])),
            _f("Budget Recommendation - What additional details can you provide about the situation?",
               show_if=("Category", ["Budget Review / Recommendation"])),
            _f("CMS / Website Change - What information is changing or needs to be updated?",
               show_if=("Category", ["CMS Switch / Website Change"])),
            _f("Conversion / Reporting Issues - Please explain the issue in detail.",
               show_if=("Category", ["Conversion / Reporting / NinjaCat Issues"])),
            _f("Google Business Profile - Please explain the issue in detail.",
               show_if=("Category", ["Google Business Profile Inquiry"])),
            _f("Keywords - Please explain the nature of your request.",
               show_if=("Category", ["Keyword List / Updates"])),
            _f("Other - Please explain the situation in detail.",
               show_if=("Category", ["Other", "EliseAI Tracking"])),
            _f("If there are any files related to your request, please upload those here."),
            _f("Is there any other information you think we should know?"),
        ],
    },
}

# Ordered value maps for the few fields whose ticket option → profile value
# differs (e.g. Property Status → lifecycle_state enum).
PROFILE_VALUE_MAP = {
    "lifecycle_state": {
        "stabilized": "stabilized", "stable": "stabilized",
        "lease up": "lease_up", "lease-up": "lease_up",
        "pre-lease": "pre_lease", "renovated": "renovated",
    },
}
