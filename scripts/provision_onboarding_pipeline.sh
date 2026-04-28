#!/usr/bin/env bash
# Provision the onboarding pipeline against the live HubSpot tenant.
#
# Run from the repo root, in an environment where HUBSPOT_API_KEY is set
# (Render Shell, your laptop with .env, or any host with the secret).
#
#   ./scripts/provision_onboarding_pipeline.sh
#
# What it does, in order:
#   1. Sanity-checks credentials by hitting GET /crm/v3/properties/companies
#   2. Creates the 12 onboarding company properties (idempotent)
#   3. Creates the 5 onboarding HubDB tables (idempotent — fetches existing ID)
#   4. Prints the env vars you need to add to Render
#
# Idempotent and safe to re-run. Each step exits non-zero if it fails so
# CI / Render Shell stops at the first error rather than masking failures.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "═══════════════════════════════════════════════════════════════"
echo "  RPM Onboarding Pipeline — Provisioning"
echo "═══════════════════════════════════════════════════════════════"

# ── Step 0: env sanity ─────────────────────────────────────────────────────
if [ -z "${HUBSPOT_API_KEY:-}" ]; then
  echo "ERROR: HUBSPOT_API_KEY is not set."
  echo "       Set it via Render Shell, .env, or export and re-run."
  exit 1
fi

echo
echo "── Step 0: verifying HubSpot credentials"
http_status=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $HUBSPOT_API_KEY" \
  "https://api.hubapi.com/crm/v3/properties/companies?limit=1")
if [ "$http_status" = "200" ]; then
  echo "   OK — token can read company properties"
elif [ "$http_status" = "401" ]; then
  echo "   ERROR: 401 — token invalid or expired"
  exit 1
elif [ "$http_status" = "403" ]; then
  echo "   ERROR: 403 — token missing scopes (need crm.objects.companies.read at minimum)"
  exit 1
else
  echo "   ERROR: HubSpot returned HTTP $http_status"
  exit 1
fi

# ── Step 1: company properties ─────────────────────────────────────────────
echo
echo "── Step 1: creating onboarding company properties"
python scripts/create_onboarding_properties.py

# ── Step 2: HubDB tables ───────────────────────────────────────────────────
echo
echo "── Step 2: creating onboarding HubDB tables"
python scripts/create_hubdb_onboarding_tables.py

# ── Step 3: reminder of remaining manual steps ────────────────────────────
echo
echo "═══════════════════════════════════════════════════════════════"
echo "  Done. Remaining manual steps:"
echo "═══════════════════════════════════════════════════════════════"
echo
echo "  1. Copy the HUBDB_*_TABLE_ID values printed above into Render env vars"
echo "     and restart the Flask service."
echo
echo "  2. Build the 4 HubSpot Workflows (gap-review email automation)"
echo "     using docs/handoffs/HUBSPOT_GAP_REVIEW_WORKFLOW.md"
echo
echo "  3. When ready to push the portal UI to live HubSpot Design Manager:"
echo "       python scripts/deploy_to_hubspot.py"
echo "     (NOT run by this script — affects what users see in the live portal)"
echo
echo "  4. Send the Fluency outreach email at"
echo "     docs/handoffs/FLUENCY_OUTREACH_EMAIL.md"
echo "     before configuring FLUENCY_DROPZONE_PATH."
