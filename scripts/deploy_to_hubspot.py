"""Deploy CMS templates to HubSpot Design Manager via Content API v2."""

import json
import os
import sys
import time

import requests

API_KEY = os.environ.get("HUBSPOT_API_KEY", "")
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
BASE_URL = "https://api.hubapi.com/content/api/v2/templates"
CMS_DIR = os.path.join(os.path.dirname(__file__), "..", "hubspot-cms")

# template_type values:
# 2 = page, 4 = email, 6 = blog listing, 13 = blog post,
# 22 = coded_file (JS/HTML partials), 24 = stylesheet

DEPLOY_MANIFEST = [
    # CSS files
    {"local": "css/portal.css", "path": "custom/client-portal/css/portal.css", "type": 24, "label": "portal.css"},
    {"local": "css/gauges.css", "path": "custom/client-portal/css/gauges.css", "type": 24, "label": "gauges.css"},
    {"local": "css/asset-library.css", "path": "custom/client-portal/css/asset-library.css", "type": 24, "label": "asset-library.css"},
    {"local": "css/configurator.css", "path": "custom/client-portal/css/configurator.css", "type": 24, "label": "configurator.css"},
    # JS files
    {"local": "js/portal.js", "path": "custom/client-portal/js/portal.js", "type": 22, "label": "portal.js"},
    {"local": "js/gauges.js", "path": "custom/client-portal/js/gauges.js", "type": 22, "label": "gauges.js"},
    {"local": "js/asset-library.js", "path": "custom/client-portal/js/asset-library.js", "type": 22, "label": "asset-library.js"},
    {"local": "js/configurator.js", "path": "custom/client-portal/js/configurator.js", "type": 22, "label": "configurator.js"},
    # Partial templates (HubL)
    {"local": "templates/partials/identity-block.html", "path": "custom/client-portal/partials/identity-block.html", "type": 22, "label": "identity-block.html"},
    {"local": "templates/partials/included-services.html", "path": "custom/client-portal/partials/included-services.html", "type": 22, "label": "included-services.html"},
    {"local": "templates/partials/report-card.html", "path": "custom/client-portal/partials/report-card.html", "type": 22, "label": "report-card.html"},
    {"local": "templates/partials/packages-card.html", "path": "custom/client-portal/partials/packages-card.html", "type": 22, "label": "packages-card.html"},
    {"local": "templates/partials/seo-deliverables.html", "path": "custom/client-portal/partials/seo-deliverables.html", "type": 22, "label": "seo-deliverables.html"},
    {"local": "templates/partials/health-score.html", "path": "custom/client-portal/partials/health-score.html", "type": 22, "label": "health-score.html"},
    {"local": "templates/partials/gauge.html", "path": "custom/client-portal/partials/gauge.html", "type": 22, "label": "gauge.html"},
    {"local": "templates/partials/recommendations.html", "path": "custom/client-portal/partials/recommendations.html", "type": 22, "label": "recommendations.html"},
    {"local": "templates/partials/asset-library.html", "path": "custom/client-portal/partials/asset-library.html", "type": 22, "label": "asset-library.html"},
    {"local": "templates/partials/asset-lightbox.html", "path": "custom/client-portal/partials/asset-lightbox.html", "type": 22, "label": "asset-lightbox.html"},
    {"local": "templates/partials/asset-upload-form.html", "path": "custom/client-portal/partials/asset-upload-form.html", "type": 22, "label": "asset-upload-form.html"},
    {"local": "templates/partials/configurator.html", "path": "custom/client-portal/partials/configurator.html", "type": 22, "label": "configurator.html"},
    {"local": "templates/partials/running-total.html", "path": "custom/client-portal/partials/running-total.html", "type": 22, "label": "running-total.html"},
    {"local": "templates/partials/tier-card.html", "path": "custom/client-portal/partials/tier-card.html", "type": 22, "label": "tier-card.html"},
    # Dashboard partial
    {"local": "templates/partials/dashboard.html", "path": "custom/client-portal/partials/dashboard.html", "type": 22, "label": "dashboard.html"},
    # Dashboard CSS/JS
    {"local": "css/dashboard.css", "path": "custom/client-portal/css/dashboard.css", "type": 24, "label": "dashboard.css"},
    {"local": "js/dashboard.js", "path": "custom/client-portal/js/dashboard.js", "type": 22, "label": "dashboard.js"},
    # Onboarding intake — partial + standalone CSS/JS. The main client-portal
    # template inlines the same content; these uploaded copies exist so future
    # templates (or a HubL `{% include %}` migration) can reuse them.
    {"local": "templates/partials/onboarding-intake.html", "path": "custom/client-portal/partials/onboarding-intake.html", "type": 22, "label": "onboarding-intake.html"},
    {"local": "css/onboarding-intake.css", "path": "custom/client-portal/css/onboarding-intake.css", "type": 24, "label": "onboarding-intake.css"},
    {"local": "js/onboarding-intake.js", "path": "custom/client-portal/js/onboarding-intake.js", "type": 22, "label": "onboarding-intake.js"},
    # Page error template
    {"local": "templates/portal-error.html", "path": "custom/client-portal/portal-error.html", "type": 22, "label": "portal-error.html"},
    # Main page template — uses direct PUT to avoid 409 path conflict.
    # HubSpot has two template entries with conflicting paths:
    #   ID 209383472929  path: custom/client-portal/client-portal.html
    #   ID 210702377297  path: custom/client-portal/Client Portal  ← LIVE (served to users)
    # The deploy_main_template() function below handles this correctly.
]

