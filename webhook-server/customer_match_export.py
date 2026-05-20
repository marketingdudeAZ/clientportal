"""Google Ads Customer Match — hashing + CSV export module.

Reference: https://support.google.com/google-ads/answer/7659867
("Format and upload your customer data")

This module is the SINGLE point in our codebase that touches raw email,
phone, and name strings. Once `hash_contact()` returns, the raw values
are dropped — nothing downstream stores them. That's intentional:
keeps PII surface narrow + auditable.

Public surface:

  normalize_email(s)             -> str
  normalize_phone(s, country)    -> str  (E.164: '+1...')
  normalize_name(s)              -> str  (lowercase, trim, no punct)
  sha256_hex(s)                  -> str | None

  hash_contact(props)            -> dict
      Takes a HubSpot contact properties dict, returns the BQ-row-shaped
      hashed payload. Raw PII fields are NOT in the returned dict.

  build_csv_bytes(rows)          -> bytes
      Renders a Customer Match CSV using Google's expected column headers
      (Email, Phone, First Name, Last Name, Country, Zip).

  write_csv_to_gcs(rows, bucket, blob_name) -> str
      Uploads the CSV to GCS. Returns the gs:// URI. Best-effort: logs
      and returns "" on failure.

Notes:
  - Google accepts EITHER raw email/phone OR hashed; we always hash to
    minimize what travels over the wire and what sits in our BQ.
  - Country must be ISO 3166-1 alpha-2, uppercase ("US"). Postal code
    raw — Google does its own normalization (5-digit US is standard).
  - Empty / None inputs produce None outputs; the CSV emits empty
    string for those cells. Google evaluates row-level match by ANY
    match key — every populated key increases match probability.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import re
import unicodedata
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# ── Normalization ────────────────────────────────────────────────────────────

def _strip_to_ascii(s: str) -> str:
    """Best-effort ASCII fold: 'José' → 'jose', 'Müller' → 'muller'.
    Customer Match matches more often on ASCII-folded values."""
    if not s:
        return ""
    n = unicodedata.normalize("NFKD", s)
    return "".join(c for c in n if not unicodedata.combining(c))


def normalize_email(email: Optional[str]) -> str:
    """Lowercase + trim. Returns '' for empty/None.

    Google's spec is lowercase + trim only; we do NOT do gmail-style
    canonicalization (removing dots, +alias) — Google handles that
    server-side.
    """
    if not email:
        return ""
    return str(email).strip().lower()


# Match anything that's not a digit or leading '+'
_PHONE_NON_DIGIT = re.compile(r"[^\d+]")


def normalize_phone(phone: Optional[str], country: str = "US") -> str:
    """Return E.164-style phone string ('+1XXXXXXXXXX' for US).

    Google's spec: include the leading '+' and the country code, no
    spaces, no dashes, no parens. Returns '' for empty/unparseable.

    Country default is US — change when expanding internationally. The
    fallback heuristic: if input already starts with '+', trust it; else
    strip non-digits and prefix '+1' if the cleaned result is exactly
    10 digits (US-style), '+' + country code prefix otherwise.
    """
    if not phone:
        return ""
    s = str(phone).strip()
    if not s:
        return ""

    if s.startswith("+"):
        # Trust the leading +; strip everything else non-digit
        digits_after_plus = re.sub(r"\D", "", s[1:])
        if not digits_after_plus:
            return ""
        return "+" + digits_after_plus

    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""

    # Country fallback — minimal mapping. Extend as we go international.
    country_codes = {"US": "1", "CA": "1", "MX": "52", "GB": "44"}
    cc = country_codes.get((country or "US").upper(), "1")

    # If the cleaned digits already start with the country code AND the
    # total length matches the expected (e.g., '1' + 10 digits for US),
    # assume the country code is present and just prefix '+'.
    if cc == "1" and len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if cc == "1" and len(digits) == 10:
        return "+1" + digits
    if digits.startswith(cc):
        return "+" + digits
    return "+" + cc + digits


# Name normalization — lowercase, ASCII fold, strip non-word chars.
_NAME_CLEAN = re.compile(r"[^\w]+", flags=re.UNICODE)


def normalize_name(name: Optional[str]) -> str:
    """Lowercase, ASCII-fold, strip punctuation. Returns '' for empty/None."""
    if not name:
        return ""
    s = _strip_to_ascii(str(name).strip().lower())
    s = _NAME_CLEAN.sub("", s)
    return s


def normalize_country(country: Optional[str]) -> str:
    """ISO 3166-1 alpha-2, uppercase. Empty for unknown."""
    if not country:
        return ""
    s = str(country).strip().upper()
    # Common aliases
    aliases = {
        "USA": "US", "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US",
        "CAN": "CA", "CANADA": "CA",
        "MEX": "MX", "MEXICO": "MX",
        "UK": "GB", "UNITED KINGDOM": "GB",
    }
    return aliases.get(s, s[:2])


def normalize_postal(postal: Optional[str]) -> str:
    """For US: keep the first 5 digits. For non-US: trim + uppercase.
    Google accepts both; the 5-digit form is what we'll have for nearly
    every contact in our DB."""
    if not postal:
        return ""
    s = str(postal).strip().upper()
    # If purely digits or digit+dash like '78704-1234', keep first 5 digits
    digits_match = re.match(r"^\s*(\d{5})", s)
    if digits_match:
        return digits_match.group(1)
    return s


# ── Hashing ──────────────────────────────────────────────────────────────────

def sha256_hex(s: Optional[str]) -> Optional[str]:
    """SHA-256 hex digest, lowercase. Returns None for empty/None input."""
    if s in (None, ""):
        return None
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def hash_contact(props: dict, *, country_default: str = "US") -> dict:
    """Convert a HubSpot contact properties dict → hashed BQ row payload.

    Reads (HubSpot property names):
        email, phone, firstname, lastname, country, postal_code

    Returns:
        {
          email_sha256, phone_sha256, first_name_sha256, last_name_sha256,
          country, postal_code
        }
    Raw PII is NOT in the returned dict. The caller adds contact_id,
    list_id, synced_at to round out the BQ row.
    """
    country = normalize_country(props.get("country") or country_default)

    return {
        "email_sha256":      sha256_hex(normalize_email(props.get("email"))),
        "phone_sha256":      sha256_hex(normalize_phone(props.get("phone"), country=country)),
        "first_name_sha256": sha256_hex(normalize_name(props.get("firstname"))),
        "last_name_sha256":  sha256_hex(normalize_name(props.get("lastname"))),
        "country":           country,
        "postal_code":       normalize_postal(props.get("postal_code") or props.get("zip")),
    }


def signature_for(contact_id: str, list_id: str, hashed: dict) -> str:
    """Short SHA-1 of the identity fields. Lets us detect "did anything
    change since last sync" for the same (list_id, contact_id) — and
    serves as an idempotency key for cross-run dedupe."""
    base = "|".join([
        str(list_id or ""),
        str(contact_id or ""),
        str(hashed.get("email_sha256") or ""),
        str(hashed.get("phone_sha256") or ""),
        str(hashed.get("first_name_sha256") or ""),
        str(hashed.get("last_name_sha256") or ""),
    ])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


# ── CSV rendering ────────────────────────────────────────────────────────────

# Google Customer Match column headers (case-insensitive on their side).
# Per spec at https://support.google.com/google-ads/answer/7659867:
#   Email, Phone, First Name, Last Name, Country, Zip
CSV_HEADERS = ["Email", "Phone", "First Name", "Last Name", "Country", "Zip"]


def build_csv_bytes(rows: Iterable[dict]) -> bytes:
    """Render an Iterable of hashed-row dicts into the Google CSV format.

    Each row dict must have the hashed keys produced by hash_contact()
    (email_sha256, phone_sha256, first_name_sha256, last_name_sha256,
    country, postal_code). Empty values render as empty cells (per Google
    spec — partial keys are allowed).
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_HEADERS)
    n = 0
    for r in rows:
        w.writerow([
            r.get("email_sha256") or "",
            r.get("phone_sha256") or "",
            r.get("first_name_sha256") or "",
            r.get("last_name_sha256") or "",
            r.get("country") or "",
            r.get("postal_code") or "",
        ])
        n += 1
    logger.info("Customer Match CSV: %d rows rendered", n)
    return buf.getvalue().encode("utf-8")


