"""RPM Client Portal configuration constants."""

import json
import os
from dotenv import load_dotenv

load_dotenv()

# --- HubSpot ---
HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")
HUBSPOT_PORTAL_ID = os.getenv("HUBSPOT_PORTAL_ID")
HUBDB_ASSET_TABLE_ID = os.getenv("HUBDB_ASSET_TABLE_ID")
HUBDB_RECOMMENDATIONS_TABLE_ID = os.getenv("HUBDB_RECOMMENDATIONS_TABLE_ID")
HUBDB_BUDGET_TIERS_TABLE_ID = os.getenv("HUBDB_BUDGET_TIERS_TABLE_ID")
HUBDB_AM_PRIORITY_TABLE_ID = os.getenv("HUBDB_AM_PRIORITY_TABLE_ID")
HUBDB_SEO_KEYWORDS_TABLE_ID = os.getenv("HUBDB_SEO_KEYWORDS_TABLE_ID")
HUBDB_SEO_COMPETITORS_TABLE_ID = os.getenv("HUBDB_SEO_COMPETITORS_TABLE_ID")
HUBDB_AI_MENTIONS_TABLE_ID = os.getenv("HUBDB_AI_MENTIONS_TABLE_ID")
HUBDB_CONTENT_BRIEFS_TABLE_ID = os.getenv("HUBDB_CONTENT_BRIEFS_TABLE_ID")
HUBDB_CONTENT_DECAY_TABLE_ID = os.getenv("HUBDB_CONTENT_DECAY_TABLE_ID")
HUBDB_PAID_KEYWORDS_TABLE_ID = os.getenv("HUBDB_PAID_KEYWORDS_TABLE_ID")
HUBDB_BRIEF_DRAFTS_TABLE_ID = os.getenv("HUBDB_BRIEF_DRAFTS_TABLE_ID")
# Onboarding/discovery + Fluency Blueprint pipeline (Phase 5).
HUBDB_ONBOARDING_INTAKE_TABLE_ID = os.getenv("HUBDB_ONBOARDING_INTAKE_TABLE_ID")
HUBDB_GAP_RESPONSES_TABLE_ID = os.getenv("HUBDB_GAP_RESPONSES_TABLE_ID")
HUBDB_BLUEPRINT_VARIABLES_TABLE_ID = os.getenv("HUBDB_BLUEPRINT_VARIABLES_TABLE_ID")
HUBDB_BLUEPRINT_TAGS_TABLE_ID = os.getenv("HUBDB_BLUEPRINT_TAGS_TABLE_ID")
HUBDB_BLUEPRINT_ASSETS_TABLE_ID = os.getenv("HUBDB_BLUEPRINT_ASSETS_TABLE_ID")
# Portal feature access — Beta/Prod partitioning (feature_access.py).
# Editing rows in these tables promotes a feature or grants Beta access
# without a deploy. Schema is documented in feature_access.py.
HUBDB_FEATURE_STAGE_TABLE_ID = os.getenv("HUBDB_FEATURE_STAGE_TABLE_ID")
HUBDB_PORTAL_ACCESS_TABLE_ID = os.getenv("HUBDB_PORTAL_ACCESS_TABLE_ID")

# --- Phase 3: Keyword Research + Trends ---
KEYWORD_RESEARCH_MAX_RESULTS = 500
KEYWORD_DIFFICULTY_BATCH_MAX = 1000
TRENDS_DEFAULT_TIMEFRAME = "past_12_months"

# --- Phase 2: Content Planner (iPullRank / GEO) ---
CLAUDE_BRIEF_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_BRIEF_MAX_TOKENS = 2000
CONTENT_DECAY_RANK_THRESHOLD = 5      # positions dropped to count as decay
CONTENT_DECAY_MIN_KEYWORDS = 3        # affected keywords per URL to flag
CONTENT_REFRESH_LOOKBACK_DAYS = 30

# --- DataForSEO ---
DATAFORSEO_LOGIN = os.getenv("DATAFORSEO_LOGIN", "")
DATAFORSEO_PASSWORD = os.getenv("DATAFORSEO_PASSWORD", "")
DATAFORSEO_BASE_URL = os.getenv("DATAFORSEO_BASE_URL", "https://api.dataforseo.com")
DATAFORSEO_DEFAULT_LOCATION = int(os.getenv("DATAFORSEO_DEFAULT_LOCATION", "2840"))  # USA
DATAFORSEO_DEFAULT_LANGUAGE = os.getenv("DATAFORSEO_DEFAULT_LANGUAGE", "en")

# BigQuery table for daily rank snapshots (time-series scale exceeds HubDB)
BIGQUERY_SEO_RANKS_TABLE = os.getenv("BIGQUERY_SEO_RANKS_TABLE", "seo_ranks_daily")
BIGQUERY_SEO_AUDIT_TABLE = os.getenv("BIGQUERY_SEO_AUDIT_TABLE", "seo_onpage_audit")

# --- Apartments.com ILS Performance Summary API (Layer 1 connector) ---
# Account-scoped daily listing performance + lead data. See ADR 0021 and
# webhook-server/apartmentscom_client.py.
# Env var name on Render is `Costar_Rental_Manager_API_Key`; APARTMENTSCOM_API_KEY
# is accepted as a local/dev fallback (see apartmentscom_client._api_key).
APARTMENTSCOM_API_KEY = os.getenv("Costar_Rental_Manager_API_Key", "") or os.getenv(
    "APARTMENTSCOM_API_KEY", ""
)
APARTMENTSCOM_BASE_URL = os.getenv(
    "APARTMENTSCOM_BASE_URL",
    "https://www.apartments.com/routes/mkt/vendor/analytics",
)
# BigQuery landing tables for apartments.com ILS data.
BIGQUERY_APARTMENTSCOM_DAILY_TABLE = os.getenv(
    "BIGQUERY_APARTMENTSCOM_DAILY_TABLE", "apartmentscom_ils_daily"
)
BIGQUERY_APARTMENTSCOM_MAP_TABLE = os.getenv(
    "BIGQUERY_APARTMENTSCOM_MAP_TABLE", "apartmentscom_listing_map"
)

# --- Google Sheets ---
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "1jRqmEzhOIe72zgwIOcDZTyvde_Y0_jvayaLTZhva9mk")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# --- KB Draft Google Drive/Sheets ---
KB_DRAFT_FOLDER_ID = os.getenv("KB_DRAFT_FOLDER_ID", "12Af-DJNd0OqZ4a2GlfMnkSoi5aeKHfJS")
KB_LOG_SHEET_ID    = os.getenv("KB_LOG_SHEET_ID",    "18oIx_CmBcTPDsG44YY3mFy2CfKhSjcTshfWYsa3gheI")

# --- Google BigQuery ---
BIGQUERY_PROJECT_ID = os.getenv("BIGQUERY_PROJECT_ID")
BIGQUERY_SERVICE_ACCOUNT_JSON = os.getenv("BIGQUERY_SERVICE_ACCOUNT_JSON")
BIGQUERY_DATASET_PROD = os.getenv("BIGQUERY_DATASET_PROD", "rpm_portal")
BIGQUERY_DATASET_DEV = os.getenv("BIGQUERY_DATASET_DEV", "rpm_portal_dev")

# --- Anthropic ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_DIGEST_MODEL = "claude-sonnet-4-5"
CLAUDE_AGENT_MODEL = "claude-sonnet-4-5"
CLAUDE_DIGEST_TEMP = 0.2
CLAUDE_DIGEST_MAX_TOKENS = 600
DIGEST_CACHE_HOURS = 24

