"""RPM Client Portal configuration constants."""

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
CLICKUP_LISTS = {
    "seo": os.getenv("CLICKUP_LIST_SEO"),
    "paid_media": os.getenv("CLICKUP_LIST_PAID_MEDIA"),
    "social": os.getenv("CLICKUP_LIST_SOCIAL"),
    "reputation": os.getenv("CLICKUP_LIST_REPUTATION"),
    "onboarding": os.getenv("CLICKUP_LIST_ONBOARDING"),
    # Property brief intake — drives the automation in property_brief.py.
    "property_brief": os.getenv("CLICKUP_LIST_PROPERTY_BRIEF"),
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
    "intake_sent":              48,
    "intake_in_progress":       24,
    "brief_drafting":            6,
    "brief_review":             24,
    "strategy_in_build":        72,
    "awaiting_client_approval": 24,
}
GAP_REVIEW_TOKEN_TTL_DAYS = 7
GAP_REVIEW_REMINDER_HOURS = 24
GAP_REVIEW_ESCALATION_HOURS = 48
GAP_REVIEW_FINAL_TIMEOUT_HOURS = 72
AI_SLOP_FLAG_THRESHOLD = 0.7

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
BRAND_COLOR_EXTRACT_COUNT = 5
BRAND_COLOR_REQUIRE_APPROVAL = True
RPM_EMAIL_DOMAIN = "rpmliving.com"

# --- Portal ---
WEBHOOK_SERVER_URL = os.getenv("WEBHOOK_SERVER_URL", "http://localhost:8443")

# --- Asset Library ---
ASSET_CATEGORIES = ["Photography", "Video", "Brand & Creative", "Marketing Collateral"]
PHOTO_SUBCATEGORIES = ["Exterior", "Interior", "Amenity", "Aerial", "Neighborhood"]
VIDEO_SUBCATEGORIES = ["Ad Creative", "Property Tour", "Testimonial"]
MAX_UPLOAD_SIZE_MB = 100
ALLOWED_IMAGE_TYPES = ["jpg", "jpeg", "png", "gif", "webp"]
ALLOWED_VIDEO_TYPES = ["mp4", "mov"]
# pdf for finished docs; ai/eps/psd/svg for raw creative source files clients
# routinely upload alongside finished assets.
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