# Template IDs — direct PUT targets (avoid path conflict / different root)
MAIN_TEMPLATE_ID = 210702377297   # custom/client-portal/Client Portal (live portal)
DEMO_TEMPLATE_ID = 210277860698   # templates/rpm-portal-demo.html (demo page)


def get_existing_templates():
    """Fetch all templates in our folder to check for duplicates."""
    existing = {}
    resp = requests.get(
        BASE_URL,
        headers=HEADERS,
        params={"path__contains": "custom/client-portal", "limit": 100},
    )
    if resp.status_code == 200:
        for t in resp.json().get("objects", []):
            existing[t["path"]] = t["id"]
    return existing


def deploy_template(item, existing):
    """Create or update a single template."""
    local_path = os.path.join(CMS_DIR, item["local"])
    if not os.path.exists(local_path):
        print(f"  SKIP (file not found): {item['local']}")
        return None

    with open(local_path) as f:
        source = f.read()

    payload = {
        "source": source,
        "path": item["path"],
        "template_type": item["type"],
        "label": item["label"],
        "is_available_for_new_content": item.get("page", False),
    }

    template_id = existing.get(item["path"])

    if template_id:
        # Update existing
        resp = requests.put(
            f"{BASE_URL}/{template_id}",
            headers=HEADERS,
            json=payload,
        )
        action = "UPDATED"
    else:
        # Create new
        resp = requests.post(BASE_URL, headers=HEADERS, json=payload)
        action = "CREATED"

    if resp.status_code in (200, 201):
        data = resp.json()
        print(f"  {action}: {item['path']} (ID: {data.get('id')})")
        return data.get("id")
    else:
        print(f"  FAILED ({resp.status_code}): {item['path']}")
        try:
            err = resp.json()
            print(f"    Error: {err.get('message', err)}")
        except Exception:
            print(f"    Response: {resp.text[:200]}")
        return None


def deploy_main_template():
    """Deploy client-portal.html directly to the known live template ID."""
    local_path = os.path.join(CMS_DIR, "templates/client-portal.html")
    if not os.path.exists(local_path):
        print("  SKIP (file not found): templates/client-portal.html")
        return False
    with open(local_path) as f:
        source = f.read()
    resp = requests.put(
        f"{BASE_URL}/{MAIN_TEMPLATE_ID}",
        headers=HEADERS,
        json={
            "source": source,
            "path": "custom/client-portal/Client Portal",
            "template_type": 2,
            "label": "Client Portal",
            "is_available_for_new_content": True,
        },
    )
    if resp.status_code == 200:
        print(f"  UPDATED: client-portal.html (ID: {MAIN_TEMPLATE_ID})")
        return True
    else:
        print(f"  FAILED ({resp.status_code}): client-portal.html (ID: {MAIN_TEMPLATE_ID})")
        try:
            print(f"    Error: {resp.json().get('message', resp.text[:200])}")
        except Exception:
            print(f"    Response: {resp.text[:200]}")
        return False


def deploy_demo_template():
    """Deploy demo.html directly to the known demo template ID."""
    local_path = os.path.join(os.path.dirname(__file__), "..", "demo.html")
    if not os.path.exists(local_path):
        print("  SKIP (file not found): demo.html")
        return False
    with open(local_path) as f:
        source = f.read()
    resp = requests.put(
        f"{BASE_URL}/{DEMO_TEMPLATE_ID}",
        headers=HEADERS,
        json={
            "source": source,
            "path": "templates/rpm-portal-demo.html",
            "template_type": 2,
            "label": "rpm-portal-demo.html",
            "is_available_for_new_content": False,
        },
    )
    if resp.status_code == 200:
        print(f"  UPDATED: demo.html (ID: {DEMO_TEMPLATE_ID})")
        return True
    else:
        print(f"  FAILED ({resp.status_code}): demo.html (ID: {DEMO_TEMPLATE_ID})")
        try:
            print(f"    Error: {resp.json().get('message', resp.text[:200])}")
        except Exception:
            print(f"    Response: {resp.text[:200]}")
        return False


def main():
    print("Fetching existing templates...")
    existing = get_existing_templates()
    print(f"Found {len(existing)} existing client-portal templates\n")

    success = 0
    failed = 0

    for item in DEPLOY_MANIFEST:
        result = deploy_template(item, existing)
        if result:
            success += 1
        else:
            failed += 1
        # Rate limit: 10 requests per second
        time.sleep(0.15)

    # Deploy main portal template via direct PUT (avoids 409 path conflict)
    if deploy_main_template():
        success += 1
    else:
        failed += 1

    # Deploy demo template (separate HubSpot template from main portal)
    if deploy_demo_template():
        success += 1
    else:
        failed += 1

    total = len(DEPLOY_MANIFEST) + 2
    print(f"\nDone: {success} deployed, {failed} failed out of {total} total")


if __name__ == "__main__":
    main()
