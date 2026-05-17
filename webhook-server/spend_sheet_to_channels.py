"""SKU column key → forecasting-channel mapping.

The spend_sheet module produces granular SKU column keys (17 today). The
Forecasting Engine + Loop view operate on a smaller 5-channel taxonomy.
This module is the single canonical bridge.

Mapping rationale:
  - Anything tied to a Google/Meta auction goes into paid_search/paid_social
    by primary placement type, not by the deal SKU name
  - retargeting + display sit on paid_search inventory (Google Display
    Network); bucketing them there matches where the spend actually lands
  - youtube + ctv are video inventory — bucket with paid_social since
    that's where social-video lives
  - demand_gen is a Google Ads campaign type that runs across both —
    bucket paid_social since the audience targeting is the differentiator
  - geofence / social_posting / email-style SKUs (eblast, email_drip)
    bucket as 'creative' (lower-attribution, ambient)
  - mgmt_fee + website_hosting are excluded from channel allocation
    (they're operational, not channel-attributed)
"""

# Spend sheet column key → Forecasting channel
SKU_TO_CHANNEL = {
    # ── Paid Search (Google ads + display + retargeting) ─────────────────────
    "search":         "paid_search",
    "pmax":           "paid_search",
    "display":        "paid_search",
    "retargeting":    "paid_search",

    # ── Paid Social (Meta + TikTok + YouTube + CTV + Demand Gen) ─────────────
    "paid_social":    "paid_social",
    "tiktok":         "paid_social",
    "youtube":        "paid_social",
    "ctv":            "paid_social",
    "demand_gen":     "paid_social",

    # ── SEO ──────────────────────────────────────────────────────────────────
    "seo":            "seo",

    # ── Reputation ───────────────────────────────────────────────────────────
    "reputation":     "reputation",

    # ── Creative / Brand / Email ─────────────────────────────────────────────
    "social_posting": "creative",
    "eblast":         "creative",
    "email_drip":     "creative",
    "geofence":       "creative",

    # ── Excluded from channel allocation (operational, not channel) ──────────
    # "mgmt_fee":       <excluded>
    # "website_hosting":<excluded>
}

# Order matters for downstream display
CHANNELS = ("paid_search", "paid_social", "seo", "reputation", "creative")


def aggregate_to_channels(by_sku: dict) -> dict:
    """Aggregate a {sku_key: amount} dict into a {channel: amount} dict.

    Returns a dict with all 5 channels present (zeros when no SKU mapped).
    SKUs not in SKU_TO_CHANNEL are silently dropped (they're operational
    line items like mgmt_fee / website_hosting that don't represent
    media spend the Loop should attribute).
    """
    out = {c: 0.0 for c in CHANNELS}
    for sku, amt in (by_sku or {}).items():
        ch = SKU_TO_CHANNEL.get(sku)
        if ch and amt:
            try:
                out[ch] += float(amt)
            except (TypeError, ValueError):
                pass
    return out
