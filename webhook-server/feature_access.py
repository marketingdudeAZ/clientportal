"""Portal feature access — per-user Beta/Prod partitioning.

The one job of this module: given the logged-in person (their portal
email) and a feature key, decide whether that person may see the feature.

Why it exists
-------------
We roll features out Beta → Prod. We need to (a) hand a Beta surface to
internal staff and a hand-picked set of clients, and (b) flip a feature
to everyone WITHOUT a code deploy. So the two knobs are split:

  * A feature has a STAGE — "off" | "beta" | "ga". This is the rollout
    state. Promoting Beta → Prod is changing it from "beta" to "ga".
  * A person has a ROLE — "internal" | "client". Internal staff see Beta
    features; clients see GA features (plus any Beta feature they've been
    explicitly allowlisted into).

Both knobs live in DATA (HubDB), so promotion is a row edit in the
HubSpot UI — no deploy. Code only carries safe DEFAULTS so the portal
behaves sanely before the HubDB tables are provisioned or if HubSpot is
unreachable: an unknown/unconfigured feature defaults to "beta", i.e.
visible to internal staff and hidden from clients. Nothing new leaks to
clients by accident.

HubDB schema (provision once, then edit rows in the HubSpot UI)
---------------------------------------------------------------
Feature-stage table  (env: HUBDB_FEATURE_STAGE_TABLE_ID)
    feature_key  TEXT    e.g. "redlight"
    stage        TEXT    one of: off | beta | ga

Portal-access table  (env: HUBDB_PORTAL_ACCESS_TABLE_ID)
    email          TEXT  lowercased login email
    role           TEXT  internal | client   (default client)
    beta_features  TEXT  comma-separated feature keys this email may see
                         while still in Beta; "*" means all Beta features

Resolution
----------
    stage == "ga"   → any authenticated user
    stage == "beta" → internal always; client only if allowlisted
    stage == "off"  → nobody (kill switch)

Reads are cached for a short TTL so per-request gating doesn't hammer
HubSpot. Call `clear_cache()` in tests.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from config import (
    HUBDB_FEATURE_STAGE_TABLE_ID,
    HUBDB_PORTAL_ACCESS_TABLE_ID,
    INTERNAL_EMAILS,
    RPM_EMAIL_DOMAIN,
)
from hubdb_helpers import read_rows

logger = logging.getLogger(__name__)

STAGE_OFF = "off"
STAGE_BETA = "beta"
STAGE_GA = "ga"
_VALID_STAGES = (STAGE_OFF, STAGE_BETA, STAGE_GA)

ROLE_INTERNAL = "internal"
ROLE_CLIENT = "client"

# How long resolved HubDB tables are cached, in seconds. Short enough that
# flipping a stage in the HubSpot UI takes effect within ~a minute.
_CACHE_TTL = 60


@dataclass(frozen=True)
class Feature:
    """A gateable portal surface.

    default_stage is the fallback used when the feature has no row in the
    HubDB stage table (or HubSpot is unreachable). Default to STAGE_BETA
    for anything client-facing so it stays internal-only until promoted.
    """

    key: str
    label: str
    default_stage: str = STAGE_BETA


# Registry of known features. Adding a key here makes it gateable and
# surfaces it in the /api/portal/features manifest. The STAGE still lives
# in HubDB — this only sets the label and the pre-provisioning default.
FEATURES: dict[str, Feature] = {
    f.key: f
    for f in (
        Feature("redlight", "Redlight Report / Health Score"),
        Feature("community_brief", "Community Brief"),
        Feature("quote_all_services", "All Marketing Services on Quote"),
        Feature("clickup_loop", "ClickUp → Company Notes Loop"),
        # Fast-follow surfaces — registered now so they can be gated the
        # moment they ship, without touching this file again.
        Feature("budgeting_forecasting", "Budgeting & Forecasting"),
        Feature("call_prep", "Call Prep"),
    )
}


# --- Tiny TTL cache -------------------------------------------------------

_cache: dict[str, tuple[float, object]] = {}


def clear_cache() -> None:
    """Drop all cached HubDB reads. Call in tests after changing fixtures."""
    _cache.clear()


def _cached(key: str, loader):
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


# --- HubDB loaders --------------------------------------------------------


def _load_stage_table() -> dict[str, str]:
    """feature_key → stage, from HubDB. Empty if unconfigured/unreachable."""
    if not HUBDB_FEATURE_STAGE_TABLE_ID:
        return {}
    out: dict[str, str] = {}
    for row in read_rows(HUBDB_FEATURE_STAGE_TABLE_ID):
        fk = str(row.get("feature_key") or "").strip()
        stage = str(row.get("stage") or "").strip().lower()
        if fk and stage in _VALID_STAGES:
            out[fk] = stage
    return out


def _load_access_table() -> dict[str, dict]:
    """email → {role, beta_features:set}, from HubDB."""
    if not HUBDB_PORTAL_ACCESS_TABLE_ID:
        return {}
    out: dict[str, dict] = {}
    for row in read_rows(HUBDB_PORTAL_ACCESS_TABLE_ID):
        email = str(row.get("email") or "").strip().lower()
        if not email:
            continue
        role = str(row.get("role") or "").strip().lower()
        role = ROLE_INTERNAL if role == ROLE_INTERNAL else ROLE_CLIENT
        raw = str(row.get("beta_features") or "")
        beta = {p.strip() for p in raw.split(",") if p.strip()}
        out[email] = {"role": role, "beta_features": beta}
    return out


def _stage_map() -> dict[str, str]:
    return _cached("stages", _load_stage_table)  # type: ignore[return-value]


def _access_map() -> dict[str, dict]:
    return _cached("access", _load_access_table)  # type: ignore[return-value]


# --- Public API -----------------------------------------------------------


def role_for(email: str | None) -> str | None:
    """Resolve a login email to "internal", "client", or None (no email).

    Internal if the email is on the RPM domain, listed in INTERNAL_EMAILS,
    or marked internal in the HubDB access table. Everyone else is a
    client. An empty email is anonymous (None) — gating treats it as a
    client with no allowlist.
    """
    if not email:
        return None
    email = email.lower().strip()
    if email.endswith("@" + RPM_EMAIL_DOMAIN):
        return ROLE_INTERNAL
    if email in INTERNAL_EMAILS:
        return ROLE_INTERNAL
    row = _access_map().get(email)
    if row and row.get("role") == ROLE_INTERNAL:
        return ROLE_INTERNAL
    return ROLE_CLIENT


def stage_for(feature_key: str) -> str:
    """Current rollout stage of a feature.

    HubDB row wins; otherwise the registry default; otherwise "beta" for
    an unknown key (safe: internal-only until configured).
    """
    override = _stage_map().get(feature_key)
    if override:
        return override
    feat = FEATURES.get(feature_key)
    return feat.default_stage if feat else STAGE_BETA


def _client_allowlisted(email: str, feature_key: str) -> bool:
    row = _access_map().get(email.lower().strip())
    if not row:
        return False
    beta = row.get("beta_features") or set()
    return "*" in beta or feature_key in beta


def can_access(email: str | None, feature_key: str) -> bool:
    """Whether the logged-in `email` may see `feature_key`."""
    stage = stage_for(feature_key)
    if stage == STAGE_OFF:
        return False
    if stage == STAGE_GA:
        return bool(email)  # any authenticated user
    # Beta: internal always; client only if explicitly allowlisted.
    if role_for(email) == ROLE_INTERNAL:
        return True
    return bool(email) and _client_allowlisted(email, feature_key)


def resolve_features(email: str | None) -> dict[str, dict]:
    """Visibility manifest for the UI: key → {label, stage, visible}.

    The CMS template uses `visible` to show/hide surfaces. Server-side
    `require_access` is still the real enforcement — this is just so the
    client doesn't render links to things it can't open.
    """
    out: dict[str, dict] = {}
    for key, feat in FEATURES.items():
        out[key] = {
            "label": feat.label,
            "stage": stage_for(key),
            "visible": can_access(email, key),
        }
    return out
