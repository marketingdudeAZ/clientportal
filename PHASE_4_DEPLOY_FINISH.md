# Phase 4 Deploy Finish — Claude Handoff

**You are the Claude instance with credentials.** The Phase 4 feature (AI-drafted client brief + Paid/SEO keyword split) is merged to `main` and deployed on Render. Three credentialed tasks remain to make it work end-to-end. The user is on Mac, new to git, and on Render's free tier (no Shell access), so none of this can run from Render itself — execute from a dev machine.

Full architecture + route reference: `ONBOARDING_AI_BRIEF_HANDOFF.md` in this repo.

---

## Preflight

Verify you're in the right place and have the expected creds:

```bash
# 1. Repo state
cd <path-to-clientportal>
git checkout main && git pull
test -f scripts/create_hubdb_paid_keywords.py || { echo "MISSING — wrong branch?"; exit 1; }

# 2. Credentials (expect HUBSPOT_API_KEY at minimum)
grep -E "^(HUBSPOT_API_KEY|ANTHROPIC_API_KEY|DATAFORSEO_LOGIN|BIGQUERY_PROJECT_ID)=" .env | sed 's/=.*/=<set>/'

# 3. Python deps
python3 -m pip install --quiet requests python-dotenv

# 4. Confirm HubSpot portal (tell the user which one you're about to touch)
python3 -c "
import os; from dotenv import load_dotenv; import requests
load_dotenv()
r = requests.get('https://api.hubapi.com/account-info/v3/details',
                 headers={'Authorization': f'Bearer {os.environ[\"HUBSPOT_API_KEY\"]}'})
print(f'Portal ID: {r.json().get(\"portalId\")}, type: {r.json().get(\"accountType\")}')
"
```

**Stop and confirm the portal ID with the user before proceeding.** Creating tables in the wrong portal is annoying to clean up.

---

## Task 1 (REQUIRED) — Provision the rpm_paid_keywords HubDB

This is the Fluency feed. Launch-blocking.

```bash
python3 scripts/create_hubdb_paid_keywords.py
```

**Expected output:**

```
Creating rpm_paid_keywords + rpm_brief_drafts HubDB tables...
  Created rpm_paid_keywords: id=<9-digit-number>
    Published table <id>
  Created rpm_brief_drafts: id=<9-digit-number>
    Published table <id>

Add these to .env:
  HUBDB_PAID_KEYWORDS_TABLE_ID=<id>
  HUBDB_BRIEF_DRAFTS_TABLE_ID=<id>
```

**If you see `409 already exists`**: the script fetches the existing ID, which is fine — someone provisioned already.

**Capture both table IDs.** You'll need them in Task 2.

---

## Task 2 (REQUIRED) — Set the env vars on Render

The user is on the free tier, so you can't Shell in, but the dashboard env-var UI is available on every plan. Do this for them via the Render API (needs a Render API key — ask the user if it isn't in `.env`) or walk them through the UI.

### Option A — Via Render API (if `RENDER_API_KEY` is available)

```bash
# Find the service ID
curl -s -H "Authorization: Bearer $RENDER_API_KEY" https://api.render.com/v1/services \
  | python3 -c "import sys,json; [print(s['service']['id'], s['service']['name']) for s in json.load(sys.stdin)]"

# Set the env vars (replace SERVICE_ID and the IDs from Task 1)
curl -X PUT -H "Authorization: Bearer $RENDER_API_KEY" \
     -H "Content-Type: application/json" \
     https://api.render.com/v1/services/<SERVICE_ID>/env-vars \
     -d '[
       {"key": "HUBDB_PAID_KEYWORDS_TABLE_ID", "value": "<from-task-1>"},
       {"key": "HUBDB_BRIEF_DRAFTS_TABLE_ID",  "value": "<from-task-1>"}
     ]'
# Note: PUT replaces the whole env-var list — fetch existing first and merge, or use the UI.
```

### Option B — Render UI (safer, no risk of overwriting other env vars)

Tell the user:
1. Render dashboard → their clientportal service → **Environment** tab
2. Click **Add Environment Variable** twice:
   - `HUBDB_PAID_KEYWORDS_TABLE_ID` = `<id from Task 1>`
   - `HUBDB_BRIEF_DRAFTS_TABLE_ID` = `<id from Task 1>`
3. Click **Save Changes** — Render will auto-redeploy.

Wait for the redeploy to go green before Task 3.

---

## Task 3 (REQUIRED) — Add HubSpot company property `paid_media_radius_miles`

Read by `/api/paid/targeting` to enforce fair-housing radius minimums. Missing property = the Paid Targeting tab shows "No radius set" forever.

```bash
python3 -c "
import os, json; from dotenv import load_dotenv; import requests
load_dotenv()
r = requests.post(
  'https://api.hubapi.com/crm/v3/properties/companies',
  headers={'Authorization': f'Bearer {os.environ[\"HUBSPOT_API_KEY\"]}',
           'Content-Type': 'application/json'},
  json={
    'name': 'paid_media_radius_miles',
    'label': 'Paid Media Radius (miles)',
    'type': 'number',
    'fieldType': 'number',
    'groupName': 'companyinformation',
    'description': 'Geo-radius used in Paid Media targeting. Housing special-ad category enforces a 15-mile minimum.',
  }
)
print(r.status_code, r.text[:400])
"
```

