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
}

# --- Creatify Video Pipeline ---
CREATIFY_API_ID  = os.getenv("CREATIFY_API_ID", "")
CREATIFY_API_KEY = os.getenv("CREATIFY_API_KEY", "")
CREATIFY_BASE_URL = "https://api.creatify.ai"
CREATIFY_WEBHOOK_SECRET = os.getenv("CREATIFY_WEBHOOK_SECRET", "")
# Custom no-avatar template UUID (built in Creatify's web editor).
# Expects a single text variable named 'script'. Media passed via media_urls.
CREATIFY_TEMPLATE_ID = os.getenv("CREATIFY_TEMPLATE_ID", "")

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

# --- Portal ---
WEBHOOK_SERVER_URL = os.getenv("WEBHOOK_SERVER_URL", "http://localhost:8443")

# --- Asset Library ---
ASSET_CATEGORIES = ["Photography", "Video", "Brand & Creative", "Marketing Collateral"]
PHOTO_SUBCATEGORIES = ["Exterior", "Interior", "Amenity", "Aerial", "Neighborhood"]
VIDEO_SUBCATEGORIES = ["Ad Creative", "Property Tour", "Testimonial"]
MAX_UPLOAD_SIZE_MB = 100
ALLOWED_IMAGE_TYPES = ["jpg", "jpeg", "png", "webp"]
ALLOWED_VIDEO_TYPES = ["mp4", "mov"]
ALLOWED_DOC_TYPES = ["pdf", "ai", "eps", "psd", "svg"]

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