# --- ClickUp ---
CLICKUP_API_KEY = os.getenv("CLICKUP_API_KEY")
# ClickUp list that portal QA feedback files into (portal_feedback.py). Unset
# means the widget stays hidden rather than accepting reports it cannot file.
CLICKUP_LIST_PORTAL_FEEDBACK = os.getenv("CLICKUP_LIST_PORTAL_FEEDBACK", "")

CLICKUP_LISTS = {
    "seo": os.getenv("CLICKUP_LIST_SEO"),
    "paid_media": os.getenv("CLICKUP_LIST_PAID_MEDIA"),
    "social": os.getenv("CLICKUP_LIST_SOCIAL"),
    "reputation": os.getenv("CLICKUP_LIST_REPUTATION"),
    "onboarding": os.getenv("CLICKUP_LIST_ONBOARDING"),
    # Property brief intake — drives the automation in property_brief.py.
    "property_brief": os.getenv("CLICKUP_LIST_PROPERTY_BRIEF"),
    # Fulfillment hand-off for self-checkout deals (fulfillment_task.py,
    # Bridge 1). Unset → the deal→ClickUp bridge no-ops.
    "fulfillment": os.getenv("CLICKUP_LIST_FULFILLMENT"),
}
# Per-stage ClickUp ticket statuses for the property brief workflow.
# These slugs are passed to ClickUp's update-task API; they must match the
# statuses configured on the property-brief list. Defaults are conservative
# so unconfigured environments simply skip status updates rather than 400.
CLICKUP_BRIEF_STATUSES = {
    "deal_created":      os.getenv("CLICKUP_BRIEF_STATUS_DEAL_CREATED",      "deal sent"),
    "awaiting_approval": os.getenv("CLICKUP_BRIEF_STATUS_AWAITING_APPROVAL", "brief in review"),
    "needs_edits":       os.getenv("CLICKUP_BRIEF_STATUS_NEEDS_EDITS",       "brief needs edits"),
    "approved":          os.getenv("CLICKUP_BRIEF_STATUS_APPROVED",          "brief approved"),
    "quote_signed":      os.getenv("CLICKUP_BRIEF_STATUS_QUOTE_SIGNED",      "quote signed"),
    "blocked":           os.getenv("CLICKUP_BRIEF_STATUS_BLOCKED",           "blocked"),
    "escalated":         os.getenv("CLICKUP_BRIEF_STATUS_ESCALATED",         "escalated"),
}
CLICKUP_WEBHOOK_SECRET = os.getenv("CLICKUP_WEBHOOK_SECRET", "")
HUBSPOT_QUOTE_WEBHOOK_SECRET = os.getenv("HUBSPOT_QUOTE_WEBHOOK_SECRET", "")

# ClickUp status that means "this intake ticket is ready — create the
# deal/quote/brief". The automation fires when a ticket is created in, or
# moves into, one of these statuses. Comma-separated, case-insensitive.
CLICKUP_INTAKE_STATUSES = [
    s.strip().lower()
    for s in os.getenv("CLICKUP_INTAKE_STATUS", "to vet").split(",")
    if s.strip()
]

# HubSpot quote template pinned on every auto-generated quote so the AM
# doesn't see "Your template is no longer available" in the editor. Must be
# a quote_template object id that currently exists in the portal — a stale
# id is itself the cause of that error. Empty -> don't pin (editor falls
# back to the portal default and the AM picks a template manually).
HUBSPOT_QUOTE_TEMPLATE_ID = os.getenv("HUBSPOT_QUOTE_TEMPLATE_ID", "472873408612")

# --- Property Brief Automation ---
# Token-gated approval portal. Tokens are unguessable and consumed once a
# decision is captured (see property_brief_store.py).
PROPERTY_BRIEF_TOKEN_TTL_HOURS = int(os.getenv("PROPERTY_BRIEF_TOKEN_TTL_HOURS", "168"))   # 7 days
PROPERTY_BRIEF_MAX_REVISIONS = int(os.getenv("PROPERTY_BRIEF_MAX_REVISIONS", "3"))
# After the cap, the brief escalates to the ops queue rather than re-running
# the LLM. Notifications go through PROPERTY_BRIEF_FAILURE_CHANNEL.
PROPERTY_BRIEF_FAILURE_CHANNEL = os.getenv("PROPERTY_BRIEF_FAILURE_CHANNEL", "clickup")
# HubDB table used as the brief store. Keyed by token. See docs/property_brief.md.
HUBDB_PROPERTY_BRIEFS_TABLE_ID = os.getenv("HUBDB_PROPERTY_BRIEFS_TABLE_ID")
# Public base URL used to render approval links. Falls back to WEBHOOK_SERVER_URL.
PROPERTY_BRIEF_PUBLIC_URL = os.getenv("PROPERTY_BRIEF_PUBLIC_URL") or os.getenv("WEBHOOK_SERVER_URL", "http://localhost:8443")
# Re-fire updates only when this ClickUp custom field is flipped to true.
# Default: never re-fire on updates (creation-only trigger).
PROPERTY_BRIEF_REFIRE_FIELD = os.getenv("PROPERTY_BRIEF_REFIRE_FIELD", "rpm_brief_reprocess")

# ClickUp checkbox attesting the Community Brief is current. When a campaign
# ticket carries this field, it must be checked before automation proceeds
# (the brief gate). Tickets WITHOUT the field are unaffected.
CLICKUP_BRIEF_ATTEST_FIELD = os.getenv(
    "CLICKUP_BRIEF_ATTEST_FIELD", "Community Brief is up to date & accurate")

# --- Portal Ticket Page (per-type forms → ClickUp) ---
# DUPLICATED, BY HAND, from webhook-server/config.py — and it had already
# drifted: this copy carried the pre-fix `form_slug`s, the bare "Creative"
# alias that resolves to the wrong list, `account_manager` (a HubSpot property
# that does not exist), and "pending pm approval" → "In progress". Re-synced
# 2026-08-17.
#
# Which file wins is decided by sys.path, not by intent: `start.py` puts
# webhook-server/ first, so PRODUCTION reads webhook-server/config.py, while
# `python3 scripts/<name>.py` from the repo root and a full pytest run read
# THIS one. A script that ran ticket discovery from the repo root would emit
# the wrong ClickUp list ids and look entirely correct doing it.
# See docs/ticket-page-scope.md.
# The portal renders one form per ticket type, reads each list's field
# definitions LIVE from ClickUp, and creates the task in that type's list.
# List IDs come from env — the ClickUp URLs carry *view* IDs, not list IDs, so
# pull the real ones with the internal GET /api/portal-tickets/admin/discover.
CLICKUP_WORKSPACE_ID = os.getenv("CLICKUP_WORKSPACE_ID", "9011805260")