**Expected:** `201` with a JSON body. `409` means it already exists — proceed. Anything else — stop and show the user.

---

## Task 4 (OPTIONAL) — BigQuery trust-signal events table

Without this, trust-signal events still get captured (falls back to Flask app logs). With it, they land in BigQuery so the team can query volume.

Run from a machine with the BigQuery service-account JSON loaded:

```bash
python3 -c "
from google.cloud import bigquery
import os; from dotenv import load_dotenv; load_dotenv()
client = bigquery.Client(project=os.environ['BIGQUERY_PROJECT_ID'])
dataset = os.environ.get('BIGQUERY_DATASET_PROD', 'rpm_portal')
table_id = f\"{client.project}.{dataset}.rpm_portal_events\"
schema = [
  bigquery.SchemaField('event_type', 'STRING', mode='REQUIRED'),
  bigquery.SchemaField('company_id', 'STRING'),
  bigquery.SchemaField('email',      'STRING'),
  bigquery.SchemaField('detail',     'STRING'),
  bigquery.SchemaField('logged_at',  'TIMESTAMP', mode='REQUIRED'),
]
table = bigquery.Table(table_id, schema=schema)
client.create_table(table, exists_ok=True)
print(f'OK: {table_id}')
"
```

---

## Task 5 (OPTIONAL — handoff to another team) — Fluency feed

This is a manual step for whoever owns Fluency blueprints, not a scripted one:

> Point Fluency at the published HubDB table `rpm_paid_keywords` (portal ID: `<confirm in Task 1>`, table ID from Task 1). Rows are keyed by `property_uuid`. Columns available: `keyword, match_type, priority, neighborhood, intent, reason, cpc_low, cpc_high, competition_index, generated_at, approved, fluency_synced_at`.

Write that as a ClickUp task (assignee: Paid team lead) or Slack DM — whichever the user prefers. If you create a ClickUp task, use `CLICKUP_LIST_PAID_MEDIA` env var as the target list.

---

## Smoke test (after Tasks 1–3 complete)

Ask the user for one real HubSpot company record ID, then:

```bash
# Use your own HubSpot credentials to resolve a good test company
# (or ask the user for one that has `uuid`, `domain`, and a client brief filled in).

CID="<company-id-from-user>"
BASE="https://<render-service-name>.onrender.com"   # ask user for exact URL
EMAIL="<user's portal email>"

# 1. Keyword generation end-to-end
curl -s -X POST "$BASE/api/onboarding/keywords/generate" \
  -H "Content-Type: application/json" -H "X-Portal-Email: $EMAIL" \
  -d "{\"company_id\":\"$CID\"}" | python3 -m json.tool

# Expect: status=ok, seeds_count>0, seo_inserted>0, paid_inserted>0,
# label_counts with seo_target/paid_only/both.

# 2. Paid targeting (confirm compliance banner + radius check)
curl -s "$BASE/api/paid/targeting?company_id=$CID" \
  -H "X-Portal-Email: $EMAIL" | python3 -m json.tool | head -40

# 3. AI draft (needs a public property URL — ask the user)
curl -s -X POST "$BASE/api/client-brief/draft" \
  -H "X-Portal-Email: $EMAIL" \
  -F "domain=<URL user gives you>" \
  | python3 -m json.tool
# Capture draft_id, then poll:
curl -s "$BASE/api/client-brief/draft/<draft_id>" \
  -H "X-Portal-Email: $EMAIL" | python3 -m json.tool
```

If any of these return non-2xx or the keyword counts are all zero, tail the Render logs — most likely causes:

- 500 on generate: DataForSEO quota / API creds missing
- seo_inserted=0 but keywords_found>0: `HUBDB_SEO_KEYWORDS_TABLE_ID` missing from Render env
- paid_inserted=0 but keywords_found>0: `HUBDB_PAID_KEYWORDS_TABLE_ID` from Task 1/2 not yet visible to the deploy
- draft `status: "error"` with "ANTHROPIC_API_KEY not configured": missing on Render env

---

## Reporting back to the user

When you're done, tell the user:

1. Portal ID you touched (from Preflight)
2. Two HubDB table IDs created in Task 1
3. Whether the Render env vars are in place and the deploy is green
4. Confirmation the HubSpot property was created (Task 3 response code)
5. Smoke-test results (counts, any non-2xx status codes with the error body)
6. Remind the user to rotate the HubSpot token used in this session if it was pasted anywhere outside a `.env` file

---

## Security notes

- **Never echo `HUBSPOT_API_KEY`, `ANTHROPIC_API_KEY`, or service-account JSON to output.**
- If the user pasted a key into a chat transcript earlier, tell them to rotate it when you're done.
- If a `.env` file has to be created, `chmod 600 .env` so it isn't world-readable.
- Don't commit `.env` — verify `.gitignore` already excludes it before touching.

## Rollback

If the deploy goes sideways:

- HubDB tables are additive — harmless to leave in place.
- Render env vars: remove via UI, redeploy.
- HubSpot property: remove via Settings → Properties → Companies → search `paid_media_radius_miles` → delete.
- Code rollback: `git revert <merge-commit-sha>` on `main` (PR #1's merge SHA), push.
