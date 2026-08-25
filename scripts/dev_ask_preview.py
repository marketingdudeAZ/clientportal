#!/usr/bin/env python3
"""Run the Ask surface locally, exactly as it will look in the portal.

    python3 scripts/dev_ask_preview.py
    open http://127.0.0.1:5055

Why this exists: the Ask UI lives inside `hubspot-cms/templates/client-portal.html`,
which is a HubL template rendered by HubSpot CMS. It cannot be opened locally, and
deploying it to see it costs a template upload plus up to ten hours of Cloudflare
cache. This serves the real markup, the real CSS and the real JavaScript — lifted
out of that template at runtime, so it cannot drift from what ships — against a
local Flask app running the real /api/ask blueprint against production data.

Same origin, so no CORS. Local only; nothing here is imported by the server.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "hubspot-cms" / "templates" / "client-portal.html"

sys.path.insert(0, str(REPO / "webhook-server"))


def _load_env() -> None:
    """Read .env, then map the two names whose values live under other keys.

    The BIGQUERY_* entries in .env are placeholders; the real service account is
    GOOGLE_SERVICE_ACCOUNT_JSON. Anyone who has debugged this file has lost an
    hour to it, so it is done here rather than left as a footgun.
    """
    from dotenv import dotenv_values

    for candidate in (REPO / ".env", Path.home() / "Client-Portal" / ".env"):
        if not candidate.exists():
            continue
        values = dotenv_values(candidate)
        for key, value in values.items():
            if value is not None:
                os.environ.setdefault(key, value)
        sa = values.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if sa and not os.environ.get("BIGQUERY_SERVICE_ACCOUNT_JSON", "").startswith("{"):
            os.environ["BIGQUERY_SERVICE_ACCOUNT_JSON"] = sa
        if not os.environ.get("BIGQUERY_PROJECT_ID"):
            os.environ["BIGQUERY_PROJECT_ID"] = "rpm-portal-492523"
        print(f"  env loaded from {candidate}")
        return
    print("  WARNING: no .env found — the API will not reach HubSpot or BigQuery")


def _extract(pattern: str, source: str, what: str) -> str:
    match = re.search(pattern, source, re.S)
    if not match:
        sys.exit(f"could not find the {what} in {TEMPLATE.name}")
    return match.group(0)


def _page() -> str:
    """Build the preview page from the live template's own markup."""
    source = TEMPLATE.read_text()

    section = _extract(r'<section id="section-ask".*?</section>', source, "Ask section")
    widget = (_extract(r'<style>\n/\* Portal feedback widget\..*?</style>', source, "feedback CSS")
              + _extract(r'<button id="fb-launch".*?</button>', source, "feedback button")
              + _extract(r'<div id="fb-modal".*?\n</div>\n\n<script>', source, "feedback modal")[:-len("<script>")]
              + _extract(r'<script>\n/\* ── Portal feedback widget ─.*?</script>', source, "feedback JS"))
    styles = _extract(r"<style>\n/\* Ask surface\..*?</style>", source, "Ask stylesheet")
    script = _extract(r"<script>\n/\* ── Ask ─.*?</script>", source, "Ask script")

    # The design tokens the portal defines on :root. Lifted rather than
    # retyped so the preview cannot look different from production.
    tokens = _extract(r"--j:#55605E.*?--tp:#1F2937;--ts:#6B7280;--tm:#9CA3AF;",
                      source, "design tokens")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ask — local preview</title>
<style>
:root {{ {tokens} --bd:#E5E7EB; --r:14px; --sh:0 1px 2px rgba(16,24,40,.04); }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--tp);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
.wrap {{ max-width:960px; margin:0 auto; padding:34px 26px 80px }}
.banner {{ background:#FFF7ED; border:1px solid #FED7AA; color:#7C2D12;
  border-radius:10px; padding:11px 15px; font-size:12.5px; line-height:1.6;
  margin-bottom:26px }}
.picker {{ display:flex; gap:10px; align-items:center; margin-bottom:24px;
  flex-wrap:wrap }}
.picker label {{ font-size:11px; font-weight:700; text-transform:uppercase;
  letter-spacing:.06em; color:var(--ts) }}
.picker select {{ padding:8px 12px; border:1px solid var(--bd); border-radius:8px;
  background:#fff; font-size:13px; color:var(--tp); min-width:260px }}
.card {{ background:#fff; border:1px solid var(--bd); border-radius:var(--r);
  box-shadow:var(--sh) }}
.card-p {{ padding:28px }}
.sec-topbar {{ display:flex; align-items:flex-start; justify-content:space-between;
  margin-bottom:28px; gap:16px }}
.sec-title {{ font-size:21px; font-weight:800; color:var(--tp) }}
.sec-sub {{ font-size:12px; color:var(--tm); margin-top:3px }}
.btn {{ border:1px solid var(--bd); background:#fff; color:var(--tp);
  border-radius:8px; padding:7px 13px; font-size:12px; font-weight:600;
  cursor:pointer }}
.btn:hover {{ border-color:var(--j) }}
</style>
{styles}
</head>
<body>
<div class="wrap">
  <div class="banner">
    <b>Local preview.</b> This is the real markup, CSS and JavaScript from
    <code>client-portal.html</code>, served against the live API and production
    data. In the portal it renders as the <b>Ask</b> tab in the left nav.
  </div>

  <div class="picker">
    <label for="prop">Property</label>
    <select id="prop" onchange="_switchProperty()">
      <option value="30912193455">The Atwood at Rivulon — Phoenix</option>
      <option value="35136892964">The Henry at Harms Woods — Chicago</option>
    </select>
  </div>

  {section}
</div>
{widget}

<script>
  window.__PORTAL_API_BASE  = window.location.origin;
  window.__PORTAL_EMAIL__   = 'kyle.shipp@rpmliving.com';
  window.__PORTAL_COMPANY_ID__ = document.getElementById('prop').value;

  function _switchProperty() {{
    window.__PORTAL_COMPANY_ID__ = document.getElementById('prop').value;
    document.getElementById('ask-answer').style.display  = 'none';
    document.getElementById('ask-error').style.display   = 'none';
    document.getElementById('ask-pending').style.display = 'none';
    document.getElementById('ask-idle').style.display    = '';
    var chips = document.querySelectorAll('.ask-chip');
    for (var i = 0; i < chips.length; i++) chips[i].className = 'ask-chip';
  }}
</script>
{script}
<script>window.loadAsk();</script>
</body>
</html>"""


def main() -> int:
    print("Ask preview")
    _load_env()

    from flask import Flask

    app = Flask(__name__)

    from routes.ask import ask_bp
    app.register_blueprint(ask_bp)
    from routes.feedback import feedback_bp
    app.register_blueprint(feedback_bp)

    page = _page()

    @app.route("/")
    def index():
        return page

    print("  blueprint /api/ask/* mounted")
    print()
    print("  ->  http://127.0.0.1:5055")
    print()
    app.run(host="127.0.0.1", port=5055, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