# Ordered ticket-type registry. `key` is the stable portal id; `label` mirrors
# the real ClickUp form/list name (so discovery can match by name); `list_env`
# names the env var holding that type's list id; `audience` gates the picker
# ("client" = anyone in the portal, "internal" = hidden). `aliases` catch
# naming drift; `form_slug` is the id segment from the public form URL, kept
# for reference only (NOT the numeric list id the API needs). A type whose
# list id is unset is omitted from the picker, so it lights up type-by-type.
PORTAL_TICKET_TYPES = [
    {"key": "new_account_build", "label": "New Account Onboarding",             "list_env": "CLICKUP_LIST_NEW_ACCOUNT_BUILD", "audience": "client",   "form_slug": "8cjaf2c-19771", "aliases": ["New Account Build", "New Account", "Onboarding"]},
    {"key": "budget_update",     "label": "Budget Changes",                     "list_env": "CLICKUP_LIST_BUDGET_UPDATE",      "audience": "client",   "form_slug": "8cjaf2c-20971", "aliases": ["Budget Update", "Budget"]},
    {"key": "general",           "label": "General Ticket",                     "list_env": "CLICKUP_LIST_GENERAL",            "audience": "client",   "form_slug": "8cjaf2c-24611", "aliases": ["General"]},
    # NOT aliased to bare "Creative": discovery's loose contains-match resolved
    # that to a list named "Creative" in the Paid Media space's documentation
    # folder, 901112821771, instead of the real intake list "Creative + Ad Copy
    # Updates". Verified 2026-08-17 — form 8cjaf2c-2751 is parented to 901111120522.
    {"key": "creative_ad_copy",  "label": "Ad Updates: Photos & New Specials",  "list_env": "CLICKUP_LIST_CREATIVE_AD_COPY",   "audience": "client",   "form_slug": "8cjaf2c-2751",  "aliases": ["Ad Updates", "Creative & Ad Copy Updates", "Creative + Ad Copy Updates", "Photos & New Specials Review"]},
    # form_slug was 8cjaf2c-2771, whose list is "[OLD] - Campaign Performance
    # Review" — ARCHIVED. The form was rebuilt on the [NEW] list (901114166834)
    # as 8cjaf2c-68111. Verified 2026-08-17.
    {"key": "campaign_review",   "label": "Digital Marketing Review",           "list_env": "CLICKUP_LIST_CAMPAIGN_REVIEW",    "audience": "client",   "form_slug": "8cjaf2c-68111", "aliases": ["Campaign Performance Review", "Marketing Review", "Performance Review"]},
    {"key": "rebrand",           "label": "Rebrands",                           "list_env": "CLICKUP_LIST_REBRAND",            "audience": "client",   "form_slug": "8cjaf2c-2811",  "aliases": ["Rebrand"]},
    # Dispos/Cancellations defaults to internal-only — flip via CLICKUP_DISPO_AUDIENCE
    # if property marketing should open one directly (scope doc §Decisions #3).
    {"key": "dispo_cancel",      "label": "Dispos / Cancellations",             "list_env": "CLICKUP_LIST_DISPO_CANCEL",       "audience": os.getenv("CLICKUP_DISPO_AUDIENCE", "internal"), "form_slug": "8cjaf2c-22451", "aliases": ["Dispo / Cancel", "Dispo", "Cancellation", "Cancel"]},
    # "New Business" is a sales/lead intake form — internal by default; not part
    # of the property client's ticket picker unless CLICKUP_NEW_BUSINESS_AUDIENCE flips it.
    {"key": "new_business",      "label": "New Business",                       "list_env": "CLICKUP_LIST_NEW_BUSINESS",       "audience": os.getenv("CLICKUP_NEW_BUSINESS_AUDIENCE", "internal"), "form_slug": "8cjaf2c-2791", "aliases": ["new business"]},
]

# ClickUp custom-field names the portal auto-fills from the property record so
# the requester never re-types what we already know. Filtered OUT of the
# client-facing form. Matched case-insensitively against the ClickUp field name.
PORTAL_TICKET_PREFILL_FIELDS = [
    s.strip() for s in os.getenv(
        "CLICKUP_TICKET_PREFILL_FIELDS",
        "Property URL,Market,Property Code,Account Manager,uuid,UUID",
    ).split(",") if s.strip()
]

# Where each prefilled ClickUp field is sourced on the HubSpot company record.
# {ClickUp field name: HubSpot company property}. Best-effort — a missing
# HubSpot prop just leaves that field blank for the requester to fill in.
PORTAL_TICKET_PREFILL_SOURCES = {
    "Property URL":    os.getenv("CLICKUP_PREFILL_PROP_URL_FIELD",  "website"),
    "Property Code":   os.getenv("CLICKUP_PREFILL_PROP_CODE_FIELD", "property_code"),
    "Market":          os.getenv("CLICKUP_PREFILL_MARKET_FIELD",    "market"),
    # There is no `account_manager` company property (verified against the live
    # schema, 838 properties). The AM is the record owner, so resolve it from
    # hubspot_owner_id via the owners API — see portal_tickets._prefill_values.
    "Account Manager": os.getenv("CLICKUP_PREFILL_AM_FIELD",        "hubspot_owner_id"),
    "uuid": "uuid",
    "UUID": "uuid",
}

# Digital region → Account Manager. All 8 ClickUp forms embed this as a
# SCREENSHOT of a spreadsheet and ask the requester to look themselves up.
#
# This is the FALLBACK, not the answer. The Account Manager is the owner of the
# HubSpot company record, resolved through the owners API
# (portal_tickets._owner_name), which is right even when someone changes desks.
# This table only covers a property with no owner set, and it goes stale the
# moment anyone moves — keep it in that role.
#
# Per the forms' own caveat: an RPMI property selects RPMI rather than its
# physical region, and "National" is no longer a digital region.
MARKET_TO_ACCOUNT_MANAGER = {
    "dallas": "Dane",
    "atlanta": "Dustin", "rpmi": "Dustin", "southeast (tn)": "Dustin", "southeast": "Dustin",
    "houston": "Juliana", "midwest": "Juliana",
    "florida": "Katie", "mid-atlantic": "Katie", "west": "Katie",
    "mountain": "Lauren",
    "austin": "Logan", "san antonio": "Logan",
}

