"""Create HubSpot CRM properties needed for the Portfolio Dashboard.

Company properties: marketing_manager_email, marketing_director_email, marketing_rvp_email
Contact properties: portal_role, portal_last_login
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

COMPANY_PROPERTIES = [
    {
        "name": "marketing_manager_email",
        "label": "Marketing Manager Email",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": "Email of the assigned Marketing Manager (for portal access).",
    },
    {
        "name": "marketing_director_email",
        "label": "Marketing Director Email",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": "Email of the assigned Marketing Director (for portal access).",
    },
    {
        "name": "marketing_rvp_email",
        "label": "Marketing RVP Email",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": "Email of the assigned Marketing RVP (for portal access).",
    },
]

CONTACT_PROPERTIES = [
    {
        "name": "portal_role",
        "label": "Portal Role",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "Role determining which properties this user can see in the portal.",
        "options": [
            {"label": "Marketing Manager", "value": "marketing_manager", "displayOrder": 0},
            {"label": "Marketing Director", "value": "marketing_director", "displayOrder": 1},
            {"label": "Marketing RVP", "value": "marketing_rvp", "displayOrder": 2},
        ],
    },
    {
        "name": "portal_last_login",
        "label": "Portal Last Login",
        "type": "datetime",
        "fieldType": "date",
        "groupName": "contactinformation",
        "description": "Timestamp of the user's last portal login.",
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
    print("Creating Company properties...")
    for prop in COMPANY_PROPERTIES:
        create_property("companies", prop)

    print("\nCreating Contact properties...")
    for prop in CONTACT_PROPERTIES:
        create_property("contacts", prop)

    print("\nDone.")


if __name__ == "__main__":
    main()
