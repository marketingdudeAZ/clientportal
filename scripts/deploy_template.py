#!/usr/bin/env python3
"""Reliably deploy client-portal.html to HubSpot.

Why this exists:
  The `hs cms upload` CLI defaults to uploading to `client-portal.html` (root)
  but the live page uses `templates/client-portal.html`. Silent mismatch — CLI
  reports success but the live page never updates.

  This script uploads directly via the HubSpot Source Code API to the path the
  live page actually references, then force-republishes the page.

Usage:
    python scripts/deploy_template.py
"""
import os, sys, time, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

KEY = os.getenv("HUBSPOT_API_KEY")
if not KEY:
    print("ERROR: HUBSPOT_API_KEY not set"); sys.exit(1)

LOCAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "hubspot-cms", "templates", "client-portal.html")
TEMPLATE_PATH = "templates/client-portal.html"   # live page's templatePath
PAGE_ID = "209266222927"                          # RPM Client Portal site page

def main():
    with open(LOCAL, 'rb') as f:
        raw = f.read()
    print(f"Local file: {len(raw)} bytes")

    # Upload draft + published via multipart (JSON body returns 415)
    h_bare = {"Authorization": f"Bearer {KEY}"}
    h_json = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

    for slot in ["draft", "published"]:
        url = f"https://api.hubapi.com/cms/v3/source-code/{slot}/content/{TEMPLATE_PATH}"
        files = {'file': ('client-portal.html', raw, 'text/html')}
        r = requests.put(url, headers=h_bare, files=files)
        print(f"  {slot.upper()} PUT: {r.status_code}")
        if r.status_code >= 400:
            print(f"    {r.text[:200]}")
            sys.exit(1)

    # Force page republish
    for endpoint in ["/draft/reset", "/draft/push-live"]:
        r = requests.post(f"https://api.hubapi.com/cms/v3/pages/site-pages/{PAGE_ID}{endpoint}",
                          headers=h_json, json={})
        print(f"  page {endpoint}: {r.status_code}")

    print("\nWaiting 30s for CDN flush...")
    time.sleep(30)

    live = requests.get(f"https://digital.rpmliving.com/client-portal?uuid=10559996814&_={int(time.time())}").text
    mojibake = live.count('‚Ä¶')
    clean = live.count('…')
    print(f"  Live page: mojibake={mojibake}  clean_ellipses={clean}")
    if mojibake > 0:
        print("  ⚠️  Mojibake still present — HubSpot cache may need another minute.")
    else:
        print("  ✅ Clean render confirmed.")

if __name__ == "__main__":
    main()
