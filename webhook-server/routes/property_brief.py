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
  <title>RPM Community Brief — Review</title>
  <style>
    :root {
      --ink: #1a1f2c;
      --muted: #5f6776;
      --line: #e3e6ec;
      --bg: #f6f7fa;
      --accent: #1a6b1a;
      --accent-warm: #b07000;
      --pill-edited: #e6f0ff;
      --pill-edited-ink: #1d4faa;
      --pill-pending: #f6efe1;
      --pill-pending-ink: #8a6312;
      --pill-auto: #eef2f7;
      --pill-auto-ink: #4a5567;
    }
    * { box-sizing: border-box; }
    body { font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
           max-width: 880px; margin: 0 auto; padding: 2rem 1.25rem 6rem;
           color: var(--ink); background: white; }
    header h1 { font-size: 1.6rem; margin: 0 0 .15rem; }
    header .lede { color: var(--muted); font-size: .95rem; }
    header .meta { color: var(--muted); font-size: .85rem; margin-top: .35rem; }
    section { margin-top: 2rem; border: 1px solid var(--line); border-radius: 10px;
              background: white; overflow: hidden; }
    section h2 { font-size: 1.05rem; margin: 0; padding: .85rem 1.1rem;
                 background: var(--bg); border-bottom: 1px solid var(--line); }
    .row { display: grid; grid-template-columns: 200px 1fr auto;
           gap: 1rem; align-items: start;
           padding: .8rem 1.1rem; border-bottom: 1px solid var(--line); }
    .row:last-child { border-bottom: 0; }
    .row .label { font-weight: 600; color: var(--ink); }
    .row .label .hint { display:block; font-weight: 400; color: var(--muted);
                        font-size: .8rem; margin-top: .15rem; line-height: 1.35; }
    .row .value { white-space: pre-wrap; word-break: break-word; min-height: 1.4em;
                  color: var(--ink); padding: .15rem 0; }
    .row .value.empty { color: var(--muted); font-style: italic; }
    .row .src { display: inline-block; font-size: .7rem; padding: .15rem .55rem;
                border-radius: 999px; white-space: nowrap; }
    .src-edited { background: var(--pill-edited); color: var(--pill-edited-ink); }
    .src-pending { background: var(--pill-pending); color: var(--pill-pending-ink); }
    .src-auto { background: var(--pill-auto); color: var(--pill-auto-ink); }
    .src-notset { background: var(--pill-auto); color: var(--pill-auto-ink); }
    .editable .value { cursor: pointer; }
    .editable .value:hover { background: #fafbfd; }
    .edit-input { width: 100%; padding: .45rem .55rem; font: inherit;
                  border: 1px solid #c2c8d0; border-radius: 6px; }
    textarea.edit-input { min-height: 6em; }
    .row.editing .value { display: none; }
    .row.editing .src { display: none; }
    .edit-form { display: none; flex-direction: column; gap: .45rem; width: 100%; grid-column: 2; }
    .row.editing .edit-form { display: flex; }
    .edit-actions { display: flex; gap: .5rem; }
    .btn { padding: .45rem .9rem; font-size: .9rem; font-weight: 500;
           border: 1px solid var(--line); background: white; border-radius: 6px;
           cursor: pointer; color: var(--ink); }
    .btn-primary { background: var(--accent); color: white; border-color: var(--accent); }
    .btn-warn { background: var(--accent-warm); color: white; border-color: var(--accent-warm); }
    .save-pulse { animation: savep 1.4s ease-out; }
    @keyframes savep { 0% { background: #e7f5e8; } 100% { background: white; } }
    .footer-bar { position: fixed; left: 0; right: 0; bottom: 0;
                  background: white; border-top: 1px solid var(--line);
                  padding: .85rem 1.25rem; display: flex; gap: .9rem;
                  align-items: center; justify-content: space-between; }
    .footer-bar .syncline { color: var(--muted); font-size: .82rem; }
    .footer-bar .actions { display: flex; gap: .55rem; }
    .err { color: #b00020; padding: 1rem; border: 1px solid #b00020; border-radius: 8px; }
    .ok  { color: var(--accent); padding: 1rem; border: 1px solid var(--accent); border-radius: 8px; }
    .preview-pane { background: var(--bg); border: 1px solid var(--line);
                    border-radius: 8px; padding: 1rem 1.1rem; margin-top: 1rem;
                    white-space: pre-wrap; font-size: .94rem; max-height: 22rem; overflow: auto; }
    @media (max-width: 700px) {
      .row { grid-template-columns: 1fr; }
      .row .src { justify-self: start; }
    }
  </style>
</head>
<body>
{% if error %}
  <header><h1>This link is no longer valid</h1></header>
  <div class="err">{{ error }}</div>
  <p>If you reached this in error, the original ClickUp ticket has the latest link.</p>
{% elif reviewed_just_now %}
  <header><h1>Thanks — marked as reviewed.</h1></header>
  <div class="ok">Edits sync to Fluency on the next daily run (6 AM Central).</div>
{% else %}
  <header>
    <h1>Community Brief — {{ property_name or "Property" }}</h1>
    <div class="lede">Confirm what's right, edit what isn't. Each save updates HubSpot — Fluency picks it up at the next daily sync.</div>
    {% if last_reviewed_iso %}
      <div class="meta">Last reviewed clean: {{ last_reviewed_iso }}</div>
    {% endif %}
  </header>

  {% for sec in sections %}
  <section data-section="{{ sec.section }}">
    <h2>{{ sec.section }}</h2>
    {% for r in sec.rows %}
      <div class="row {% if r.editable %}editable{% endif %}" data-key="{{ r.key }}" data-type="{{ r.type }}">
        <div class="label">
          {{ r.label }}
          {% if r.hint %}<span class="hint">{{ r.hint }}</span>{% endif %}
        </div>
        <div class="value {% if not r.value %}empty{% endif %}"
             data-value="{{ r.value }}"
             onclick="{% if r.editable %}startEdit(this){% endif %}">{{ r.value if r.value else (r.pending and "Pending — fills in once Apt IQ data lands" or "Not set — click to add") }}</div>
        <div>
          {% if r.has_override %}
            <span class="src src-edited">Edited</span>
          {% elif r.pending %}
            <span class="src src-pending">Pending</span>
          {% elif not r.value %}
            <span class="src src-notset">Not set</span>
          {% else %}
            <span class="src src-auto">{{ r.source or "Auto" }}</span>
          {% endif %}
        </div>
        {% if r.editable %}
          <div class="edit-form">
            {% if r.type == 'dropdown' %}
              <select class="edit-input" data-key="{{ r.key }}">
                <option value="">— not set —</option>
                {% for opt in r.options %}
                  <option value="{{ opt }}" {% if opt == r.value %}selected{% endif %}>{{ opt }}</option>
                {% endfor %}
              </select>
            {% elif r.type == 'textarea' %}
              <textarea class="edit-input" data-key="{{ r.key }}">{{ r.value }}</textarea>
            {% else %}
              <input type="text" class="edit-input" data-key="{{ r.key }}" value="{{ r.value }}">
            {% endif %}
            <div class="edit-actions">
              <button type="button" class="btn btn-primary" onclick="saveEdit(this)">Save</button>
              <button type="button" class="btn" onclick="cancelEdit(this)">Cancel</button>
            </div>
          </div>
        {% endif %}
      </div>
    {% endfor %}
  </section>
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

<script>
const TOKEN = {{ token | tojson }};

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
    // Update the row UI in place.
    const valueEl = row.querySelector('.value');
    valueEl.textContent = value || 'Not set — click to add';
    valueEl.classList.toggle('empty', !value);
    valueEl.setAttribute('data-value', value);
    const src = row.querySelector('.src');
    if (src) { src.className = value ? 'src src-edited' : 'src src-notset';
               src.textContent = value ? 'Edited' : 'Not set'; }
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
    """
    record = store.get(token)
    if not record:
        return jsonify({"error": "invalid token"}), 410
    company_props = community_brief.load_company_state(record.get("company_id") or "")
    prose = community_brief.generate_prose_preview(
        company_props, company_props.get("name", "")
    )
    return jsonify({"status": "ok", "prose": prose})


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
