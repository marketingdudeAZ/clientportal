"""Create Products in HubSpot for the budget configurator.

One Product per tier per service, matching the pricing table in Section 4.3.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from config import HUBSPOT_API_KEY

API_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

PRODUCTS = [
    # SEO
    {"name": "SEO — Local", "price": "100", "gl_code": "51000000", "setup": "0"},
    {"name": "SEO — Lite", "price": "300", "gl_code": "51000000", "setup": "0"},
    {"name": "SEO — Basic", "price": "500", "gl_code": "51000000", "setup": "0"},
    {"name": "SEO — Standard", "price": "800", "gl_code": "51000000", "setup": "0"},
    {"name": "SEO — Premium", "price": "1300", "gl_code": "51000000", "setup": "0"},
    # Social Posting
    {"name": "Social Posting — Basic", "price": "300", "gl_code": "51010002", "setup": "500"},
    {"name": "Social Posting — Standard", "price": "450", "gl_code": "51010002", "setup": "500"},
    {"name": "Social Posting — Premium", "price": "700", "gl_code": "51010002", "setup": "500"},
    # Reputation
    {"name": "Reputation — Response Only", "price": "190", "gl_code": "51010002", "setup": "50"},
    {"name": "Reputation — Response + Removal", "price": "255", "gl_code": "51010002", "setup": "50"},
    # Paid Media (variable pricing)
    {"name": "Paid Search — Google Ads", "price": "0", "gl_code": "51000000", "setup": "0"},
    {"name": "Paid Social — Meta/Facebook", "price": "0", "gl_code": "51000000", "setup": "0"},
]


def main():
    print("Seeding HubSpot Product catalog...\n")
    created = 0

    for product in PRODUCTS:
        payload = {
            "properties": {
                "name": product["name"],
                "price": product["price"],
                "recurringbillingfrequency": "monthly",
                "description": f"GL Code: {product['gl_code']}. Setup fee: ${product['setup']} if new enrollment.",
            }
        }

        resp = requests.post(
            f"{API_BASE}/crm/v3/objects/products",
            headers=HEADERS,
            json=payload,
        )

        if resp.status_code in (200, 201):
            pid = resp.json()["id"]
            print(f"  Created: {product['name']} (ID: {pid})")
            created += 1
        elif resp.status_code == 409:
            print(f"  Exists:  {product['name']}")
        else:
            print(f"  FAILED:  {product['name']} — {resp.status_code}: {resp.text}")

    print(f"\nDone. Created {created}/{len(PRODUCTS)} products.")


if __name__ == "__main__":
    main()
