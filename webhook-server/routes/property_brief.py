"""Property Brief Automation routes.

Endpoint surface:

  POST /webhooks/clickup/property-brief        ClickUp ticket created/updated
  POST /webhooks/hubspot/quote-signed          HubSpot quote-signed event
  GET  /property-brief/approve/<token>         Hosted approval portal (HTML)
  POST /api/property-brief/approve/<token>     Submitter decision (form/JSON)

All three webhook entry points are HMAC-signed; the approval portal is
gated by the unguessable per-brief token. There is no user auth — the
token is the auth.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading

from flask import Blueprint, jsonify, render_template_string, request

import clickup_client
import community_brief
import property_brief
import property_brief_store as store
from config import (
    CLICKUP_WEBHOOK_SECRET,
    HUBSPOT_QUOTE_WEBHOOK_SECRET,
)

logger = logging.getLogger(__name__)

property_brief_bp = Blueprint("property_brief", __name__)


# ── In-process retry mutex ─────────────────────────────────────────────────
#
# ClickUp retries any webhook delivery that takes longer than ~15s. Path A
# alone (13 line-item creates + quote create + signer/RVP find-or-create
# + ~30 association PUTs) routinely exceeds that. Without a mutex, a single
# ClickUp ticket fires 5-7 retries before our async daemon completes and
# the brief-store idempotency takes over — and during that window we
# create duplicate deals + duplicate briefs.
#
# This in-memory set is the first line of defence: the FIRST webhook
# delivery for a given ticket_id holds the slot until its daemon thread
# finishes. Any retry that arrives during that window sees the slot and
# 200s immediately without doing work.
#
# Survives within a single worker process. Cross-restart and cross-worker
# retries fall back to the brief-store check (slower-consistent but still
# correct — duplicate briefs would just no-op).

_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()


def _try_claim(ticket_id: str) -> bool:
    """Return True if this caller is now the owner of `ticket_id` work.

    False means another worker thread is already processing this ticket;
    the caller should drop the request.
    """
    with _in_flight_lock:
        if ticket_id in _in_flight:
            return False
        _in_flight.add(ticket_id)
        return True


def _release_claim(ticket_id: str) -> None:
    with _in_flight_lock:
        _in_flight.discard(ticket_id)


# ── Webhook: ClickUp ticket created/updated ────────────────────────────────

@property_brief_bp.route("/webhooks/clickup/property-brief", methods=["POST"])
def clickup_webhook():
    raw = request.get_data() or b""
    sig = request.headers.get("X-Signature", "")
    if not _verify_clickup_signature(raw, sig):
        return jsonify({"error": "invalid signature"}), 401

    try:
        event = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return jsonify({"error": "invalid json"}), 400

    task_id = event.get("task_id") or (event.get("payload") or {}).get("task_id")
    if not task_id:
        return jsonify({"error": "missing task_id"}), 400

    task = clickup_client.get_task(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404

    if not property_brief.should_fire(event, task):
        return jsonify({"status": "skipped", "reason": "trigger gate"}), 200

    try:
        parsed = property_brief.parse_ticket(task)
    except property_brief.TicketParseError as e:
        clickup_client.post_comment(
            task_id,
            f"Community Brief automation could not start: {e}. Please update the ticket and re-trigger.",
        )
        return jsonify({"status": "blocked", "error": str(e)}), 200

    # Idempotency layer 1: brief store. If we already finished a prior
    # delivery for this ticket, the brief record sticks around — short
    # circuit fast.
    if store.find_by_ticket(parsed.get("ticket_id") or ""):
        logger.info("Ticket %s already has a brief record — skipping pipeline", task_id)
        return jsonify({"status": "skipped", "reason": "already_processed"}), 200

    # Idempotency layer 2: in-process mutex. The brief store check loses
    # to retries that arrive while the FIRST delivery's daemon thread
    # is mid-pipeline (record not written yet). The mutex covers that
    # window — first claimer wins, others 200 immediately.
    if not _try_claim(task_id):
        logger.info("Ticket %s already in-flight — skipping retry", task_id)
        return jsonify({"status": "skipped", "reason": "in_flight"}), 200

    # Whole pipeline — Path A (commercial) + Path B (brief) — runs async.
    # Path A alone takes 15-25s with 13 line-item creates + 13 quote-line
    # associations + signer/RVP find-or-create + associations. That's
    # already past ClickUp's webhook timeout (~15s), even before the LLM.
    # Returning 200 in under a second is the only way to keep ClickUp
    # from retrying mid-pipeline and creating duplicate deals/briefs.
    threading.Thread(
        target=_run_pipeline_async,
        args=(parsed,),
        daemon=True,
        name=f"pipeline-{task_id}",
    ).start()

    return jsonify({
        "status":     "dispatched",
        "ticket_id":  task_id,
    }), 200


def _run_pipeline_async(parsed: dict) -> None:
    """Daemon-thread wrapper for the whole post-parse pipeline.

    Runs Path A (commercial) → comment in ClickUp → Path B (brief). The
    in-flight claim is released no matter what so a future taskUpdated
    re-fire can re-enter. Per-step errors are surfaced as ClickUp
    comments since the synchronous handler has already returned.
    """
    task_id = parsed.get("ticket_id") or ""
    try:
        # Path A — commercial.
        try:
            commercial = property_brief.run_commercial_path(parsed)
        except property_brief.CompanyMatchAmbiguous as e:
            clickup_client.post_comment(
                task_id,
                f"{e}. Stopped automation; please pick the correct HubSpot company manually.",
            )
            return
        except Exception as e:
            logger.exception("Commercial path failed for ticket %s", task_id)
            try:
                clickup_client.post_comment(
                    task_id, f"Community Brief automation hit a HubSpot error: {e}"
                )
            except Exception:
                pass
            return

        try:
            property_brief.comment_commercial_result(parsed, commercial)
        except Exception:
            logger.exception("Failed to post commercial comment for %s", task_id)

        # Path B — brief. Uses its own internal idempotency: if a record
        # already exists for this ticket (e.g., another worker raced us
        # past the in-process mutex), run_brief_path just no-ops.
        try:
            property_brief.run_brief_path(parsed, commercial)
        except Exception as e:
            logger.exception("Async brief path failed for ticket %s", task_id)
            try:
                clickup_client.post_comment(
                    task_id,
                    f"Community Brief generation failed: {e}. Toggle the re-fire flag to retry.",
                )
            except Exception:
                pass
    finally:
        _release_claim(task_id)


# ── Webhook: HubSpot quote signed ──────────────────────────────────────────

@property_brief_bp.route("/webhooks/hubspot/quote-signed", methods=["POST"])
def hubspot_quote_signed():
    raw = request.get_data() or b""
    sig = request.headers.get("X-HubSpot-Signature-V3") or request.headers.get("X-Signature", "")
    if not _verify_hubspot_signature(raw, sig):
        return jsonify({"error": "invalid signature"}), 401

    try:
        events = json.loads(raw or b"[]")
    except json.JSONDecodeError:
        return jsonify({"error": "invalid json"}), 400

    if isinstance(events, dict):
        events = [events]

    results = []
    for event in events or []:
        deal_id = (
            event.get("dealId")
            or event.get("objectId")
            or (event.get("properties") or {}).get("hs_deal_id")
        )
        results.append(property_brief.handle_quote_signed(str(deal_id) if deal_id else ""))
    return jsonify({"status": "ok", "results": results}), 200


# ── Community Brief portal ─────────────────────────────────────────────────
#
# Renamed from "Property Brief" 2026-05-08 — "Community Brief" matches how
# property stakeholders refer to it. URL path stays /property-brief/approve/
# for back-compat with links already in flight.

_COMMUNITY_BRIEF_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RPM Community Brief — {{ property_name or "Review" }}</title>
  <style>
    :root {
      --ink: #1f2937;
      --muted: #6b7280;
      --line: #e5e7eb;
      --bg: #f3f4f6;
      --card: #ffffff;
      --accent: #2563eb;
      --accent-deep: #1d4ed8;
      --green: #16803d;
      --warn: #b07000;
      --pill-pipe-bg: #eef2ff;
      --pill-pipe-ink: #3730a3;
      --pill-over-bg: #ecfeff;
      --pill-over-ink: #155e75;
      --pill-pending-bg: #f3f4f6;
      --pill-pending-ink: #6b7280;
      --pill-tag-bg: #f0fdfa;
      --pill-tag-ink: #115e59;
    }
    * { box-sizing: border-box; }
    body { font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
           margin: 0; padding: 2rem 1.25rem 7rem; color: var(--ink); background: var(--bg); }
    .wrap { max-width: 1080px; margin: 0 auto; }
    header h1 { font-size: 1.65rem; margin: 0 0 .25rem; letter-spacing: -0.01em; }
    header .lede { color: var(--muted); font-size: .95rem; max-width: 640px; }
    header .meta { color: var(--muted); font-size: .82rem; margin-top: .4rem; }

    .summary-card { background: var(--card); border: 1px solid var(--line);
                    border-radius: 12px; padding: 1.1rem 1.25rem; margin-top: 1.5rem;
                    box-shadow: 0 1px 2px rgba(15,23,42,.04); }
    .summary-card .summary-head { display: flex; justify-content: space-between; align-items: center;
                                  margin-bottom: .55rem; }
    .summary-card h3 { font-size: .78rem; letter-spacing: .08em; text-transform: uppercase;
                       color: var(--muted); margin: 0; font-weight: 700; }
    .summary-card .summary-body { font-size: 1rem; line-height: 1.55; color: var(--ink); white-space: pre-wrap; }
    .summary-card .summary-body.loading { color: var(--muted); font-style: italic; }
    .summary-card .btn { padding: .35rem .7rem; font-size: .78rem; }

    .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
            margin-top: 1rem; overflow: hidden;
            box-shadow: 0 1px 2px rgba(15,23,42,.04); }
    .card .card-head { padding: .75rem 1.25rem; border-bottom: 1px solid var(--line);
                       background: var(--card); }
    .card .card-head h2 { font-size: .78rem; letter-spacing: .08em; text-transform: uppercase;
                          margin: 0; color: var(--ink); font-weight: 700; }

    .row { display: grid; grid-template-columns: 200px 1fr 130px;
           gap: 1.25rem; align-items: start;
           padding: .9rem 1.25rem; border-bottom: 1px solid var(--line); }
    .row:last-child { border-bottom: 0; }
    .row .label { font-size: .72rem; letter-spacing: .06em; text-transform: uppercase;
                  color: var(--muted); font-weight: 700; padding-top: .15rem; }
    .row .label .hint { display: block; font-weight: 500; color: var(--muted);
                        font-size: .7rem; letter-spacing: 0; text-transform: none;
                        margin-top: .35rem; line-height: 1.45; }
    .row .value { color: var(--ink); white-space: pre-wrap; word-break: break-word; min-height: 1.45em; }
    .row .value.empty { color: var(--muted); font-style: italic; }
    .row .badge-cell { text-align: right; }
    .badge { display: inline-block; font-size: .65rem; letter-spacing: .08em;
             text-transform: uppercase; font-weight: 600;
             padding: .25rem .55rem; border-radius: 999px; white-space: nowrap; }
    .badge-pipeline { background: var(--pill-pipe-bg); color: var(--pill-pipe-ink); }
    .badge-override { background: var(--pill-over-bg); color: var(--pill-over-ink); }
    .badge-pending  { background: var(--pill-pending-bg); color: var(--pill-pending-ink); }

    .pills { display: flex; flex-wrap: wrap; gap: .35rem .4rem; }
    .pill { display: inline-block; padding: .2rem .65rem; font-size: .82rem;
            border-radius: 999px; background: var(--pill-tag-bg);
            color: var(--pill-tag-ink); white-space: nowrap; }

    .row.editable .value { cursor: pointer; border-bottom: 1px dashed transparent;
                           transition: border-color .15s; }
    .row.editable .value:hover { border-bottom-color: var(--accent); }
    .row.editing .value, .row.editing .badge-cell { display: none; }
    .edit-form { display: none; flex-direction: column; gap: .45rem;
                 grid-column: 2 / span 2; }
    .row.editing .edit-form { display: flex; }
    .edit-input { width: 100%; padding: .5rem .65rem; font: inherit;
                  border: 1px solid #cbd5e1; border-radius: 6px;
                  background: #fff; color: var(--ink); }
    textarea.edit-input { min-height: 6em; resize: vertical; }
    .edit-actions { display: flex; gap: .5rem; }
    .btn { padding: .45rem .9rem; font-size: .85rem; font-weight: 500;
           border: 1px solid var(--line); background: var(--card);
           border-radius: 6px; cursor: pointer; color: var(--ink); }
    .btn:hover { background: #fafbfd; }
    .btn-primary { background: var(--accent); color: white; border-color: var(--accent); }
    .btn-primary:hover { background: var(--accent-deep); }
    .save-pulse { animation: savep 1.4s ease-out; }
    @keyframes savep { 0% { background: #ecfdf5; } 100% { background: var(--card); } }
    .footer-bar { position: fixed; left: 0; right: 0; bottom: 0;
                  background: var(--card); border-top: 1px solid var(--line);
                  padding: .9rem 1.5rem; display: flex; gap: .9rem;
                  align-items: center; justify-content: space-between;
                  box-shadow: 0 -2px 10px rgba(15,23,42,.05); }
    .footer-bar .syncline { color: var(--muted); font-size: .82rem; max-width: 600px; }
    .footer-bar .actions { display: flex; gap: .55rem; }
    .err { color: #b00020; padding: 1rem; border: 1px solid #b00020; border-radius: 8px; background: #fff; }
    .ok  { color: var(--green); padding: 1rem; border: 1px solid var(--green); border-radius: 8px; background: #fff; }
    .preview-pane { background: var(--card); border: 1px solid var(--line);
                    border-radius: 12px; padding: 1.1rem 1.25rem; margin-top: 1rem;
                    white-space: pre-wrap; font-size: .95rem; line-height: 1.55;
                    max-height: 28rem; overflow: auto;
                    box-shadow: 0 1px 2px rgba(15,23,42,.04); }
    @media (max-width: 740px) {
      .row { grid-template-columns: 1fr; }
      .row .badge-cell { text-align: left; }
      .edit-form { grid-column: 1; }
    }
  </style>
</head>
<body>
<div class="wrap">
{% if error %}
  <header><h1>This link is no longer valid</h1></header>
  <div class="err">{{ error }}</div>
  <p>If you reached this in error, the original ClickUp ticket has the latest link.</p>
{% elif reviewed_just_now %}
  <header><h1>Thanks — marked as reviewed.</h1></header>
  <div class="ok">Edits sync to Fluency on the next daily run (6 AM Central).</div>
{% else %}
  <header>
    <h1>{{ property_name or "Community Brief" }}</h1>
    <div class="lede">Confirm what's right, edit what isn't. Each save updates HubSpot — Fluency picks it up at the next daily sync.</div>
    {% if last_reviewed_iso %}
      <div class="meta">Last reviewed clean: {{ last_reviewed_iso }}</div>
    {% endif %}
  </header>

  <div class="summary-card">
    <div class="summary-head">
      <h3>Summary</h3>
      <button class="btn" onclick="loadSummary(true)">Refresh</button>
    </div>
    <div id="summary-body" class="summary-body loading">Generating summary from your brief data…</div>
  </div>

  {% for sec in sections %}
  <div class="card" data-section="{{ sec.section }}">
    <div class="card-head"><h2>{{ sec.section }}</h2></div>
    {% for r in sec.rows %}
      <div class="row {% if r.editable %}editable{% endif %}" data-key="{{ r.key }}" data-type="{{ r.type }}">
        <div class="label">
          {{ r.label }}
          {% if r.hint %}<span class="hint">{{ r.hint }}</span>{% endif %}
        </div>
        <div class="value {% if not r.value %}empty{% endif %}"
             data-value="{{ r.value }}"
             onclick="{% if r.editable %}startEdit(this){% endif %}">
          {% if r.pills and r.pills|length > 1 %}
            <div class="pills">
              {% for p in r.pills %}<span class="pill">{{ p }}</span>{% endfor %}
            </div>
          {% elif r.value %}
            {{ r.value }}
          {% else %}
            Not yet computed
          {% endif %}
        </div>
        <div class="badge-cell">
          <span class="badge badge-{{ r.badge_kind }}">{{ r.badge }}</span>
        </div>
        {% if r.editable %}
          <div class="edit-form">
            {% if r.type == 'dropdown' %}
              <select class="edit-input">
                <option value="">— not set —</option>
                {% for opt in r.options %}
                  <option value="{{ opt }}" {% if opt == r.value %}selected{% endif %}>{{ opt }}</option>
                {% endfor %}
              </select>
            {% elif r.type == 'textarea' %}
              <textarea class="edit-input" placeholder="One per line">{{ r.value }}</textarea>
            {% else %}
              <input type="text" class="edit-input" value="{{ r.value }}">
            {% endif %}
            <div class="edit-actions">
              <button type="button" class="btn btn-primary" onclick="saveEdit(this)">Save</button>
              <button type="button" class="btn" onclick="cancelEdit(this)">Cancel</button>
            </div>
          </div>
        {% endif %}
      </div>
    {% endfor %}
  </div>
  {% endfor %}

  <div id="preview-area"></div>

  <div class="footer-bar">
    <div class="syncline">Edits land in HubSpot immediately. They sync to Fluency at the next daily run (6 AM Central).</div>
    <div class="actions">
      <button class="btn" onclick="loadPreview()">Preview as document</button>
      <button class="btn btn-primary" onclick="markReviewed()">Looks good</button>
    </div>
  </div>
{% endif %}
</div>

<script>
const TOKEN = {{ token | tojson }};

async function loadSummary(forceRefresh) {
  const el = document.getElementById('summary-body');
  if (!el) return;
  el.classList.add('loading');
  el.textContent = 'Generating summary from your brief data…';
  try {
    const r = await fetch(`/api/community-brief/${TOKEN}/summary` +
                          (forceRefresh ? '?refresh=1' : ''),
                          {method: 'POST'});
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    el.classList.remove('loading');
    el.textContent = d.summary || 'No summary available yet.';
  } catch (e) {
    el.textContent = 'Summary unavailable right now: ' + e.message;
  }
}

function startEdit(valueEl) {
  const row = valueEl.closest('.row');
  if (!row.classList.contains('editable')) return;
  row.classList.add('editing');
  const input = row.querySelector('.edit-input');
  if (input) input.focus();
}
function cancelEdit(btn) {
  btn.closest('.row').classList.remove('editing');
}
async function saveEdit(btn) {
  const row = btn.closest('.row');
  const input = row.querySelector('.edit-input');
  const key = row.getAttribute('data-key');
  const value = input.value;
  btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const r = await fetch(`/api/community-brief/${TOKEN}/field`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key, value}),
    });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    // Re-render the value cell with pills if there are multiple lines.
    const valueEl = row.querySelector('.value');
    const pieces = (value || '').split(/\\r?\\n|,/).map(s=>s.trim()).filter(Boolean);
    if (pieces.length > 1) {
      valueEl.innerHTML = '<div class="pills">' +
        pieces.map(p => '<span class="pill">' + p.replace(/</g,'&lt;') + '</span>').join('') +
        '</div>';
    } else {
      valueEl.textContent = value || 'Not yet computed';
    }
    valueEl.classList.toggle('empty', !value);
    valueEl.setAttribute('data-value', value);
    const badge = row.querySelector('.badge');
    if (badge) {
      badge.className = 'badge ' + (value ? 'badge-override' : 'badge-pending');
      badge.textContent = value ? 'OVERRIDE' : 'OVERRIDE PENDING';
    }
    row.classList.remove('editing');
    row.classList.add('save-pulse');
    setTimeout(() => row.classList.remove('save-pulse'), 1400);
  } catch (e) {
    alert('Save failed: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Save';
  }
}
async function loadPreview() {
  const area = document.getElementById('preview-area');
  area.innerHTML = '<div class="preview-pane">Generating preview…</div>';
  try {
    const r = await fetch(`/api/community-brief/${TOKEN}/preview`, {method: 'POST'});
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    area.innerHTML = '<div class="preview-pane">' + (d.prose || '').replace(/&/g,'&amp;').replace(/</g,'&lt;') + '</div>';
  } catch (e) {
    area.innerHTML = '<div class="err">Preview failed: ' + e.message + '</div>';
  }
}
async function markReviewed() {
  if (!confirm('Mark this community brief as reviewed clean? You can keep editing afterwards.')) return;
  try {
    const r = await fetch(`/api/community-brief/${TOKEN}/approve`, {method: 'POST'});
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    location.reload();
  } catch (e) {
    alert('Mark-reviewed failed: ' + e.message);
  }
}

// Auto-load the summary on page load.
window.addEventListener('DOMContentLoaded', () => loadSummary(false));
</script>
</body>
</html>
"""