# ── GCS upload ───────────────────────────────────────────────────────────────

def write_csv_to_gcs(csv_bytes: bytes, bucket: str, blob_name: str) -> str:
    """Upload bytes to gs://{bucket}/{blob_name}. Returns the gs:// URI
    on success, '' on failure. Best-effort: logs the error and returns ''
    rather than raising.

    Uses the same service-account JSON path as the rest of the BQ stack
    (BIGQUERY_SERVICE_ACCOUNT_JSON env var, either a JSON string or a
    path to a .json file). Project comes from BIGQUERY_PROJECT_ID.
    """
    if not (csv_bytes and bucket and blob_name):
        logger.warning("write_csv_to_gcs: missing csv/bucket/blob_name")
        return ""
    sa = os.environ.get("BIGQUERY_SERVICE_ACCOUNT_JSON", "")
    project = os.environ.get("BIGQUERY_PROJECT_ID", "")
    if not (sa and project):
        logger.warning("write_csv_to_gcs: BQ env not configured")
        return ""

    try:
        import json as _json
        from google.cloud import storage
        from google.oauth2 import service_account
        info = _json.loads(sa) if sa.strip().startswith("{") else _json.load(open(sa))
        creds = service_account.Credentials.from_service_account_info(info)
        client = storage.Client(project=project, credentials=creds)
        bkt = client.bucket(bucket)
        blob = bkt.blob(blob_name)
        blob.upload_from_string(csv_bytes, content_type="text/csv")
        uri = f"gs://{bucket}/{blob_name}"
        logger.info("Customer Match CSV uploaded: %s (%d bytes)", uri, len(csv_bytes))
        return uri
    except Exception as exc:
        logger.warning("write_csv_to_gcs failed: %s", exc)
        return ""