# --- Per-type form manifests (docs: ticket intake redesign §3) ---------------
#
# The scope doc chose to read forms live from ClickUp so they could never
# drift. The instinct is right; the endpoint it picked has no concept of a
# form. In ClickUp, custom fields are defined at the SPACE level, so
# `GET /list/{id}/field` returns every field AVAILABLE TO the list — the union
# of every ticket type in the space — while the thing that decides which
# fields a form shows, in what order, with what help text, is the form view,
# which the public API does not expose. Hence: this repo owns which fields
# appear and how they read; ClickUp still owns field ids, types and dropdown
# options, resolved live BY NAME at render and submit time. A team member
# editing a dropdown's options still flows through with no redeploy; adding or
# removing a field becomes a manifest change, and the drift test says so
# instead of the field silently vanishing.
#
# `name` MUST match the live ClickUp custom-field name EXACTLY — it is the
# join key. Resolution is per list, never globally by name across the space:
# several channel fields exist twice under one display name (currency on
# new_account_build, select on budget_update), so a global lookup returns the
# wrong field id.
#
# `tier` is one of:
#   "known" — resolved from the property record; submitted, never rendered
#   "file"  — owned by the Community Brief; confirm-or-update (phase 3)
#   "ask"   — a real input, the only tier that renders today
# `source` names the HubSpot property (known) or fluency_* override (file).
#
# Scoped to `creative_ad_copy` on purpose: it renders ZERO of its five real
# fields today, so every special submitted through the portal arrives
# unactionable. Highest value, smallest surface, and it proves the
# manifest+drift pattern before the other five types adopt it. A type with no
# entry here keeps the previous whole-list behaviour, so nothing regresses.
PORTAL_TICKET_FORMS = {
    "creative_ad_copy": {
        "sla": "Please allow 5–7 business days for this request.",
        "notes": [
            "This form is only for live campaigns that already have both the "
            "creative and the copy to be updated.",
        ],
        "prereqs": [],
        "name_pattern": "[Property Name] - Creative / Ad Copy Update",
        "sections": [
            {
                # Every field here is tier `known` — none of them render. The
                # section exists so the drift test still sees them and so the
                # phase-2 identity summary card has somewhere to live.
                "title": "Property Information",
                "help": "",
                "fields": [
                    {"name": "Property Code", "tier": "known", "source": "property_code"},
                    {"name": "Property URL", "tier": "known", "source": "website"},
                    # Live name is "Account Manager", NOT the form's question
                    # wording ("Who is your Account Manager?"). Resolved from
                    # hubspot_owner_id — there is no account_manager company
                    # property (verified against the live 838-property schema).
                    {"name": "Account Manager", "tier": "known", "source": "hubspot_owner_id"},
                    # Live name carries a trailing asterisk. It is the join key,
                    # so it is reproduced verbatim, typo and all.
                    {"name": "Market*", "tier": "known", "source": "market"},
                    # Lowercase `z_`, not `Z_`. A case-SENSITIVE "hide Z_*"
                    # filter would miss this field entirely.
                    {"name": "z_Requested By", "tier": "known", "source": "submitted_by"},
                ],
            },
            {
                "title": "Creative + Ad Copy Information",
                "help": "",
                "fields": [
                    {"name": "What is the new/updated special?", "tier": "ask",
                     "required": True, "maxlength": 90,
                     "help": "Max 90 characters, no capitalization, and no "
                             "punctuation other than periods — ad platforms "
                             "reject copy that breaks these rules. "
                             "Ex: 2 bedrooms starting at $1500 through july 15th.",
                     "validate": "no_caps_no_punct_except_period"},
                    {"name": "Channels / Ads Impacted", "tier": "ask", "required": True,
                     "help": "Select every channel this update should reach."},
                    {"name": "Special / Promotion End Date", "tier": "ask", "required": True,
                     "help": "When the special stops running."},
                    {"name": "SharePoint Path to New Image / Photo", "tier": "ask",
                     "help": "Leave blank if there is no new image."},
                    {"name": "Is there any other information we need to know?",
                     "tier": "ask"},
                ],
            },
        ],
    },

    "general": {
        "sla": "Please allow 5–7 business days for this request.",
        "notes": [
            "Disposing or cancelling a property? Submit a Dispo / Cancellation "
            "request instead.",
            "Rebranding a property? Submit a Rebrand request instead.",
        ],
        "prereqs": [],
        "name_pattern": "[Property Name] - General Ticket - [Issue / Inquiry]",
        "sections": [
            {
                "title": "Property Information",
                "help": "",
                "fields": [
                    {"name": "Property Name", "tier": "known", "source": "name"},
                    {"name": "Property Code", "tier": "known", "source": "property_code"},
                    {"name": "Account Manager", "tier": "known", "source": "hubspot_owner_id"},
                    # NOT "Market": this list carries FOUR fields with that exact
                    # name, each holding a different regional option set. "Market*"
                    # is the one with the 12 digital regions the forms use.
                    {"name": "Market*", "tier": "known", "source": "market"},
                    {"name": "z_Requested By", "tier": "known", "source": "submitted_by"},
                ],
            },
            {
                "title": "About your request",
                "help": "",
                "fields": [
                    {"name": "Category", "tier": "ask", "required": True,
                     "help": "What area of digital marketing does this concern?"},
                    {"name": "Is there any other information you think we should know?",
                     "tier": "ask", "required": True,
                     "help": "Describe the issue or question in as much detail as "
                             "you can — what you expected, what you are seeing, and "
                             "since when."},
                ],
            },
        ],
        # NOT in this manifest, deliberately:
        #   "What is the priority of this project?" — the form's priority control
        #     is ClickUp's NATIVE task priority, not a custom field, so there is
        #     nothing on the list to resolve it to.
        #   "If there are any files related to your request…" — an `attachment`
        #     field. The portal has no upload control for tickets yet, and
        #     rendering an attachment as a text box (what the old code did) just
        #     collects a path nobody can open.
    },

    "rebrand": {
        "sla": "Please allow 5–7 business days for this request.",
        "notes": [
            "Rebrands are completed as close to the requested launch date as "
            "possible. Outside constraints mean that is not always achievable.",
        ],
        "prereqs": [],
        "name_pattern": "[Property Name] - Rebrand",
        "sections": [
            {
                "title": "Current Property Information",
                "help": "",
                "fields": [
                    {"name": "Property Name", "tier": "known", "source": "name"},
                    {"name": "Property Code", "tier": "known", "source": "property_code"},
                    {"name": "Property URL", "tier": "known", "source": "website"},
                    {"name": "Account Manager", "tier": "known", "source": "hubspot_owner_id"},
                    {"name": "Market*", "tier": "known", "source": "market"},
                    {"name": "z_Requested By", "tier": "known", "source": "submitted_by"},
                ],
            },
            {
                "title": "Rebrand Information",
                "help": "Everything below describes the property AFTER the rebrand.",
                "fields": [
                    {"name": "Date of Rebrand Launch", "tier": "ask", "required": True},
                    {"name": "Rebranded Property Name", "tier": "ask", "required": True},
                    # The form asks for "New Website URL"; the live field is
                    # "Rebrand URL".
                    {"name": "Rebrand URL", "tier": "ask", "required": True,
                     "help": "The website address once the rebrand takes place."},
                    {"name": "New Property Email", "tier": "ask", "required": True},
                    {"name": "Website Platform", "tier": "ask", "required": True,
                     "help": "Is the platform changing? Ex: switching from Jonah "
                             "to RentCafe."},
                    {"name": "Link to Rebrand Creative", "tier": "ask", "required": True},
                    {"name": "Is there any other information we need to know?",
                     "tier": "ask"},
                ],
            },
        ],
    },

    "campaign_review": {
        "sla": "Please allow 5–7 business days for this request.",
        "notes": [
            "This request is for investigating a DEFINED performance concern. It "
            "is not a recurring account audit, and not a request to make "
            "unspecified campaign changes.",
            "Requests such as \"refresh ad copy\", \"audit all keywords\" or "
            "\"identify any possible optimizations\" may be returned for more "
            "detail.",
            "For budget recommendations, campaign launches, creative updates or "
            "special/concession changes, submit the applicable request type "
            "instead.",
        ],
        "prereqs": [],
        "name_pattern": "[Property Name] - Campaign Performance Review",
        # RE-TRANSCRIBED FROM THE LIVE LIST (901114166834), not from the brief's
        # field table: that table was taken from form 8cjaf2c-2771, whose list is
        # the ARCHIVED "[OLD] - Campaign Performance Review". The two schemas are
        # almost entirely different — the archived one has 13 fields, this has 40.
        "sections": [
            {
                "title": "Property Information",
                "help": "",
                "fields": [
                    {"name": "Property Code", "tier": "known", "source": "property_code"},
                    {"name": "Account Manager", "tier": "known", "source": "hubspot_owner_id"},
                    {"name": "Market*", "tier": "known", "source": "market"},
                    {"name": "z_Requested By", "tier": "known", "source": "submitted_by"},
                ],
            },
            {
                "title": "What prompted this review",
                "help": "",
                "fields": [
                    {"name": "What is the primary reason for review?", "tier": "ask",
                     "required": True},
                    {"name": "Change and Timeframe for Change", "tier": "ask",
                     "required": True,
                     "help": "What specifically changed, when you first noticed it, "
                             "which metrics are affected, and what you are comparing "
                             "against."},
                ],
            },
            {
                "title": "Current performance",
                # Phase 4 replaces these four with a timestamped snapshot from the
                # portal's own occupancy / ATR / budget data — the sidebar is
                # already showing those numbers on the same screen. Until that
                # ships they stay as inputs: asking is worse than auto-attaching,
                # but far better than dropping the data entirely.
                "help": "",
                "fields": [
                    {"name": "Occupancy", "tier": "ask", "required": True,
                     "help": "Current occupancy %."},
                    {"name": "Occupancy Trend", "tier": "ask", "required": True},
                    {"name": "Current ATR %", "tier": "ask", "required": True},
                    {"name": "Has the budget recently changed?", "tier": "ask",
                     "required": True},
                    {"name": "Target Occupancy %", "tier": "ask", "required": True},
                    {"name": "Target Date/Timeline", "tier": "ask", "required": True,
                     "help": "Ex: August 1st, or 2 months."},
                    {"name": "Property Status", "tier": "ask", "required": True,
                     "help": "Select everything that applies."},
                ],
            },
            {
                "title": "Context for the review",
                "help": "",
                "fields": [
                    {"name": "Lead-Quality Concerns", "tier": "ask", "required": True},
                    {"name": "Are there any active concessions or specials that may "
                             "be relevant to this review?", "tier": "ask", "required": True},
                    {"name": "Are there particular units, price points, or renter "
                             "needs relevant to this review?", "tier": "ask", "required": True,
                     "help": "Priority floor plans, unit types, price or income "
                             "requirements, immediate move-in needs."},
                    {"name": "Additional Channels/Services", "tier": "ask", "required": True,
                     "help": "What is running outside Digital Marketing's products?"},
                    {"name": "Which parties are concerned with campaign performance?",
                     "tier": "ask", "required": True},
                    {"name": "Which sources have you reviewed?", "tier": "ask",
                     "required": True},
                    {"name": "Pricing vs. Competitor(s)", "tier": "ask", "required": True,
                     "help": "Based on the most recent comp review."},
                ],
            },
        ],
    },

    "new_account_build": {
        "sla": "Please allow 5–7 business days for this request.",
        "notes": [
            "This form is for new properties coming online, NOT for budget "
            "changes. If you need budget recommendations, submit a General "
            "request.",
            "Management fee is 20% or $250, whichever is greater.",
        ],
        "prereqs": [
            "Property website is live.",
            "Property images received (2–3 minimum).",
            "Property logo received.",
            "Property code received (in SalesForce).",
            "P-Code received in RentCafe (paid products ONLY).",
            "Tracking and UTM codes received.",
            "IO has been signed.",
        ],
        "name_pattern": "[Property Name] - New Account Build",
        "sections": [
            {
                "title": "Property Information",
                "help": "",
                "fields": [
                    {"name": "Property Name", "tier": "known", "source": "name"},
                    {"name": "Property Address", "tier": "known", "source": "address"},
                    {"name": "Property URL", "tier": "known", "source": "website"},
                    {"name": "Property Type", "tier": "known", "source": "property_type"},
                    {"name": "Property Email Address", "tier": "known", "source": "company_email"},
                    {"name": "Property Code", "tier": "known", "source": "property_code"},
                    {"name": "Property Status", "tier": "known",
                     "source": "fluency_lifecycle_state"},
                    {"name": "z_Client Name", "tier": "known", "source": "client"},
                    {"name": "Account Manager", "tier": "known", "source": "hubspot_owner_id"},
                    {"name": "Market*", "tier": "known", "source": "market"},
                    {"name": "RM's Email", "tier": "known", "source": "regional_manager_email"},
                    {"name": "RVP's Email", "tier": "known", "source": "marketing_rvp_email"},
                    {"name": "z_Requested By", "tier": "known", "source": "submitted_by"},
                ],
            },
            {
                "title": "Launch",
                "help": "",
                "fields": [
                    {"name": "Earliest Launch Date", "tier": "ask", "required": True,
                     "help": "Choose 'Future Date' if this is more than 7 days out."},
                    {"name": "Property Website Platform", "tier": "ask", "required": True,
                     "help": "Ex: RentCafe, Jonah, Perq, Knock, RealPage, Fervor."},
                    {"name": "Property Website Bot", "tier": "ask", "required": True,
                     "help": "Ex: CRMIQ, Knock, Funnel, Client Hosted, None."},
                ],
            },
            {
                "title": "Property Details",
                "help": "",
                "fields": [
                    {"name": "Top Amenities", "tier": "file",
                     "source": "fluency_amenities_override"},
                    {"name": "Local Hotspots / Attractions", "tier": "file",
                     "source": "fluency_landmarks_override"},
                    {"name": "z_Top Competitors", "tier": "file",
                     "source": "fluency_competitors_override"},
                    {"name": "Local / Colloquial Terms", "tier": "file",
                     "source": "fluency_neighborhood_override"},
                    {"name": "Voice & Tone", "tier": "file",
                     "source": "fluency_voice_tier_override"},
                    {"name": "Target Audience", "tier": "file",
                     "source": "fluency_motivations_considerations_override"},
                    {"name": "Property Floor Plan Type", "tier": "file",
                     "source": "fluency_floor_plans_override"},
                    {"name": "Campaign Focus", "tier": "ask", "required": True,
                     "help": "The main goal, and any specific floor plans or "
                             "layouts to push."},
                    {"name": "Keywords", "tier": "ask", "required": True,
                     "help": "Terms you want this property to be found for."},
                    {"name": "SharePoint URL", "tier": "ask"},
                ],
            },
            {
                "title": "Account Budget — Paid",
                "help": "Monthly budget per channel. Leave blank for anything you "
                        "are not launching.",
                # The `currency` twins. Each of these names ALSO exists on this
                # same list as a drop_down (add/remove/change) used by
                # budget_update, so clickup_type is what keeps them apart.
                "fields": [
                    {"name": "Paid Search", "tier": "ask", "clickup_type": "currency"},
                    {"name": "Google Display", "tier": "ask", "clickup_type": "currency",
                     "help": "Creative required. If you do not have it, submit a CSR."},
                    {"name": "PMax", "tier": "ask", "clickup_type": "currency"},
                    {"name": "Paid Social", "tier": "ask", "clickup_type": "currency"},
                    {"name": "Geofence", "tier": "ask", "clickup_type": "currency",
                     "help": "$250 one-time setup fee."},
                    {"name": "Retargeting", "tier": "ask", "clickup_type": "currency"},
                    {"name": "TikTok", "tier": "ask", "clickup_type": "currency"},
                    {"name": "Programmatic", "tier": "ask", "clickup_type": "currency"},
                ],
            },
            {
                "title": "SEO, Email & Social",
                "help": "",
                "fields": [
                    {"name": "SEO - Onboard", "tier": "ask"},
                    {"name": "Email Drip Campaign - New Build", "tier": "ask",
                     "help": "$125/mo plus a $225 one-time setup fee."},
                    {"name": "eBlast", "tier": "ask", "help": "$700 one-time fee."},
                    {"name": "Organic Social", "tier": "ask"},
                ],
            },
            {
                "title": "Tracking",
                "help": "",
                "fields": [
                    {"name": "Property Tracking Information", "tier": "ask",
                     "required": True,
                     "help": "Tracking and UTM codes for this property."},
                    {"name": "Is there any other information you think we should know?",
                     "tier": "ask"},
                ],
            },
        ],
    },

    "budget_update": {
        "sla": "Please allow 5–7 business days for this request.",
        "notes": [
            "This form is for properties already online.",
            "SLAs do not supersede best practice — product cancellations are done "
            "at end of month.",
            "If this budget update adds a NEW channel build for a paid product, "
            "the new-build prerequisites apply too.",
            "Disposing or cancelling? Submit a Dispo / Cancellation request. Need "
            "budget recommendations? Submit a General request.",
        ],
        "prereqs": [],
        "name_pattern": "[Property Name] - Budget Update",
        "sections": [
            {
                "title": "Property Information",
                "help": "",
                "fields": [
                    {"name": "Property Name", "tier": "known", "source": "name"},
                    {"name": "Property URL", "tier": "known", "source": "website"},
                    {"name": "Property Code", "tier": "known", "source": "property_code"},
                    {"name": "Account Manager", "tier": "known", "source": "hubspot_owner_id"},
                    {"name": "Market*", "tier": "known", "source": "market"},
                    {"name": "RM's Email", "tier": "known", "source": "regional_manager_email"},
                    {"name": "RVP's Email", "tier": "known", "source": "marketing_rvp_email"},
                    {"name": "z_Requested By", "tier": "known", "source": "submitted_by"},
                ],
            },
            {
                "title": "Timing",
                "help": "",
                "fields": [
                    {"name": "Earliest Launch Date", "tier": "ask", "required": True,
                     "help": "'ASAP' defaults to the SLA turn time above."},
                    {"name": "SharePoint URL", "tier": "ask", "required": True},
                ],
            },
            {
                "title": "Has anything about the property changed?",
                "help": "Only fill in what has actually changed since the last "
                        "update. Anything you leave blank stays as it is.",
                "fields": [
                    {"name": "Top Amenities", "tier": "file",
                     "source": "fluency_amenities_override"},
                    {"name": "Local Hotspots / Attractions", "tier": "file",
                     "source": "fluency_landmarks_override"},
                    {"name": "z_Top Competitors", "tier": "file",
                     "source": "fluency_competitors_override"},
                    {"name": "Local / Colloquial Terms", "tier": "file",
                     "source": "fluency_neighborhood_override"},
                    {"name": "Target Audience", "tier": "file",
                     "source": "fluency_motivations_considerations_override"},
                    {"name": "Campaign Focus", "tier": "ask"},
                    {"name": "Keywords", "tier": "ask"},
                ],
            },
            {
                "title": "Account Budget — Paid",
                "help": "Add, remove or change a channel.",
                # The drop_down twins of the same eight names. clickup_type is
                # load-bearing: without it these resolve to the currency variants
                # new_account_build uses, and the value lands on the wrong field.
                "fields": [
                    {"name": "Paid Search", "tier": "ask", "clickup_type": "drop_down"},
                    {"name": "Google Display", "tier": "ask", "clickup_type": "drop_down",
                     "help": "Creative required. If you do not have it, submit a CSR."},
                    {"name": "P Max", "tier": "ask",
                     "help": "Images and a logo are needed to launch a new PMax campaign."},
                    {"name": "Paid Social", "tier": "ask", "clickup_type": "drop_down"},
                    {"name": "Geofence", "tier": "ask", "clickup_type": "drop_down",
                     "help": "Creative required."},
                    {"name": "Retargeting", "tier": "ask", "clickup_type": "drop_down"},
                    {"name": "TikTok", "tier": "ask", "clickup_type": "drop_down"},
                    {"name": "Programmatic", "tier": "ask", "clickup_type": "drop_down"},
                ],
            },
            {
                "title": "SEO, Email & Social",
                "help": "",
                "fields": [
                    {"name": "SEO - Budget Update", "tier": "ask"},
                    {"name": "Email Drip Campaign - Budget Update", "tier": "ask"},
                    {"name": "eBlast", "tier": "ask", "help": "$700 one-time fee."},
                    {"name": "Organic Social - Budget Update", "tier": "ask",
                     "help": "$500 one-time setup fee."},
                ],
            },
            {
                "title": "Tracking",
                "help": "",
                "fields": [
                    {"name": "Property Tracking Information", "tier": "ask",
                     "required": True},
                    {"name": "Is there any other information you think we should know?",
                     "tier": "ask"},
                ],
            },
        ],
    },

    "dispo_cancel": {
        "sla": "Please allow 5–7 business days once a signed IO is received.",
        "notes": [
            "This form is exclusively for properties disposing, or cancelling ALL "
            "in-house digital services. To cancel only certain channels, submit a "
            "Budget Update request instead.",
            "Dispositions are completed ON the disposition date given below.",
            "Cancellations require a 30-day opt-out notice.",
            "If the date changes, tell the team as soon as possible.",
        ],
        "prereqs": [],
        "name_pattern": "[Property Name] - Dispo OR Cancellation",
        "sections": [
            {
                "title": "Property Information",
                "help": "",
                "fields": [
                    {"name": "Property Name", "tier": "known", "source": "name"},
                    {"name": "Property Code", "tier": "known", "source": "property_code"},
                    {"name": "Account Manager", "tier": "known", "source": "hubspot_owner_id"},
                    {"name": "Market*", "tier": "known", "source": "market"},
                    {"name": "RM's Email", "tier": "known", "source": "regional_manager_email"},
                    {"name": "RVP's Email", "tier": "known", "source": "marketing_rvp_email"},
                    {"name": "z_Requested By", "tier": "known", "source": "submitted_by"},
                ],
            },
            {
                "title": "Dispo / Cancellation",
                "help": "",
                "fields": [
                    {"name": "Dispo/Cancellation Date", "tier": "ask", "required": True,
                     "help": "Cancellations need a 30-day opt-out window from today."},
                    {"name": "Is this a cancellation or a dispo?", "tier": "ask",
                     "required": True,
                     "help": "Cancellation: still under RPM management, running "
                             "digital with another agency. Disposition: no longer "
                             "with RPM management."},
                    {"name": "Is there any other information we need to know?",
                     "tier": "ask"},
                ],
            },
        ],
    },

    "new_business": {
        "sla": "Please allow 5–7 business days for this request.",
        "notes": [
            "Only submit this if you are pitching NEW business.",
            "If the SLA window is too tight, contact the Account Manager directly.",
        ],
        "prereqs": [],
        "name_pattern": "[Property Name / Ownership Group] - New Business Request",
        # The one type where the property may not exist in HubSpot yet, so the
        # KNOWN tier mostly does not apply: identity is ASKED, not resolved.
        "sections": [
            {
                "title": "Requester",
                "help": "",
                "fields": [
                    {"name": "z_Requested By", "tier": "known", "source": "submitted_by"},
                ],
            },
            {
                "title": "The pitch",
                "help": "",
                "fields": [
                    {"name": "Requested Due Date", "tier": "ask", "required": True,
                     "help": "Anything less than 3 days out defaults to the SLA above."},
                    {"name": "Pitch Date", "tier": "ask", "required": True},
                    {"name": "Account Manager", "tier": "ask", "required": True},
                    {"name": "Market*", "tier": "ask", "required": True},
                ],
            },
            {
                "title": "Property Information",
                "help": "This property is prospective, so nothing here can be "
                        "filled in from our records yet.",
                "fields": [
                    {"name": "Property Name", "tier": "ask", "required": True},
                    {"name": "Property URL", "tier": "ask", "required": True},
                    {"name": "Property Address", "tier": "ask", "required": True},
                    {"name": "Property Status", "tier": "ask", "required": True},
                    {"name": "Property Type", "tier": "ask", "required": True},
                    {"name": "Unit Count", "tier": "ask", "required": True},
                    {"name": "What digital tactics are they currently running?",
                     "tier": "ask", "required": True},
                    {"name": "Metrics that Matter", "tier": "ask", "required": True,
                     "help": "What matters to this ownership group."},
                ],
            },
            {
                "title": "Competitors",
                "help": "",
                "fields": [
                    {"name": "Competitor Website #1", "tier": "ask"},
                    {"name": "Competitor Website #2", "tier": "ask"},
                    {"name": "Competitor Website #3", "tier": "ask"},
                    {"name": "Is there any other information you think we should know?",
                     "tier": "ask"},
                ],
            },
        ],
    },
}

