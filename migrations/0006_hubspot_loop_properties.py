"""Add Loop-related custom properties to HubSpot CRM companies.

Per ADR 0009 + ADR 0014 + ADR 0015, we need these new HubSpot company
properties:

- hyly_property_id  (string) — Hyly property ID for the Convert join
- loop_mode         (enumeration) — auto-pilot | co-pilot | custom

Existing properties referenced (not created here; assumed already present):
- aptiq_property_id, aptiq_market_id, seo_tier, plestatus, uuid

Idempotent: HubSpot returns 409 if the property already exists; we
catch that as success.

R1 compliance: this migration NEVER creates or modifies the `uuid`
property. R1 is hardcoded into MigrationContext.hubspot_session — any
attempt to PATCH uuid will be blocked at the request layer when that
guard is built.
"""

TARGETS = ["hubspot_crm"]

PROPERTIES = [
    {
        "name": "hyly_property_id",
        "label": "Hyly Property ID",
        "type": "string",
        "fieldType": "text",
        "description": "Hyly's internal property ID (e.g. '1839261086288116013'). "
                       "Used to join Hyly's BQ attribution data to this property. "
                       "Backfilled by services/hyly_property_sync; do not edit manually.",
        "groupName": "companyinformation",
    },
    {
        "name": "loop_mode",
        "label": "Loop Mode",
        "type": "enumeration",
        "fieldType": "select",
        "description": "Controls how the Multifamily Loop interacts with this "
                       "property. auto-pilot = AI applies recommendations within "
                       "bounded heuristics. co-pilot = client/AM approves weekly. "
                       "custom = goal-driven plan crafted by AM.",
        "groupName": "companyinformation",
        "options": [
            {"label": "Auto-pilot", "value": "auto-pilot", "displayOrder": 0},
            {"label": "Co-pilot",   "value": "co-pilot",   "displayOrder": 1},
            {"label": "Custom",     "value": "custom",     "displayOrder": 2},
        ],
    },
]


def up(ctx):
    session = ctx.hubspot_session
    base = "https://api.hubapi.com/crm/v3/properties/companies"
    created = 0
    existed = 0
    for prop in PROPERTIES:
        if ctx.dry_run:
            ctx.log(f"DRY RUN — would POST {prop['name']}")
            continue
        r = session.post(base, json=prop, timeout=15)
        if r.status_code in (200, 201):
            created += 1
            ctx.log(f"Created HubSpot property: {prop['name']}")
        elif r.status_code == 409:
            existed += 1
            ctx.log(f"HubSpot property already exists: {prop['name']}")
        else:
            raise RuntimeError(
                f"Failed to create HubSpot property {prop['name']}: "
                f"HTTP {r.status_code}: {r.text[:300]}"
            )
    ctx.log(f"HubSpot property setup complete: {created} created, {existed} already existed")


def down(ctx):
    # Deleting HubSpot custom properties is destructive; we provide it
    # but log warnings.
    session = ctx.hubspot_session
    base = "https://api.hubapi.com/crm/v3/properties/companies"
    for prop in PROPERTIES:
        if ctx.dry_run:
            ctx.log(f"DRY RUN — would DELETE {prop['name']}")
            continue
        r = session.delete(f"{base}/{prop['name']}", timeout=15)
        if r.status_code in (200, 204):
            ctx.log(f"DELETED {prop['name']} (data on existing records is lost)")
        elif r.status_code == 404:
            ctx.log(f"{prop['name']} already absent")
        else:
            raise RuntimeError(f"Delete failed for {prop['name']}: {r.status_code} {r.text[:200]}")
