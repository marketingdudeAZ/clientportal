# SEO Insights — HubSpot Handoff Plan (Phase 1)

Paste this whole file into a fresh Claude Code session in your HubSpot-side project. Everything needed to finish Phase 1 is here.

---

## What's already done (on the Flask side)

Branch `claude/seo-package-portal-integration-bJKfG` on `marketingdudeAZ/clientportal` is pushed and ready. It contains:

- Flask routes: `GET /api/seo/entitlement`, `GET /api/seo/dashboard`, `GET|POST /api/seo/keywords`, `POST /api/seo/keywords/<id>/delete`, `GET|POST /api/seo/competitors`, `GET /api/seo/ai-mentions`
- DataForSEO client (`dataforseo_client.py`), tier gating (`seo_entitlement.py`), dashboard assembly (`seo_dashboard.py`), AI-mentions tracker (`ai_mentions.py`), daily/weekly refresh cron (`seo_refresh_cron.py`)
- BigQuery helpers for rank time-series in `bigquery_client.py`
- Frontend section in `demo.html` (`#section-seo`, new "SEO & AI" nav item, Rankings / AI Mentions / Competitors sub-tabs, inline-SVG trendlines)
- 31 passing tests (`tests/test_seo_*.py`, `tests/test_dataforseo_client.py`)

The Flask app will deploy cleanly to Render once env vars are set. **None of the HubSpot-side provisioning has been done.**

---

## Tasks remaining (all HubSpot-side)

### 1. Create 3 HubDB tables

Log into HubSpot → Marketing → Files and Templates → HubDB → **Create table** for each below. Publish after creating each.

**Table: `rpm_seo_keywords`**
Label: RPM SEO Keywords. Use for pages: No.

| Column name | Label | Type |
|---|---|---|
| property_uuid | Property UUID | Text |
| keyword | Keyword | Text |
| priority | Priority | Text |
| tag | Tag | Text |
| intent | Intent | Text |
| branded | Branded | Boolean (checkbox) |
| target_position | Target Position | Number |
| volume | Search Volume | Number |
| difficulty | Difficulty | Number |

**Table: `rpm_seo_competitors`**
Label: RPM SEO Competitors.

| Column name | Label | Type |
|---|---|---|
| property_uuid | Property UUID | Text |
| competitor_domain | Competitor Domain | Text |
| label | Label | Text |

**Table: `rpm_ai_mentions`**
Label: RPM AI Mentions Snapshots.

| Column name | Label | Type |
|---|---|---|
| property_uuid | Property UUID | Text |
| scanned_at | Scanned At | Date and time |
| composite_index | Composite Index | Number |
| chatgpt_rate | ChatGPT Rate | Number |
| perplexity_rate | Perplexity Rate | Number |
| gemini_rate | Gemini Rate | Number |
| aio_rate | AI Overview Rate | Number |
| detail_json | Detail JSON | Rich text |

After publishing each, copy the **numeric table ID** from the URL (e.g. `/hubdb/tables/123456789`). You'll need all three.

### 2. Create 3 company properties

HubSpot → Settings → Data Management → Properties → Companies → **Create property**:

| Name | Label | Type | Field type | Group |
|---|---|---|---|---|
| seo_last_audit_score | SEO Last Audit Score | Number | Number | Company information |
| seo_last_crawl_at | SEO Last Crawl At | Datetime | Date | Company information |
| ai_visibility_index | AI Visibility Index | Number | Number | Company information |

These are written to by the weekly cron — no manual data entry needed.

### 3. Set Render env vars

In your Render service → Environment → Add:

```
DATAFORSEO_LOGIN=<login>
DATAFORSEO_PASSWORD=<password>          # mark as Secret
DATAFORSEO_DEFAULT_LOCATION=2840        # USA
DATAFORSEO_DEFAULT_LANGUAGE=en
HUBDB_SEO_KEYWORDS_TABLE_ID=<from step 1>
HUBDB_SEO_COMPETITORS_TABLE_ID=<from step 1>
HUBDB_AI_MENTIONS_TABLE_ID=<from step 1>
```

Save → service auto-redeploys.

### 4. Deploy `demo.html` to HubSpot CMS

The updated `demo.html` on the branch has a new "SEO & AI" nav item and `#section-seo` section (lines ~800 for nav, ~1084 for section, ~5950 for JS module). Deploy it the same way you deploy the rest of the portal template:

```
python scripts/deploy_to_hubspot.py
```