# ClickUp task statuses → client-safe portal labels (scope doc §4). Matched
# case-insensitively; anything unmapped falls through to a title-cased version
# of the raw ClickUp status rather than leaking an internal slug verbatim.
PORTAL_TICKET_STATUS_MAP = {
    "to do": "Open", "open": "Open", "new": "Open", "backlog": "Open",
    "in progress": "In progress",
    # NOT "In progress". This is the one status where the requester can act, and
    # showing it as in-progress makes them wait on us while we wait on them.
    "pending pm approval": "Needs your approval",
    "pending approval": "Needs your approval",
    "in review": "In progress", "review": "In progress", "awaiting review": "In progress",
    "complete": "Done", "completed": "Done", "closed": "Done", "done": "Done",
}

# --- Ticket → property-profile loop (docs/ticket-to-profile-loop.md) ---------
# A completed ticket PROPOSES profile changes; a human accepts them. Nothing
# here ever writes on its own — see ticket_profile_sync.py.

# Master flag. Off → /api/ticket-profile/* 404s and ticket completion generates
# no proposals (process_completed_task behaves exactly as it did before).
TICKET_PROFILE_LOOP_ENABLED = (
    os.getenv("TICKET_PROFILE_LOOP_ENABLED", "").strip().lower() == "true"
)

