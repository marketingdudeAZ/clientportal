#!/usr/bin/env python3
"""Reliably deploy client-portal.html to HubSpot.

Why this exists — THREE traps this script avoids:

  TRAP 1 — Multiple template files with similar names
    HubSpot had 4 different files all named some variant of "client-portal.html".
    The `hs cms upload` CLI defaulted to uploading to the wrong one silently.
    Fix: always use template ID 210982557303 (canonical) — no path guessing.

  TRAP 2 — Two separate HubSpot template APIs
    v3 Source Code API (cms/v3/source-code/...) — newer, API-friendly
    v2 Content API     (content/api/v2/templates/{id}) — OLDER, what the
                                                          renderer actually reads
    They are NOT synced. Uploads must go through v2 for the live page to see them.

  TRAP 3 — Cloudflare prerender cache
    HubSpot prerenders pages and caches the HTML at the edge for up to 10 hours.
    Template updates don't auto-invalidate this cache. The only reliable busts:
      a) Change the page's URL slug → new cache key
      b) Wait 10 hours

Usage:
    python3 scripts/deploy_template.py                          # client-portal
    python3 scripts/deploy_template.py --target workspace-v2    # /client-portal/v2

Targets:
    client-portal  (default)  hubspot-cms/templates/client-portal.html
    workspace-v2              hubspot-cms/templates/client-portal-v2.html — the
                              loader page for the client workspace. It holds no
                              app code: the app is served from Render and the
                              template must stay a few KB, which is what its
                              validator enforces. Its HubSpot template and page
                              IDs are not hard-coded here because the page is
                              created once, by hand, in HubSpot; set
                              HUBSPOT_V2_TEMPLATE_ID and HUBSPOT_V2_PAGE_ID
                              (see docs/PORTAL_V2_HOSTING.md) and then paste
                              them into TARGETS below so nobody has to guess.

Exit codes:
    0 = template uploaded via v2, verified stored, live page confirmed updated
    1 = upload or verification failed (see logs)
    2 = upload succeeded but live page still stale;
        follow printed instructions to change URL slug
"""
import argparse, os, re, sys, time, requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

KEY = os.getenv("HUBSPOT_API_KEY")
if not KEY:
    print("ERROR: HUBSPOT_API_KEY not set in .env", file=sys.stderr)
    sys.exit(1)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(REPO, "hubspot-cms", "templates")
LOCAL = os.path.join(TEMPLATES, "client-portal.html")
LOCAL_V2 = os.path.join(TEMPLATES, "client-portal-v2.html")

# Canonical HubSpot identifiers — DO NOT CHANGE without testing
TEMPLATE_ID = "210982557303"   # v2 template ID for client-portal.html
PAGE_ID = "209266222927"       # RPM Client Portal site page

# Try both URL slugs — script succeeds if EITHER serves the new render
LIVE_URLS = [
    "https://digital.rpmliving.com/portal-dashboard?uuid=10559996814",
    "https://digital.rpmliving.com/client-portal?uuid=10559996814",
]

# Sentinel strings the live page MUST contain for a successful deploy
REQUIRED_SENTINELS = [
    "seoCheckEntitlement",
    "contentCheckEntitlement",
    "addEventListener('portal-data-ready'",
]