Or manually upload via HubSpot Design Manager → Source Code API → replace `templates/rpm-portal-demo.html` → Push live.

### 5. Verify HubL injections

The frontend JS reads these window globals that HubSpot personalization tokens inject. Confirm your CMS template has them:

```html
<script>
  window.__PORTAL_COMPANY_ID__ = '{{ request.contact.associated_company.id }}';
  window.__PORTAL_EMAIL__ = '{{ request.contact.email }}';
  window.__PORTAL_PROP_UUID__ = '{{ request.contact.associated_company.property_uuid }}';
  window.__PORTAL_PROP_PACKAGES = {{ request.contact.associated_company.portal_packages_json|safe }};
  window.__WEBHOOK_URL__ = 'https://rpm-portal-server.onrender.com'; // or your Render URL
</script>
```

The SEO nav item only appears when `__PORTAL_PROP_PACKAGES` contains `{ channel: 'seo_organic' }`. If your existing HubL already builds that array from active deals/line items, the gate will work automatically.

### 6. Seed a test property

On one SEO-package test company in HubSpot:
- Verify the company has an `SEO_Package` line item on its latest deal, OR `seo_budget > 0`.
- Open the `rpm_seo_keywords` HubDB table and insert 5–10 rows with that property's `property_uuid` — e.g., `apartments phoenix`, `luxury apartments scottsdale`, `{property_name}`, `pet friendly apartments {city}`, etc.
- Insert 2–3 rows into `rpm_seo_competitors` with competitor domains.
- Publish both tables.

Then load the portal as that test contact → navigate to SEO & AI → you should see the keyword table populated (positions will show `—` until the cron runs) and the "Add keyword" button if the tier is Basic+.

### 7. Schedule the cron (later)

Once tables are seeded and you've confirmed the UI loads:

Create two new Render **Cron Job** services (separate from the web service, same repo):

| Cron | Schedule | Command | Env vars needed |
|---|---|---|---|
| Daily rank refresh | `0 9 * * *` (9 UTC = 4 AM ET) | `python webhook-server/seo_refresh_cron.py` | `CRON_MODE=daily` + all SEO env vars |
| Weekly AI mentions + audit | `0 10 * * 1` (Mondays 10 UTC) | `python webhook-server/seo_refresh_cron.py` | `CRON_MODE=weekly` + all SEO env vars |

---

## Package-tier gating (reference only)

Enforced server-side in `seo_entitlement.py`. For reference when you write AM-facing collateral:

| Feature | Min tier |
|---|---|
| Dashboard view | Local |
| Read tracked keywords | Local |
| Add/remove keywords | Basic |
| AI Mentions tracker | Basic |
| Content clusters / briefs (Phase 2) | Standard |
| Content decay queue (Phase 2) | Premium |
| Keyword research tool (Phase 3) | Basic |
| Trend explorer (Phase 3) | Standard |

Tier detection order: SKU `SEO_Package` on latest deal → fallback to company `seo_budget` → nearest tier by price ($100/$300/$500/$800/$1300).

---

## Verification checklist

- [ ] 3 HubDB tables created and published; IDs captured
- [ ] 3 company properties created on Companies object
- [ ] All 8 env vars set on Render web service
- [ ] Render auto-redeployed successfully (check logs for `/api/seo/*` route registration)
- [ ] `curl` test: `curl -H "X-Portal-Email: you@rpmliving.com" "<render-url>/api/seo/entitlement?company_id=<test-id>"` returns tier + features map
- [ ] demo.html deployed to HubSpot template
- [ ] Test property loaded in portal shows "SEO & AI" nav item
- [ ] Keyword table renders with seeded rows
- [ ] Cron jobs created and first run completes without error
- [ ] Rank data appears in dashboard after first daily cron run
- [ ] AI mention snapshot appears after first weekly cron run

---

## Notes for the next Claude session

- The Flask code is already committed and tested. Don't re-implement it; just provision and deploy.
- HubSpot private-app token may need these scopes added: `hubdb`, `crm.objects.companies.write`, `crm.schemas.companies.write` if any step returns 403.
- BigQuery table `seo_ranks_daily` is referenced by `bigquery_client.get_seo_rank_history()` — it auto-creates on first insert via `insert_rows_json`, no manual DDL needed. (Dataset must exist, which it does per `BIGQUERY_DATASET_PROD`.)
- The daily cron is idempotent but expensive. Each property × keyword = 1 SERP call at ~$0.001. 500 properties × 30 keywords = ~$15/day.
