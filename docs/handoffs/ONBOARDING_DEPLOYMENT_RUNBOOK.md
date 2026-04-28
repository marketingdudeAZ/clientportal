# Onboarding Pipeline — Deployment Runbook

End-to-end checklist for landing the brief→fulfillment pipeline in
production. Steps are ordered by dependency — do not skip ahead.

**Branch:** `claude/client-onboarding-discovery-rcQj4`
**Owner of this runbook:** whoever's running the deploy.
**Estimated wall time:** 60–90 minutes (mostly waiting on HubSpot UI).

---

## 1. Confirm preconditions

- [ ] You have HubSpot Private App admin access (to create properties + workflows)
- [ ] The private-app token in `HUBSPOT_API_KEY` has scopes:
      `crm.objects.companies.read`, `crm.objects.companies.write`,
      `hubdb`, `files` (the new asset uploader needs Files write)
- [ ] You have shell access to run the provisioning scripts against the
      target environment (dev or prod)
- [ ] Anthropic API key in `ANTHROPIC_API_KEY` (used by ILS research +
      gap-review slop classifier)
- [ ] The branch is merged or checked out on the deployment host

---

## 2. Provision HubSpot company properties

Creates 12 new company properties for the lifecycle + gap workflow.

```bash
python scripts/create_onboarding_properties.py
```

Expected output: `CREATED:` for each property the first time, `EXISTS:`
on subsequent runs (idempotent). If you see `FAILED:`, check the error
message — usually a scope issue.

Properties created:
- `rpm_onboarding_status` (enum, 11 values)
- `rpm_onboarding_status_changed_at` (datetime)
- `community_manager_email` / `community_manager_name`
- `regional_manager_email` / `regional_manager_name`
- `rpm_gap_review_action` / `rpm_gap_review_token` / `rpm_gap_review_questions`
- `rpm_gap_review_email_sent_at` / `rpm_gap_review_response_at` / `rpm_gap_review_status`

---

## 3. Provision HubDB tables

Creates 5 new tables and prints their IDs.

```bash
python scripts/create_hubdb_onboarding_tables.py
```

Copy the printed table IDs into your `.env` (and the production secret
store):

```
HUBDB_ONBOARDING_INTAKE_TABLE_ID=<id>
HUBDB_GAP_RESPONSES_TABLE_ID=<id>
HUBDB_BLUEPRINT_VARIABLES_TABLE_ID=<id>
HUBDB_BLUEPRINT_TAGS_TABLE_ID=<id>
HUBDB_BLUEPRINT_ASSETS_TABLE_ID=<id>
```

Restart the Flask service so the new env vars get picked up.

---

## 4. Build the HubSpot Workflows

Open `docs/handoffs/HUBSPOT_GAP_REVIEW_WORKFLOW.md` and build:

| # | Name | Trigger |
|---|---|---|
| 1 | Send CM Email | `rpm_gap_review_action = send_cm_email` |
| 2 | CM No-Response Escalation | `rpm_gap_review_status = sent` AND `_email_sent_at > 48h ago` |
| 3 | Final Timeout | `rpm_gap_review_status = escalated` AND `_email_sent_at > 72h ago` |
| 4 | Response Received | `rpm_gap_review_response_at` known and changes |

Plus 6 SLA-breach workflows (one per stage), per the spec table.

**Test each workflow with a non-production company** before flipping live.
Set `rpm_gap_review_action = send_cm_email` manually on a test company,
verify Workflow 1 fires and creates the owner task with the email body
rendered.

---

## 5. Deploy the CMS template + assets

```bash
python scripts/deploy_to_hubspot.py
```

This pushes:
- Updated `client-portal.html` (with the inline Onboarding section)
- Standalone `partials/onboarding-intake.html` (for future `{% include %}`)
- `css/onboarding-intake.css`, `js/onboarding-intake.js`
- All other existing portal files (CSS/JS/partials) — uploaded
  unconditionally; HubSpot dedupes by hash

Verify in HubSpot Design Manager:
- `custom/client-portal/Client Portal` template updated
- `custom/client-portal/partials/onboarding-intake.html` present
- `custom/client-portal/css/onboarding-intake.css` present
- `custom/client-portal/js/onboarding-intake.js` present

Open the live portal in a browser. Confirm:
- "Onboarding" nav item is visible (you may need to manually set
  `rpm_onboarding_status = intake_sent` on a test company first)
- Form renders without console errors
- Status pill shows current stage
- Logo upload triggers color extraction
- Manager email blur triggers name preview