def _render_brief(token: str, record: dict | None, *, reviewed_just_now: bool = False, error: str | None = None):
    """Render the Community Brief page for a given token + record.

    Pulls the latest HubSpot company state at render time so edits made
    in another tab show up after refresh.
    """
    if error:
        return render_template_string(
            _COMMUNITY_BRIEF_TEMPLATE, token=token, error=error,
            reviewed_just_now=False, sections=[], property_name="",
            last_reviewed_iso="",
        ), 410
    if reviewed_just_now:
        # Success-after-submit page. No record to render fields from.
        return render_template_string(
            _COMMUNITY_BRIEF_TEMPLATE, token=token, error=None,
            reviewed_just_now=True, sections=[], property_name="",
            last_reviewed_iso="",
        )
    if not record:
        return render_template_string(
            _COMMUNITY_BRIEF_TEMPLATE, token=token,
            error="This link is no longer valid.",
            reviewed_just_now=False, sections=[], property_name="",
            last_reviewed_iso="",
        ), 410
    company_props = community_brief.load_company_state(record.get("company_id") or "")
    sections = community_brief.build_render_context(company_props)
    last_iso = ""
    last_ms = record.get("last_reviewed_at_ms") or 0
    if last_ms:
        from datetime import datetime, timezone
        last_iso = datetime.fromtimestamp(int(last_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return render_template_string(
        _COMMUNITY_BRIEF_TEMPLATE,
        token=token,
        error=None,
        reviewed_just_now=reviewed_just_now,
        sections=sections,
        property_name=company_props.get("name", ""),
        last_reviewed_iso=last_iso,
    )


@property_brief_bp.route("/property-brief/approve/<token>", methods=["GET"])
def render_approval(token):
    return _render_brief(token, store.get(token))


@property_brief_bp.route("/api/community-brief/<token>/field", methods=["PATCH"])
def patch_field(token):
    """Update a single editable field. Writes to the override property."""
    record = store.get(token)
    if not record:
        return jsonify({"error": "invalid token"}), 410
    payload = request.get_json(silent=True) or {}
    key = (payload.get("key") or "").strip()
    value = payload.get("value")
    if value is None:
        value = ""
    ok, message = community_brief.write_field(record.get("company_id") or "", key, str(value))
    if not ok:
        return jsonify({"error": message}), 400
    return jsonify({"status": "ok", "value": message})


@property_brief_bp.route("/api/community-brief/<token>/preview", methods=["POST"])
def preview_brief(token):
    """Render an LLM-narrative preview from the current structured fields.

    On-demand only — not persisted. Always reflects current values.
    Sections covered: Property Overview, Voice + Tier, What to Say,
    Guardrails. Channel Strategy + Success Metrics intentionally
    excluded — those are commercial / measurement concerns that don't
    belong in the qualitative community brief.
    """
    record = store.get(token)
    if not record:
        return jsonify({"error": "invalid token"}), 410
    company_props = community_brief.load_company_state(record.get("company_id") or "")
    prose = community_brief.generate_prose_preview(
        company_props, company_props.get("name", "")
    )
    return jsonify({"status": "ok", "prose": prose})


@property_brief_bp.route("/api/community-brief/<token>/summary", methods=["POST"])
def brief_summary(token):
    """Return a 2-3 sentence executive summary for the brief header.

    Cached on the brief record after first generation; pass ?refresh=1
    to regenerate after edits land. Fair-Housing-safe by construction
    (the prompt forbids demographic targeting language).
    """
    record = store.get(token)
    if not record:
        return jsonify({"error": "invalid token"}), 410
    refresh = request.args.get("refresh") == "1"
    cached = (record.get("summary_text") or "").strip()
    if cached and not refresh:
        return jsonify({"status": "ok", "summary": cached, "cached": True})

    company_props = community_brief.load_company_state(record.get("company_id") or "")
    summary = community_brief.generate_summary(
        company_props, company_props.get("name", "")
    )
    record["summary_text"] = summary
    try:
        store._backend().put(record)  # noqa: SLF001
    except Exception:
        logger.exception("summary cache write failed for %s", token)
    return jsonify({"status": "ok", "summary": summary, "cached": False})


@property_brief_bp.route("/api/community-brief/<token>/approve", methods=["POST"])
def mark_reviewed(token):
    """Stamp the brief as 'reviewed clean' without freezing it.

    Reviewer can keep editing afterwards. We just record when it was
    last confirmed so we have an audit trail.

    Important: we do NOT call store.consume() or set status=APPROVED.
    Those would make store.get() return None on subsequent loads (the
    legacy single-use-token semantics). The editable model needs the
    record to keep resolving forever.
    """
    record = store.get(token)
    if not record:
        return jsonify({"error": "invalid token"}), 410
    import time as _t
    record["last_reviewed_at_ms"] = int(_t.time() * 1000)
    record["last_reviewed_by"] = (request.headers.get("X-Portal-Email") or "").strip().lower()
    try:
        store._backend().put(record)  # noqa: SLF001 — direct write, not via consume
    except Exception as e:
        logger.exception("mark_reviewed put failed for %s", token)
        return jsonify({"error": str(e)}), 500
    # Side effects: stamp brief markdown on HubSpot company, post ClickUp
    # comment, advance ticket status. Idempotent — safe to call on every
    # "Looks good" click.
    try:
        property_brief.handle_approval(record)
    except Exception:
        logger.exception("handle_approval side-effects failed for %s", token)
    return jsonify({"status": "ok",
                    "last_reviewed_at_ms": record["last_reviewed_at_ms"]})


# ── Legacy markdown-blob approval (kept for back-compat with old links) ────
#
# Old approval URL format submits to POST /api/property-brief/approve/<token>
# with form-encoded "decision" + "feedback". The new portal uses the JSON
# /api/community-brief/<token>/approve endpoint above. Keep this around so
# any in-flight links from before the rename still resolve.

@property_brief_bp.route("/api/property-brief/approve/<token>", methods=["POST"])
def submit_approval_legacy(token):
    payload = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    decision = (payload.get("decision") or "").strip().lower()
    feedback = (payload.get("feedback") or "").strip()
    decided_by = (payload.get("decided_by") or request.headers.get("X-Portal-Email") or "").strip().lower()

    if decision not in (store.STATUS_APPROVED, store.STATUS_NEEDS_EDITS):
        return jsonify({"error": "decision must be 'approved' or 'needs_edits'"}), 400

    record = store.consume(
        token,
        decision=decision,
        decided_by=decided_by,
        feedback=feedback if decision == store.STATUS_NEEDS_EDITS else "",
    )
    if not record:
        return _render_brief(token, None, error="This link has expired or already been used.")

    if decision == store.STATUS_APPROVED:
        try:
            property_brief.handle_approval(record)
        except Exception as e:
            logger.exception("handle_approval failed for token %s", token)
            return jsonify({"status": "error", "error": str(e)}), 500
    else:
        try:
            property_brief.handle_needs_edits(record)
        except Exception as e:
            logger.exception("handle_needs_edits failed for token %s", token)
            return jsonify({"status": "error", "error": str(e)}), 500

    if request.is_json:
        return jsonify({"status": "ok", "decision": decision})
    return _render_brief(token, None, reviewed_just_now=True)


# ── Signature verification ─────────────────────────────────────────────────

def _verify_clickup_signature(raw: bytes, signature: str) -> bool:
    if not CLICKUP_WEBHOOK_SECRET:
        # In dev environments without a secret configured, we accept the
        # request but log loudly so it shows up in any audit.
        logger.warning("CLICKUP_WEBHOOK_SECRET not set — accepting unsigned webhook")
        return True
    if not signature:
        return False
    # ClickUp generates a server-side secret per webhook — there's no API
    # to set them all to the same value. Support a comma-separated list
    # so multiple webhooks (one per list) can verify against the same
    # env var. Whitespace around individual secrets is tolerated.
    candidates = [s.strip() for s in CLICKUP_WEBHOOK_SECRET.split(",") if s.strip()]
    for secret in candidates:
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        # ClickUp delivers the hex digest with no prefix.
        if hmac.compare_digest(expected, signature):
            return True
    return False


def _verify_hubspot_signature(raw: bytes, signature: str) -> bool:
    if not HUBSPOT_QUOTE_WEBHOOK_SECRET:
        logger.warning("HUBSPOT_QUOTE_WEBHOOK_SECRET not set — accepting unsigned webhook")
        return True
    if not signature:
        return False
    expected = hmac.new(HUBSPOT_QUOTE_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    candidates = (expected, f"sha256={expected}")
    return any(hmac.compare_digest(c, signature) for c in candidates)