# Optional LLM extractor. Off → only the deterministic field-name map below
# produces proposals. On → the ticket thread is also read by the model, which
# may only propose fields already allow-listed for that ticket type.
TICKET_PROFILE_AI_EXTRACT = (
    os.getenv("TICKET_PROFILE_AI_EXTRACT", "").strip().lower() == "true"
)

# ClickUp custom-field NAME (matched case-insensitively) → community_brief
# field key. This is the deterministic extractor: a completed ticket carrying
# "New Property Name" proposes that value for the profile's Advertised Name.
# Override or extend without a deploy via TICKET_PROFILE_FIELD_MAP_JSON
# ({"clickup field name": "brief_field_key"}) — supplied entries win.
TICKET_PROFILE_FIELD_MAP = {
    # Rebrands
    "new property name":       "advertised_name",
    "new advertised name":     "advertised_name",
    "new short name":          "short_name",
    "former property name":    "former_property_name",
    "previous property name":  "former_property_name",
    "old property name":       "former_property_name",
    # Creative & ad copy
    "taglines":                "taglines",
    "tagline":                 "taglines",
    "must include":            "must_include",
    "things not to say":       "forbidden_phrases",
    "forbidden phrases":       "forbidden_phrases",
    # Budget updates
    "new budget":              "marketing_budget",
    "new monthly budget":      "marketing_budget",
    "total monthly budget":    "marketing_budget",
    "marketing budget":        "marketing_budget",
    # New account build / onboarding
    "property amenities":      "property_amenities",
    "in-unit features":        "unit_features",
    "unit features":           "unit_features",
    "competitors":             "competitors",
    "neighborhood":            "neighborhood",
    "nearby employers":        "nearby_employers",
    "unit level details":      "unit_level_details",
    "unit-level details":      "unit_level_details",
}
try:
    _tpfm_extra = json.loads(os.getenv("TICKET_PROFILE_FIELD_MAP_JSON", "") or "{}")
    if isinstance(_tpfm_extra, dict):
        TICKET_PROFILE_FIELD_MAP.update(
            {str(k).strip().lower(): str(v).strip() for k, v in _tpfm_extra.items()}
        )