---

## 6. Configure Fluency export (optional, Phase 1)

For Phase 1 (CSV/sFTP drop), set:

```
FLUENCY_DROPZONE_PATH=/var/run/fluency-dropzone   # or wherever sFTP mounts
```

Phase 2 (REST API) — only when Fluency support has issued credentials:

```
FLUENCY_API_KEY=<your_key>
FLUENCY_API_BASE_URL=https://api.fluency.inc
```

The factory in `fluency_exporter.py` auto-selects the right exporter based
on whether `FLUENCY_API_KEY` is set.

To trigger an export manually:
```bash
curl -X POST https://your-server/api/onboarding/fluency/export \
  -H "X-Portal-Email: csm@rpmliving.com" \
  -H "Content-Type: application/json" \
  -d '{"company_id": "12345", "property_uuid": "abc-def"}'
```

---

## 7. Smoke test — full end-to-end

On a test property:

1. Set `rpm_onboarding_status = intake_sent` on the company
2. Open the portal as the PMA — Onboarding nav appears
3. Fill the form with:
   - Required structured fields
   - A real apartments.com URL for that property
   - A logo PNG with transparent background
   - A hero JPG ≥1200px on shortest side
4. Submit — verify response includes `gap_questions: []` if everything's clean
5. Check HubDB:
   - `rpm_onboarding_intake` has a new row
   - `rpm_blueprint_assets` has 4 logo + 3 hero variants
   - `rpm_blueprint_variables` has `brand_primary` + `brand_secondary` rows
6. Check HubSpot company properties:
   - `rpm_onboarding_status` advanced to `intake_complete`
   - `community_manager_name` derived correctly from email
7. Brief drafter — confirm a fresh draft was kicked with ILS data:
   ```bash
   tail -f webhook-server.log | grep "ILS research"
   ```
   Should see: `brief_ai_drafter: ILS research found N providers, M quotes`
8. To test the gap workflow, submit again with a generic free-text answer
   ("our luxurious community provides best-in-class amenities") — verify:
   - Response includes a `gap_review_token`
   - HubSpot company has `rpm_gap_review_action = send_cm_email`
   - HubSpot Workflow 1 fires within 1 minute
   - Owner sees a task with the pre-drafted email body

---

## 8. Failure modes + recovery

| Symptom | Likely cause | Fix |
|---|---|---|
| `colors_extract` returns 401 | `X-Portal-Email` header missing | Check JS is sending it |
| Asset upload returns 403 from HubSpot Files | `files` scope not on token | Add scope, regenerate token |
| Brief draft has no ILS data | `_fetch_html` blocked by apartments.com | Expected occasionally — fail-graceful, no action needed |
| Workflow 1 never fires | Trigger condition wrong | Re-check spec; common gotcha is workflow being inactive |
| `transition_state` returns 400 | Illegal transition attempted | Check `_LEGAL_TRANSITIONS` in `onboarding_state.py` |
| Gap response form 400 with "link no longer valid" | Token already used (single-use) | Issue a new token via re-submit of intake |
| Fluency export `501 Not Implemented` | API exporter selected but not built | Unset `FLUENCY_API_KEY` to fall back to CSV |

---

## 9. Rollback

If something goes badly:

1. Revert the `client-portal.html` deploy by running `deploy_to_hubspot.py`
   from the previous commit (the script overwrites in place).
2. Disable the 4 HubSpot Workflows (don't delete — toggle off).
3. The HubDB tables and properties are additive — they don't need removing.
   The Flask code degrades gracefully if their env vars are unset.
4. Optionally, set `rpm_onboarding_status = not_started` on any company you
   were testing against, so they fall out of the lifecycle UI.

---

## 10. Production observability

Things worth alerting on in your monitoring:

- `gap_review.review_intake` errors (slop classifier failures are usually
  Anthropic 5xx — alert if > 5% over 15 min)
- `ils_research._fetch_html` 4xx rate (rises if apartments.com tightens
  bot detection — may need a scraping service)
- `_kick_brief_redraft_with_ils` thread crashes (silent today; consider
  metric for this)
- HubDB `publish` failures on `rpm_onboarding_intake` (would lose intake
  rows silently)

---

## 11. What ships next (not in this runbook)

- Phase 2 Fluency API push (waiting on credentials)
- A "review this draft" UI section for the CSM at `brief_review` stage
- Slack/email digest of stalled onboardings to the Director (currently
  visible only as HubSpot tasks)
- BigQuery export of `rpm_onboarding_intake` for funnel analysis