def _h(json_body=False):
    h = {"Authorization": f"Bearer {KEY}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HUBL_DIRECTIVE_RE = re.compile(r"{%\s*(if|else|elif|endif|for|endfor|set)\b")


def find_hubl_directives_in_html_comments(source):
    """Return list of (line_no, snippet) for any HubL directive token found
    inside an HTML comment.

    HubL processes templating directives BEFORE HTML parsing, so any
    `{% if %}`, `{% else %}`, etc. that appears inside an English-language
    HTML comment is treated as a real directive and silently scrambles the
    branch structure. This bit us on 2026-04-28 — a self-referential comment
    in client-portal.html ("Mirror copy lives in the {% else %} portfolio
    branch.") terminated the property branch 450 lines early, dropping all
    the slide-over IIFE code from the rendered output.

    Returns: list of (line_no_1indexed, comment_snippet) tuples. Empty list
             means clean.
    """
    hits = []
    for m in _HTML_COMMENT_RE.finditer(source):
        body = m.group(0)
        if _HUBL_DIRECTIVE_RE.search(body):
            line_no = source.count("\n", 0, m.start()) + 1
            hits.append((line_no, body[:160]))
    return hits


def validate_hubl_structure(source):
    """Fail fast if the IIFE JS blocks ended up in the wrong HubL branch.

    Template has {% if uuid_param %}...{% else %}...{% endif %}. Phase 1/2/3 JS
    must be inside the IF branch; if appended to end-of-file (common Claude edit
    mistake), they land in the ELSE branch and never run on /client-portal?uuid=X.

    Also catches HubL directives accidentally written inside HTML comment
    text (see find_hubl_directives_in_html_comments docstring).
    """
    # Check 1: structural — IIFE inside IF branch
    if_pos = source.find("{% if uuid_param %}")
    else_pos = source.find("{% else %}")
    endif_pos = source.rfind("{% endif %}")
    seo_check_pos = source.find("function seoCheckEntitlement")

    if -1 in (if_pos, else_pos, endif_pos):
        return False, "HubL if/else/endif markers missing in template"
    if seo_check_pos == -1:
        return False, "seoCheckEntitlement function missing from template"
    if not (if_pos < seo_check_pos < else_pos):
        return False, (
            f"seoCheckEntitlement at position {seo_check_pos} is OUTSIDE the "
            f"{{% if uuid_param %}} branch (if={if_pos}, else={else_pos}). "
            f"The JS IIFEs were appended to the wrong branch. "
            f"Move them before the </script></body></html>{{% else %}} block."
        )

    # Check 2: no HubL directives inside HTML comments — they get parsed
    # before HTML, scrambling the branch structure.
    rogue = find_hubl_directives_in_html_comments(source)
    if rogue:
        lines = "\n".join(f"  line {ln}: {snip!r}" for ln, snip in rogue)
        return False, (
            f"HubL directive found inside {len(rogue)} HTML comment(s). "
            f"HubL parses directives before HTML, so writing `{{% else %}}` "
            f"or `{{% if %}}` inside comment text creates a phantom branch "
            f"that scrambles the live render. Rephrase the comment(s):\n"
            f"{lines}"
        )

    return True, None


# Markers that would mean the workspace app had been pasted into the v2 loader
# template. The whole point of that template is that it holds no app code.
_V2_APP_LEAKS = ("function api(", "function reportHref(", "__PORTAL_API_BASE__ = '", "--ground:")
_V2_MAX_BYTES = 20_000


def validate_workspace_v2(source):
    """Fail fast if the v2 loader template stopped being a loader.

    Three ways this page can break, all of them silent for ~10 hours because
    Cloudflare caches the render:

      1. The app gets pasted in. Then there are two copies of a 3,200-line
         file, and this one is the copy nobody remembers to update.
      2. The loader script ends up below an else marker (DEPLOY.md Trap 4) and
         never runs, leaving a blank page.
      3. The version is dropped from the asset URL, so there is no way to tell
         which render is live.
    """
    if len(source) > _V2_MAX_BYTES:
        return False, (
            f"client-portal-v2.html is {len(source)} bytes (limit {_V2_MAX_BYTES}). "
            f"It is a loader: the app lives in webhook-server/portal_pages/"
            f"workspace.html and is served from Render.")
    leaks = [m for m in _V2_APP_LEAKS if m in source]
    if leaks:
        return False, (
            f"the workspace app looks pasted into the template ({leaks}). "
            f"Keep one source of truth — edit portal_pages/workspace.html.")
    if "/workspace/embed.js?v=" not in source:
        return False, "the loader <script src=.../workspace/embed.js?v=...> is missing"
    if not re.search(r"{%\s*set\s+app_version\s*=\s*[\"'][^\"']+[\"']\s*%}", source):
        return False, ("app_version is unset — bump it on every change or "
                       "Cloudflare serves the previous render for ~10 hours")
    else_pos = source.find("{% else %}")
    loader_pos = source.find("/workspace/embed.js?v=")
    if else_pos != -1 and loader_pos > else_pos:
        return False, (
            f"the loader is at position {loader_pos}, below the else marker at "
            f"{else_pos} (DEPLOY.md Trap 4) — it will upload cleanly and never "
            f"run. Move it above the else.")
    rogue = find_hubl_directives_in_html_comments(source)
    if rogue:
        lines = "\n".join(f"  line {ln}: {snip!r}" for ln, snip in rogue)
        return False, f"HubL directive inside an HTML comment:\n{lines}"
    return True, None


def _env_id(name):
    v = (os.getenv(name) or "").strip()
    return v or None


# Everything a target needs to deploy. Add a target here rather than editing
# main(); the IDs are the part that must never be guessed (Trap 1).
TARGETS = {
    "client-portal": {
        "local": LOCAL,
        "template_id": "210982557303",
        "page_id": "209266222927",
        "live_urls": LIVE_URLS,
        "sentinels": REQUIRED_SENTINELS,
        "validate": validate_hubl_structure,
        "ok_note": "Pre-flight OK — IIFEs inside IF branch",
    },
    "workspace-v2": {
        "local": LOCAL_V2,
        # Created by hand in HubSpot once, then pasted here. Until then they
        # come from the environment so a deploy can never hit the wrong page.
        "template_id": _env_id("HUBSPOT_V2_TEMPLATE_ID"),
        "page_id": _env_id("HUBSPOT_V2_PAGE_ID"),
        "live_urls": ["https://digital.rpmliving.com/client-portal/v2"],
        "sentinels": ["/workspace/embed.js?v=", 'id="rpm-workspace"'],
        "validate": validate_workspace_v2,
        "ok_note": "Pre-flight OK — loader only, versioned, above any else",
    },
}


def upload_v2(source, template_id=TEMPLATE_ID):
    """Upload via v2 Content API (what HubSpot's renderer reads from)."""
    url = f"https://api.hubapi.com/content/api/v2/templates/{template_id}"
    r = requests.put(url, headers=_h(json_body=True), json={"source": source})
    print(f"  v2 PUT template {template_id}: {r.status_code}")
    if r.status_code >= 400:
        print(f"    {r.text[:300]}", file=sys.stderr)
        sys.exit(1)


def verify_stored(template_id=TEMPLATE_ID, sentinels=REQUIRED_SENTINELS):
    """Confirm template has required sentinels post-upload."""
    r = requests.get(f"https://api.hubapi.com/content/api/v2/templates/{template_id}", headers=_h())
    if r.status_code != 200:
        print(f"  v2 GET returned {r.status_code}", file=sys.stderr)
        sys.exit(1)
    body = r.json().get("source", "")
    missing = [s for s in sentinels if s not in body]
    if missing:
        print(f"  ABORT: template missing sentinels: {missing}", file=sys.stderr)
        sys.exit(1)
    print(f"  Verified ({len(body)} bytes, all sentinels present)")


def republish_page(page_id=PAGE_ID):
    r = requests.post(f"https://api.hubapi.com/cms/v3/pages/site-pages/{page_id}/draft/reset",
                      headers=_h(json_body=True), json={})
    print(f"  draft/reset: {r.status_code}")
    r = requests.post(f"https://api.hubapi.com/cms/v3/pages/site-pages/{page_id}/draft/push-live",
                      headers=_h(json_body=True), json={})
    print(f"  push-live: {r.status_code}")


def wait_for_live(timeout_s=180, live_urls=None, sentinels=REQUIRED_SENTINELS):
    start = time.time()
    while time.time() < start + timeout_s:
        for url in (live_urls or LIVE_URLS):
            probe = url + ("&" if "?" in url else "?") + f"_={int(time.time())}"
            r = requests.get(probe)
            if r.status_code == 200:
                body = r.text
                missing = [s for s in sentinels if s not in body]
                if not missing:
                    print(f"  ✅ LIVE: {url} ({len(body)} bytes)")
                    return True
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] still stale — retrying...")
        time.sleep(15)
    return False


def main(target="client-portal"):
    t = TARGETS[target]
    if not t["template_id"] or not t["page_id"]:
        print(f"ERROR: {target} has no HubSpot template/page ID yet.", file=sys.stderr)
        print("       Create the page in HubSpot once, then set "
              "HUBSPOT_V2_TEMPLATE_ID and HUBSPOT_V2_PAGE_ID (or paste them "
              "into TARGETS). See docs/PORTAL_V2_HOSTING.md.", file=sys.stderr)
        sys.exit(1)

    print(f"Deploy {target} → template ID {t['template_id']} (page {t['page_id']})\n")

    with open(t["local"]) as f:
        source = f.read()
    print(f"Local template: {len(source)} bytes")

    # Pre-flight check: don't even upload if the template's structure is wrong
    ok, err = t["validate"](source)
    if not ok:
        print(f"\n❌ PRE-FLIGHT FAILED: {err}", file=sys.stderr)
        sys.exit(1)
    print(f"  {t['ok_note']}")

    print("\nUploading via v2 API...")
    upload_v2(source, t["template_id"])
    time.sleep(2)

    print("\nVerifying storage...")
    verify_stored(t["template_id"], t["sentinels"])

    print("\nRepublishing page...")
    republish_page(t["page_id"])

    print(f"\nWaiting up to 180s for CDN...")
    if wait_for_live(live_urls=t["live_urls"], sentinels=t["sentinels"]):
        print("\n✅ Deploy complete.")
        sys.exit(0)
    print("\n⚠️  Stored + republished but live is stale. Cloudflare is holding.", file=sys.stderr)
    print("    Fix: open https://app.hubspot.com/website/19843861/pages/{}/edit".format(t["page_id"]), file=sys.stderr)
    print("         Settings → General → change URL slug (e.g. add '-v2') → Update", file=sys.stderr)
    print("         This gives Cloudflare a fresh cache key.", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", default="client-portal", choices=sorted(TARGETS),
                    help="which template to deploy (default: client-portal)")
    main(ap.parse_args().target)