except (ValueError, TypeError):
    pass

# Which profile fields each ticket type may propose. A ticket type absent here
# proposes nothing — new types opt IN. dispo_cancel is deliberately absent and
# is additionally hard-excluded in ticket_profile_sync (it is already excluded
# from client-facing writes via ticket_recap.EXCLUDED_TYPES).
#
# NOTE on keys: two ticket-type vocabularies exist in this repo. The portal
# registry above uses `creative_ad_copy` / `campaign_review`; the completion
# path classifies by ClickUp LIST NAME via ticket_recap.infer_ticket_type(),
# which yields `creative_update` / `performance_review`. Proposals arrive on
# the infer_ticket_type() spelling, so both are listed and kept identical
# rather than silently proposing nothing for a renamed list.
_TICKET_PROFILE_CREATIVE_FIELDS = (
    "taglines", "must_include", "forbidden_phrases",
    "differentiators", "residents_love",
)
TICKET_PROFILE_TYPE_FIELDS = {
    "rebrand": (
        "advertised_name", "short_name", "former_property_name",
        "taglines", "brand_adjectives", "must_include", "forbidden_phrases",
    ),
    "creative_update": _TICKET_PROFILE_CREATIVE_FIELDS,
    "creative_ad_copy": _TICKET_PROFILE_CREATIVE_FIELDS,
    "budget_update": (
        "marketing_budget",
    ),
    "new_account_build": (
        "advertised_name", "short_name", "property_amenities", "unit_features",
        "unit_level_details", "competitors", "neighborhood", "nearby_employers",
        "landmarks", "differentiators", "must_include", "forbidden_phrases",
        "marketing_budget",
    ),
}

# --- Creatify Video Pipeline ---
CREATIFY_API_ID  = os.getenv("CREATIFY_API_ID", "")
CREATIFY_API_KEY = os.getenv("CREATIFY_API_KEY", "")
CREATIFY_BASE_URL = "https://api.creatify.ai"
CREATIFY_WEBHOOK_SECRET = os.getenv("CREATIFY_WEBHOOK_SECRET", "")
# Custom no-avatar template UUID (built in Creatify's web editor).
# Expects a single text variable named 'script'. Media passed via media_urls.
CREATIFY_TEMPLATE_ID = os.getenv("CREATIFY_TEMPLATE_ID", "")

# --- HeyGen (alternative video provider) ---
# Creatify always renders an avatar under its OverCards overlay; HeyGen v2 lets
# us build scenes with character.type = "none" for true avatar-free output.
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY", "")
HEYGEN_BASE_URL = os.getenv("HEYGEN_BASE_URL", "https://api.heygen.com")
HEYGEN_WEBHOOK_SECRET = os.getenv("HEYGEN_WEBHOOK_SECRET", "")

# Which provider to use when the enrollment request omits `provider`.
VIDEO_PROVIDER_DEFAULT = os.getenv("VIDEO_PROVIDER_DEFAULT", "creatify")

# --- NinjaCat ---
NINJACAT_EXPORT_BUCKET = os.getenv("NINJACAT_EXPORT_BUCKET")

# --- Webhook ---
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))

