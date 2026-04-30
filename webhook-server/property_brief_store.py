"""Token-keyed brief store for the property-brief approval portal.

The store persists one row per brief revision and indexes by an unguessable
URL-safe token. Two backends:

  - HubDB (production) — when HUBDB_PROPERTY_BRIEFS_TABLE_ID is set the
    rows live in a HubDB table so they survive process restarts and the
    approval URL keeps working across the multiple Render workers.
  - In-memory (tests/dev) — when the HubDB table isn't configured we fall
    back to a process-local dict. This lets the unit tests run hermetically
    and gives a sensible local-dev default.

Public surface:

  create(...)        Allocate a token, persist the row, return the record.
  get(token)         Look up by token (None if missing or expired).
  consume(...)       Atomically mark approved/needs_edits and stamp the
                     decided_at + decided_by so the token can't be reused.
  attach_revision(...)
                     Re-issue a token after a needs-edits run; bumps
                     revision_count, keeps the original ticket linkage.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from typing import Any

import requests

from config import (
    HUBDB_PROPERTY_BRIEFS_TABLE_ID,
    HUBSPOT_API_KEY,
    PROPERTY_BRIEF_TOKEN_TTL_HOURS,
)

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
_HS_HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

# Brief lifecycle states.
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_NEEDS_EDITS = "needs_edits"
STATUS_EXPIRED = "expired"
STATUS_ESCALATED = "escalated"


# ── Token + record helpers ─────────────────────────────────────────────────

def _now_ms() -> int:
    return int(time.time() * 1000)


def _ttl_ms() -> int:
    return PROPERTY_BRIEF_TOKEN_TTL_HOURS * 60 * 60 * 1000


def _new_token() -> str:
    # 256 bits of entropy — overkill for the threat model, but cheap and
    # forecloses any "maybe-guessable" debate during review.
    return secrets.token_urlsafe(32)


def _new_record(
    *,
    ticket_id: str,
    company_id: str,
    deal_id: str | None,
    submitter_email: str,
    rm_email: str,
    brief_markdown: str,
    revision_count: int = 0,
) -> dict[str, Any]:
    now = _now_ms()
    return {
        "token":           _new_token(),
        "ticket_id":       ticket_id,
        "company_id":      company_id,
        "deal_id":         deal_id,
        "submitter_email": submitter_email,
        "rm_email":        rm_email,
        "brief_markdown":  brief_markdown,
        "revision_count":  revision_count,
        "feedback_history": [],
        "status":          STATUS_PENDING,
        "created_at_ms":   now,
        "expires_at_ms":   now + _ttl_ms(),
        "decided_at_ms":   None,
        "decided_by":      None,
    }


# ── Public API ─────────────────────────────────────────────────────────────

def create(
    *,
    ticket_id: str,
    company_id: str,
    deal_id: str | None,
    submitter_email: str,
    rm_email: str,
    brief_markdown: str,
) -> dict[str, Any]:
    """Persist a new brief and return the full record (including the token)."""
    record = _new_record(
        ticket_id=ticket_id,
        company_id=company_id,
        deal_id=deal_id,
        submitter_email=submitter_email,
        rm_email=rm_email,
        brief_markdown=brief_markdown,
    )
    _backend().put(record)
    return record


def attach_revision(
    *,
    previous: dict[str, Any],
    brief_markdown: str,
    feedback: str,
) -> dict[str, Any]:
    """Issue a new token + record after a needs-edits run.

    The previous record's feedback is preserved on the new record so the
    LLM and the ops team can see the full revision history.
    """
    history = list(previous.get("feedback_history") or [])
    if feedback:
        history.append(feedback)

    record = _new_record(
        ticket_id=previous["ticket_id"],
        company_id=previous["company_id"],
        deal_id=previous.get("deal_id"),
        submitter_email=previous.get("submitter_email", ""),
        rm_email=previous.get("rm_email", ""),
        brief_markdown=brief_markdown,
        revision_count=int(previous.get("revision_count") or 0) + 1,
    )
    record["feedback_history"] = history
    _backend().put(record)
    return record


def get(token: str) -> dict[str, Any] | None:
    """Look up by token. Returns None if missing, consumed, or expired."""
    if not token:
        return None
    record = _backend().get(token)
    if not record:
        return None

    # Tokens become single-use the moment a decision lands; the read path
    # treats already-consumed tokens as missing so the portal returns the
    # standard "no longer valid" page.
    if record.get("status") in (STATUS_APPROVED, STATUS_NEEDS_EDITS, STATUS_ESCALATED):
        return None

    expires = int(record.get("expires_at_ms") or 0)
    if expires and _now_ms() > expires:
        record["status"] = STATUS_EXPIRED
        _backend().put(record)
        return None

    return record


def consume(token: str, *, decision: str, decided_by: str, feedback: str = "") -> dict[str, Any] | None:
    """Atomically capture the decision on a pending brief.

    Returns the updated record, or None if the token was already consumed,
    expired, or unknown.
    """
    if decision not in (STATUS_APPROVED, STATUS_NEEDS_EDITS):
        raise ValueError(f"Unknown decision: {decision}")

    backend = _backend()
    with backend.lock(token):
        record = backend.get(token)
        if not record:
            return None
        if record.get("status") != STATUS_PENDING:
            return None
        expires = int(record.get("expires_at_ms") or 0)
        if expires and _now_ms() > expires:
            record["status"] = STATUS_EXPIRED
            backend.put(record)
            return None

        record["status"] = decision
        record["decided_at_ms"] = _now_ms()
        record["decided_by"] = decided_by
        if feedback:
            history = list(record.get("feedback_history") or [])
            history.append(feedback)
            record["feedback_history"] = history
        backend.put(record)
        return record


def find_by_ticket(ticket_id: str) -> list[dict[str, Any]]:
    """Return every brief record (any status) for a ClickUp ticket, newest first."""
    if not ticket_id:
        return []
    return _backend().find_by_ticket(ticket_id)


# ── Backends ───────────────────────────────────────────────────────────────

class _MemoryBackend:
    """Process-local store. Used in tests and when HubDB isn't configured."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        # Reentrant — `consume()` holds the lock then calls `get()` which
        # re-acquires it.
        self._lock = threading.RLock()

    def get(self, token: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows.get(token)
            return dict(row) if row else None

    def put(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._rows[record["token"]] = dict(record)

    def find_by_ticket(self, ticket_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(r) for r in self._rows.values() if r.get("ticket_id") == ticket_id]
        rows.sort(key=lambda r: r.get("created_at_ms") or 0, reverse=True)
        return rows

    def lock(self, token: str):
        # Single global lock is fine — the store is already process-local
        # and per-token contention is negligible compared to the network.
        return self._lock

    def reset(self) -> None:
        with self._lock:
            self._rows.clear()


class _HubDBBackend:
    """Persist brief rows in a HubDB table. Schema:

        token          (text, indexed)
        ticket_id      (text)
        company_id     (text)
        deal_id        (text)
        submitter_email (text)
        rm_email       (text)
        brief_markdown (rich_text)
        revision_count (number)
        feedback_history (text — JSON list)
        status         (text)
        created_at_ms  (number)
        expires_at_ms  (number)
        decided_at_ms  (number)
        decided_by     (text)
    """

    def __init__(self, table_id: str) -> None:
        self._table_id = table_id
        self._lock = threading.Lock()  # cross-row writes are rare; one lock is fine

    def _row_url(self, row_id: str | None = None) -> str:
        base = f"{HS_BASE}/cms/v3/hubdb/tables/{self._table_id}/rows"
        return f"{base}/{row_id}" if row_id else base

    def _to_values(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "token":             record["token"],
            "ticket_id":         record.get("ticket_id"),
            "company_id":        record.get("company_id"),
            "deal_id":           record.get("deal_id"),
            "submitter_email":   record.get("submitter_email"),
            "rm_email":          record.get("rm_email"),
            "brief_markdown":    record.get("brief_markdown"),
            "revision_count":    record.get("revision_count"),
            "feedback_history":  json.dumps(record.get("feedback_history") or []),
            "status":            record.get("status"),
            "created_at_ms":     record.get("created_at_ms"),
            "expires_at_ms":     record.get("expires_at_ms"),
            "decided_at_ms":     record.get("decided_at_ms"),
            "decided_by":        record.get("decided_by"),
        }

    def _from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        v = row.get("values") or {}
        try:
            history = json.loads(v.get("feedback_history") or "[]")
        except (TypeError, ValueError):
            history = []
        return {
            "row_id":            row.get("id"),
            "token":             v.get("token"),
            "ticket_id":         v.get("ticket_id"),
            "company_id":        v.get("company_id"),
            "deal_id":           v.get("deal_id"),
            "submitter_email":   v.get("submitter_email"),
            "rm_email":          v.get("rm_email"),
            "brief_markdown":    v.get("brief_markdown"),
            "revision_count":    int(v.get("revision_count") or 0),
            "feedback_history":  history,
            "status":            v.get("status"),
            "created_at_ms":     int(v.get("created_at_ms") or 0),
            "expires_at_ms":     int(v.get("expires_at_ms") or 0),
            "decided_at_ms":     v.get("decided_at_ms"),
            "decided_by":        v.get("decided_by"),
        }

    def _find_row_id(self, token: str) -> str | None:
        try:
            r = requests.get(
                self._row_url(),
                headers=_HS_HEADERS,
                params={"token__eq": token, "limit": 1},
                timeout=10,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            logger.warning("HubDB brief lookup failed for token: %s", e)
            return None
        results = r.json().get("results") or []
        return results[0]["id"] if results else None

    def get(self, token: str) -> dict[str, Any] | None:
        row_id = self._find_row_id(token)
        if not row_id:
            return None
        try:
            r = requests.get(self._row_url(row_id), headers=_HS_HEADERS, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.warning("HubDB row read failed for %s: %s", row_id, e)
            return None
        return self._from_row(r.json())

    def put(self, record: dict[str, Any]) -> None:
        values = self._to_values(record)
        row_id = record.get("row_id") or self._find_row_id(record["token"])
        try:
            if row_id:
                r = requests.patch(
                    self._row_url(row_id),
                    headers=_HS_HEADERS,
                    json={"values": values},
                    timeout=10,
                )
            else:
                r = requests.post(
                    self._row_url(),
                    headers=_HS_HEADERS,
                    json={"values": values},
                    timeout=10,
                )
            r.raise_for_status()
            # Publish so the row is immediately readable via the published API.
            requests.post(
                f"{HS_BASE}/cms/v3/hubdb/tables/{self._table_id}/draft/publish",
                headers=_HS_HEADERS,
                timeout=10,
            )
        except requests.RequestException as e:
            logger.error("HubDB brief upsert failed: %s", e)

    def find_by_ticket(self, ticket_id: str) -> list[dict[str, Any]]:
        try:
            r = requests.get(
                self._row_url(),
                headers=_HS_HEADERS,
                params={"ticket_id__eq": ticket_id, "limit": 50},
                timeout=10,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            logger.warning("HubDB brief find_by_ticket failed: %s", e)
            return []
        rows = [self._from_row(row) for row in r.json().get("results") or []]
        rows.sort(key=lambda r: r.get("created_at_ms") or 0, reverse=True)
        return rows

    def lock(self, token: str):
        return self._lock


# Lazy singleton — pick the backend based on what's configured. Tests can
# override via `set_backend()`.
_BACKEND: Any = None


def _backend():
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    if HUBDB_PROPERTY_BRIEFS_TABLE_ID and HUBSPOT_API_KEY:
        _BACKEND = _HubDBBackend(HUBDB_PROPERTY_BRIEFS_TABLE_ID)
    else:
        _BACKEND = _MemoryBackend()
    return _BACKEND


def set_backend(backend: Any) -> None:
    """Swap the store backend. Tests use this to inject _MemoryBackend()."""
    global _BACKEND
    _BACKEND = backend


def reset_for_tests() -> None:
    """Drop every record. Only safe to call in tests."""
    backend = _backend()
    if hasattr(backend, "reset"):
        backend.reset()