# --- URLs ---
PORTAL_BASE_URL = os.getenv("PORTAL_BASE_URL", "https://digital.rpmliving.com/client-portal")
NINJACAT_DASHBOARD_ID = os.getenv("NINJACAT_DASHBOARD_ID", "16866")

# --- Service Tiers ---
SEO_TIERS = {
    "Local": 100,
    "Lite": 300,
    "Basic": 500,
    "Standard": 800,
    "Premium": 1300,
}
SEO_TIER_ORDER = ["Local", "Lite", "Basic", "Standard", "Premium"]

# Feature gates for portal SEO Insights — tier a property must be on to see the feature.
SEO_FEATURE_MIN_TIER = {
    "dashboard":        "Local",     # everyone with any SEO package
    "keywords_read":    "Local",
    "keywords_write":   "Basic",
    "ai_mentions":      "Basic",
    "content_clusters": "Standard",  # Phase 2
    "content_briefs":   "Standard",  # Phase 2
    "content_decay":    "Premium",   # Phase 2
    "keyword_research": "Basic",     # Phase 3
    "trend_explorer":   "Standard",  # Phase 3
    # Onboarding + Paid Media (Phase 4)
    "brief_ai_draft":   "Local",     # all tiers can use AI draft
    "onboarding_keywords": "Basic",  # seed + classify + route
    "paid_targeting":   "Local",
    "paid_audiences":   "Local",
    "paid_creative":    "Local",
}

SOCIAL_POSTING_TIERS = {
    "Basic": 300,
    "Standard": 450,
    "Premium": 700,
}
SOCIAL_POSTING_SETUP_FEE = 500

REPUTATION_TIERS = {
    "Response Only": 190,
    "Response + Removal": 255,
}
REPUTATION_SETUP_FEE = 50

SETUP_FEES = {
    "social_posting": SOCIAL_POSTING_SETUP_FEE,
    "reputation": REPUTATION_SETUP_FEE,
}

PLE_STATUS_INCLUDE = ["RPM Managed", "Dispositioning", "Onboarding"]
PAID_MEDIA_REVIEW_WINDOW_HOURS = 48

# --- Onboarding/Discovery Pipeline (Phase 5) ---
# Target: deal-signed → live in 5-7 days. HubSpot workflow watches
# rpm_onboarding_status_changed_at and alerts the company owner if a stage
# exceeds its budget.
ONBOARDING_SLA_DAYS_TOTAL = 7
ONBOARDING_SLA_PER_STAGE_HOURS = {
    "intake_sent":              48,   # client has 2 days to start the form
    "intake_in_progress":       24,   # once started, finish within 1 day
    "brief_drafting":            6,   # AI draft is async, allow 6h headroom
    "brief_review":             24,   # CSM review window
    "strategy_in_build":        72,   # paid + SEO build in parallel
    "awaiting_client_approval": 24,
}
# Gap-review email workflow — see docs/handoffs/HUBSPOT_GAP_REVIEW_WORKFLOW.md.
GAP_REVIEW_TOKEN_TTL_DAYS = 7
GAP_REVIEW_REMINDER_HOURS = 24       # owner reminder if no email sent
GAP_REVIEW_ESCALATION_HOURS = 48     # escalate to RM if no CM response
GAP_REVIEW_FINAL_TIMEOUT_HOURS = 72  # flag onboarding_status=escalated

# AI-slop classifier threshold on free-text intake fields. Above this score,
# the field gets surfaced in gap_questions for CM verification.
AI_SLOP_FLAG_THRESHOLD = 0.7

# Fluency Blueprint asset size variants. Logos must be transparent PNG;
# property hero photos can be JPG with EXIF intact (provenance check).
# Asset uploader pre-resizes to these dims via Pillow before HubSpot Files
# upload — Fluency pulls the exact dim it needs, no server-side resize.
FLUENCY_ASSET_VARIANTS = {
    "logo": [
        {"role": "logo_square",    "width": 1200, "height": 1200, "fmt": "PNG"},
        {"role": "logo_landscape", "width": 1200, "height": 300,  "fmt": "PNG"},
        {"role": "logo_small",     "width": 600,  "height": 600,  "fmt": "PNG"},
        {"role": "favicon",        "width": 128,  "height": 128,  "fmt": "PNG"},
    ],
    "hero": [
        {"role": "hero_landscape", "width": 1200, "height": 628,  "fmt": "JPG"},
        {"role": "hero_square",    "width": 1200, "height": 1200, "fmt": "JPG"},
        {"role": "hero_portrait",  "width": 960,  "height": 1200, "fmt": "JPG"},
    ],
}

# Brand color extraction — pull top-N dominant colors from logo (k-means
# on pixel data, ignoring transparent + near-white/near-black regions).
# PMA picks primary/secondary from these swatches; CSM approves.
BRAND_COLOR_EXTRACT_COUNT = 5
BRAND_COLOR_REQUIRE_APPROVAL = True

# RPM Living email convention — used to derive Community Manager and
# Regional Manager names from their emails on intake submit.
RPM_EMAIL_DOMAIN = "rpmliving.com"
# Non-RPM-domain logins treated as internal staff (e.g. the owner's
# personal address). Comma-separated; lowercased. The HubDB portal-access
# table can also mark an email internal — this env is the no-HubDB path.
INTERNAL_EMAILS = {
    e.strip().lower()
    for e in os.getenv("INTERNAL_EMAILS", "").split(",")
    if e.strip()
}

# --- Portal ---
WEBHOOK_SERVER_URL = os.getenv("WEBHOOK_SERVER_URL", "http://localhost:8443")

# --- Asset Library ---
ASSET_CATEGORIES = ["Photography", "Video", "Brand & Creative", "Marketing Collateral"]
PHOTO_SUBCATEGORIES = ["Exterior", "Interior", "Amenity", "Aerial", "Neighborhood"]
VIDEO_SUBCATEGORIES = ["Ad Creative", "Property Tour", "Testimonial"]
MAX_UPLOAD_SIZE_MB = 100
ALLOWED_IMAGE_TYPES = ["jpg", "jpeg", "png", "gif", "webp"]
ALLOWED_VIDEO_TYPES = ["mp4", "mov"]
ALLOWED_DOC_TYPES   = ["pdf", "ai", "eps", "psd", "svg"]

# --- Included Services (per-unit pricing) ---
INCLUDED_SERVICES = [
    {"name": "CRM Lead Management", "per_unit": 1.50, "type": "per_unit"},
    {"name": "Customer Experience", "per_unit": 0.55, "type": "per_unit"},
    {"name": "Website Hosting", "tiers": {"Basic": 50, "Semi-Custom": 205, "Custom": 205}, "type": "tier", "property": "website_hosting_type"},
    {"name": "SOCi", "flat": 60, "type": "flat"},
    {"name": "Marketing Self-Service", "flat": 65, "type": "flat"},
    {"name": "Training", "type": "unit_band", "bands": [
        {"min": 1, "max": 149, "price": 100},
        {"min": 150, "max": 299, "price": 150},
        {"min": 300, "max": 499, "price": 200},
        {"min": 500, "max": 999, "price": 275},
        {"min": 1000, "max": 99999, "price": 350},
    ]},
    {"name": "Regional Facilities", "type": "unit_range", "min_per_unit": 0.75, "max_per_unit": 1.75},
]


def get_setup_fee(service, current_tier):
    """Return setup fee: charged for new enrollments, $0 for upgrades."""
    if current_tier in (None, "None", ""):
        return SETUP_FEES.get(service, 0)
    return 0
