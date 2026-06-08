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
import os
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

# Recently-processed tickets, mapped to the epoch second we first saw them.
# Replaces the old release-on-completion mutex: the previous design freed
# the slot as soon as the pipeline finished, so a webhook delivery arriving
# 5 seconds later (ClickUp Automations creating subtasks, the bot's own
# comment posts triggering taskUpdated, etc.) would pass the brief-store
# sentinel (HubDB read-after-write lag) AND find an empty mutex AND re-run
# the whole pipeline. Hold the claim for the full TTL window so any retry
# within 10 minutes short-circuits at 200 immediately.
_CLAIM_TTL_S = 600   # 10 minutes
_recent: dict[str, float] = {}
_recent_lock = threading.Lock()


def _try_claim(ticket_id: str) -> bool:
    """Return True if this is the first webhook delivery for `ticket_id`
    in the last _CLAIM_TTL_S seconds.

    False means we've already processed (or are processing) this ticket
    very recently — the caller should drop the request without re-running
    the pipeline. The 10-minute TTL covers the vast majority of duplicate
    delivery storms (ClickUp's subtask-creation cascade + comment-triggered
    taskUpdated events both burn through in seconds) without holding the
    claim forever — a legitimate re-fire via the manual flag after the
    TTL expires will still be processed.
    """
    import time as _time
    if not ticket_id:
        return True
    now = _time.time()
    with _recent_lock:
        ts = _recent.get(ticket_id, 0.0)
        if now - ts < _CLAIM_TTL_S:
            return False
        _recent[ticket_id] = now
        # Opportunistic GC so the dict doesn't grow without bound.
        if len(_recent) > 1000:
            cutoff = now - _CLAIM_TTL_S
            for k in [k for k, v in _recent.items() if v < cutoff]:
                _recent.pop(k, None)
        return True


def _release_claim(ticket_id: str) -> None:  # noqa: ARG001 - kept for callers
    """No-op. Claims expire by TTL, not on pipeline completion. See _try_claim."""
    return None


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

    # Community Brief attestation gate. If this ticket carries the attestation
    # checkbox and it's unchecked, hold the work until someone confirms the
    # brief is current. Tickets without the field (e.g. brief intake) skip this.
    attested = property_brief.brief_attested(task)
    if attested is False:
        clickup_client.post_comment(
            task_id,
            "Hold: please confirm the checkbox “Community Brief is up to date & "
            "accurate” before this is sent to the team. Re-trigger once checked.",
        )
        return jsonify({"status": "blocked", "reason": "brief_attestation_required"}), 200

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

        # Cross-worker dedup: if run_commercial_path found that another
        # worker already finished the pipeline for this ticket, skip both
        # the comment AND the brief path. The original delivery handled
        # everything; posting again would just be noise.
        if commercial.get("already_processed"):
            logger.info("Ticket %s already processed by another worker — "
                        "skipping comment + brief", task_id)
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
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>RPM | Community Brief — {{ property_name or "Review" }}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    /* Match /accounts/property aesthetic 1:1 (jasper navy, copper, sage). */
    :root{
      --j:#53606C;--jd:#3D474F;--j1:#1A2530;--j2:#151F2B;
      --cu:#C8964E;--cul:#D4A86A;--cug:rgba(200,150,78,0.12);
      --sage:#8FA68E;--mint:#CDE3E0;--ml:#E8F4F2;
      --red:#C94444;--amb:#D4910A;--amb-bg:#FEF7E6;--amb-bd:#F5D58F;
      --rose:#FFF0F0;--rose-bd:#F2C8C8;
      --white:#fff;--bg:#F0F2F5;
      --tp:#1F2937;--ts:#6B7280;--tm:#9CA3AF;
      --bd:#E5E7EB;--bdl:#F3F4F6;
      --sh:0 1px 3px rgba(0,0,0,0.07),0 1px 2px rgba(0,0,0,0.04);
      --r:12px;
    }
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Montserrat',-apple-system,sans-serif;font-size:14px;color:var(--tp);background:var(--bg);padding-bottom:96px}
    .wrap{max-width:1100px;margin:0 auto;padding:24px}
    .hdr{background:var(--white);padding:28px 32px;border-radius:var(--r);box-shadow:var(--sh);margin-bottom:18px}
    .h-name{font-size:28px;font-weight:800;color:var(--j1);letter-spacing:-.02em}
    .h-sub{color:var(--ts);font-size:13px;margin-top:6px}
    .summary{background:var(--white);padding:22px 28px;border-radius:var(--r);box-shadow:var(--sh);margin-bottom:14px;border-left:3px solid var(--cu)}
    .summary-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
    .summary-head h3{font-size:11px;font-weight:800;color:var(--ts);text-transform:uppercase;letter-spacing:.08em}
    .summary-body{font-size:15px;line-height:1.6;color:var(--tp);white-space:pre-wrap}
    .summary-body.loading{color:var(--tm);font-style:italic;font-size:14px}
    .section{background:var(--white);padding:22px 28px;border-radius:var(--r);box-shadow:var(--sh);margin-bottom:14px}
    .section h2{font-size:13px;font-weight:800;color:var(--j1);text-transform:uppercase;letter-spacing:.08em;margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid var(--bdl)}
    .callout-warning{border:1.5px solid var(--amb-bd);background:var(--amb-bg)}
    .callout-warning .callout-banner{display:flex;align-items:center;gap:8px;background:var(--amb);color:var(--white);padding:8px 16px;margin:-22px -28px 16px -28px;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;border-radius:var(--r) var(--r) 0 0}
    .callout-warning .callout-banner::before{content:'⚠'}
    dl.fields{display:grid;grid-template-columns:200px 1fr 160px;column-gap:14px;row-gap:0}
    dl.fields .row{display:contents}
    dl.fields dt{padding:9px 0;font-size:12px;font-weight:700;color:var(--ts);text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--bdl);align-self:start}
    dl.fields dt .hint{display:block;font-weight:500;color:var(--tm);font-size:11px;letter-spacing:0;text-transform:none;margin-top:5px;line-height:1.45}
    dl.fields dd{padding:9px 0;border-bottom:1px solid var(--bdl);font-size:14px;align-self:start}
    dl.fields dd.value{color:var(--tp);font-weight:500;word-break:break-word;white-space:pre-wrap}
    dl.fields dd.value.editable{cursor:pointer;border-radius:6px;margin:5px -8px;padding:4px 8px}
    dl.fields dd.value.editable:hover{background:var(--ml)}
    dl.fields dd.empty{color:var(--tm);font-style:italic;font-weight:400}
    dl.fields dd.source{color:var(--tm);font-size:11px;text-transform:uppercase;letter-spacing:.05em;font-weight:600;text-align:right}
    .src-override{color:var(--sage)}
    .src-pipeline{color:var(--cu)}
    .src-pending{color:var(--tm)}
    .chip{display:inline-block;background:var(--ml);color:var(--jd);padding:3px 10px;border-radius:99px;font-size:11px;font-weight:700;margin:2px 4px 2px 0}
    .row.editing dd.value, .row.editing dd.source{display:none}
    .edit-cell{display:none;padding:9px 0;border-bottom:1px solid var(--bdl);grid-column:2 / span 2;align-self:start}
    .row.editing .edit-cell{display:flex;flex-direction:column;gap:8px}
    .edit-input{width:100%;padding:8px 12px;font-family:inherit;font-size:14px;border:1px solid var(--bd);border-radius:8px;background:var(--white);color:var(--tp)}
    textarea.edit-input{min-height:96px;resize:vertical;line-height:1.5}
    .edit-actions{display:flex;gap:8px}
    .btn{padding:8px 18px;font-family:inherit;font-size:13px;font-weight:600;border:1px solid var(--bd);background:var(--white);border-radius:8px;cursor:pointer;color:var(--tp);transition:all .15s}
    .btn:hover{background:var(--bdl)}
    .btn-primary{background:var(--j1);color:var(--white);border-color:var(--j1)}
    .btn-primary:hover{background:var(--j2)}
    .btn-cu{background:var(--cu);color:var(--white);border-color:var(--cu)}
    .btn-cu:hover{background:var(--cul)}
    .save-pulse{animation:savep 1.4s ease-out}
    @keyframes savep{0%{background:rgba(143,166,142,0.18)}100%{background:transparent}}
    .footer-bar{position:fixed;left:0;right:0;bottom:0;background:var(--white);border-top:1px solid var(--bd);padding:14px 24px;display:flex;gap:14px;align-items:center;justify-content:space-between;box-shadow:0 -2px 10px rgba(0,0,0,0.06);z-index:10}
    .footer-bar .syncline{color:var(--ts);font-size:12px;max-width:680px}
    .footer-bar .actions{display:flex;gap:8px}
    .err{color:var(--red);padding:14px 18px;border:1.5px solid var(--rose-bd);background:var(--rose);border-radius:var(--r);margin-top:12px}
    .ok{color:var(--sage);padding:14px 18px;border:1.5px solid var(--mint);background:var(--ml);border-radius:var(--r);margin-top:12px}
    .preview-pane{background:var(--white);padding:22px 28px;border-radius:var(--r);box-shadow:var(--sh);margin-top:14px;font-size:14px;line-height:1.6;white-space:pre-wrap;max-height:32rem;overflow:auto;color:var(--tp)}
    /* Structured tables (floorplans / tracking / documents) */
    table.tbl{width:100%;border-collapse:collapse;font-size:13px;margin-top:4px}
    table.tbl th{text-align:left;font-size:11px;font-weight:700;color:var(--ts);text-transform:uppercase;letter-spacing:.05em;padding:8px 10px;border-bottom:2px solid var(--bdl)}
    table.tbl td{padding:7px 10px;border-bottom:1px solid var(--bdl);color:var(--tp);vertical-align:middle}
    table.tbl tr:last-child td{border-bottom:none}
    table.tbl .src-label{font-weight:600;color:var(--jd)}
    table.tbl input.cell{width:100%;padding:6px 8px;font-family:inherit;font-size:13px;border:1px solid var(--bd);border-radius:6px;background:var(--white);color:var(--tp)}
    .tbl-empty{color:var(--tm);font-style:italic;font-size:13px;padding:6px 0}
    .doc-list{list-style:none;display:flex;flex-direction:column;gap:8px;margin:4px 0 12px}
    .doc-list li{display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--bg);border-radius:8px}
    .doc-list a{color:var(--j1);font-weight:600;text-decoration:none;word-break:break-all}
    .doc-list a:hover{text-decoration:underline}
    .doc-kind{font-size:10px;font-weight:700;color:var(--white);background:var(--sage);padding:2px 8px;border-radius:99px;text-transform:uppercase;letter-spacing:.04em}
    .doc-del{margin-left:auto;background:none;border:none;color:var(--red);cursor:pointer;font-size:18px;line-height:1;padding:0 4px}
    .doc-add{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
    .doc-add input{flex:1;min-width:160px;padding:8px 12px;font-family:inherit;font-size:13px;border:1px solid var(--bd);border-radius:8px}
    .tbl-save{margin-top:12px}
    .tbl-hint{color:var(--tm);font-size:12px;margin-bottom:10px;line-height:1.5}
    @media (max-width:760px){
      dl.fields{grid-template-columns:1fr}
      dl.fields dt{padding-top:14px;border:none}
      dl.fields dd{padding-bottom:14px}
      dl.fields dd.source{text-align:left;padding-top:0}
      .edit-cell{grid-column:1}
    }
  </style>
</head>
<body>
<div class="wrap">
{% if error %}
  <div class="hdr"><div class="h-name">This link is no longer valid</div></div>
  <div class="err">{{ error }}</div>
{% elif reviewed_just_now %}
  <div class="hdr"><div class="h-name">Thanks — marked as reviewed.</div></div>
  <div class="ok">Edits sync to Fluency on the next daily run (6 AM Central).</div>
{% else %}
  <div class="hdr">
    <div class="h-name">{{ property_name or "Community Brief" }}</div>
    <div class="h-sub">Community Brief · Confirm what's right, edit what isn't. Each save updates HubSpot — Fluency picks it up at the next daily sync.</div>
    {% if last_reviewed_iso %}
      <div class="h-sub">Last reviewed clean: {{ last_reviewed_iso }}</div>
    {% endif %}
  </div>

  <div class="summary">
    <div class="summary-head">
      <h3>Summary</h3>
      <button class="btn" onclick="loadSummary(true)">Refresh</button>
    </div>
    <div id="summary-body" class="summary-body loading">Generating summary from your brief data…</div>
  </div>

  {% for sec in sections %}
  <section class="section{% if sec.section == 'Guardrails' %} callout-warning{% endif %}" data-section="{{ sec.section }}">
    {% if sec.section == 'Guardrails' %}<div class="callout-banner">Voice guardrails — these shape what Fluency does and doesn't say</div>{% endif %}
    <h2>{{ sec.section }}</h2>
    {% set table_types = ['floorplan_table','tracking_table','documents'] %}
    <dl class="fields">
      {% for r in sec.rows if r.type not in table_types %}
      <div class="row {% if r.editable %}has-edit{% endif %}" data-key="{{ r.key }}" data-type="{{ r.type }}">
        <dt>
          {{ r.label }}
          {% if r.hint %}<span class="hint">{{ r.hint }}</span>{% endif %}
        </dt>
        <dd class="value {% if r.editable %}editable{% endif %} {% if not r.value %}empty{% endif %}"
            data-value="{{ r.value }}"
            onclick="{% if r.editable %}startEdit(this){% endif %}">
          {% if r.pills and r.pills|length > 1 %}
            {% for p in r.pills %}<span class="chip">{{ p }}</span>{% endfor %}
          {% elif r.value %}{{ r.value }}{% else %}Not yet computed{% endif %}
        </dd>
        <dd class="source src-{{ r.badge_kind }}">{{ r.badge }}</dd>
        {% if r.editable %}
        <dd class="edit-cell">
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
        </dd>
        {% endif %}
      </div>
      {% endfor %}
    </dl>

    {% for r in sec.rows if r.type in table_types %}
      {% if r.hint %}<div class="tbl-hint">{{ r.hint }}</div>{% endif %}

      {% if r.type == 'floorplan_table' %}
      <div data-key="{{ r.key }}" data-type="floorplan_table">
        {% if r.structured %}
        <table class="tbl">
          <thead><tr><th>Floor Plan</th><th>Beds</th><th>Baths</th><th>Sq Ft</th><th>Units</th><th>Avail</th></tr></thead>
          <tbody>
            {% for fp in r.structured %}
            <tr>
              <td class="src-label">{{ fp.name }}</td>
              <td>{% if fp.beds == 0 %}Studio{% else %}{{ fp.beds }}{% endif %}</td>
              <td>{{ fp.baths }}</td><td>{{ fp.sqft }}</td>
              <td>{{ fp.total_units }}</td><td>{{ fp.available }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
        {% else %}<div class="tbl-empty">Pending — floor plans load from Apt IQ once the property is onboarded.</div>{% endif %}
        <details style="margin-top:10px">
          <summary style="cursor:pointer;color:var(--ts);font-size:12px">Edit floor plans (advanced — JSON)</summary>
          <textarea id="fp-json" class="edit-input" style="margin-top:8px;min-height:140px;font-family:monospace">{{ r.value }}</textarea>
          <div class="tbl-save"><button class="btn btn-primary" onclick="saveJsonField('{{ r.key }}','fp-json')">Save floor plans</button></div>
        </details>
      </div>

      {% elif r.type == 'tracking_table' %}
      <table class="tbl" id="trk-table">
        <thead><tr><th style="width:30%">Source</th><th style="width:30%">Tracking Number</th><th>UTM</th></tr></thead>
        <tbody>
          {% for t in r.structured %}
          <tr data-source="{{ t.source }}">
            <td class="src-label">{{ t.source }}</td>
            <td><input class="cell trk-num" value="{{ t.tracking_number }}" placeholder="e.g. 512-555-0100"></td>
            <td><input class="cell trk-utm" value="{{ t.utm }}" placeholder="utm_source={{ t.utm_source }}&utm_medium={{ t.utm_medium }}"></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <div class="tbl-save"><button class="btn btn-primary" onclick="saveTracking('{{ r.key }}')">Save tracking &amp; UTMs</button></div>

      {% elif r.type == 'documents' %}
      <ul class="doc-list" id="doc-list">
        {% for d in r.structured %}
        <li data-url="{{ d.url }}" data-label="{{ d.label }}" data-kind="{{ d.kind }}">
          <a href="{{ d.url }}" target="_blank" rel="noopener">{{ d.label }}</a>
          {% if d.kind %}<span class="doc-kind">{{ d.kind }}</span>{% endif %}
          <button class="doc-del" title="Remove" onclick="removeDoc(this,'{{ r.key }}')">&times;</button>
        </li>
        {% endfor %}
      </ul>
      <div class="doc-add">
        <input id="doc-label" placeholder="Label (e.g. 2026 Pitch Deck)">
        <input id="doc-url" placeholder="https://… link to the file">
        <input id="doc-kind" placeholder="Kind (pitch_deck / rfp / brand_guide)" style="max-width:220px">
        <button class="btn btn-primary" onclick="addDoc('{{ r.key }}')">Add</button>
      </div>
      {% endif %}
    {% endfor %}
  </section>
  {% endfor %}

  <div id="preview-area"></div>

  <div class="footer-bar">
    <div class="syncline">Edits land in HubSpot immediately. They sync to Fluency at the next daily run (6 AM Central).</div>
    <div class="actions">
      <button class="btn" onclick="loadPreview()">Preview as document</button>
      <button class="btn btn-cu" onclick="markReviewed()">Looks good</button>
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
    const r = await fetch(`/api/community-brief/${TOKEN}/summary` + (forceRefresh ? '?refresh=1' : ''),
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
  if (!row) return;
  row.classList.add('editing');
  const input = row.querySelector('.edit-input');
  if (input) input.focus();
}
function cancelEdit(btn) { btn.closest('.row').classList.remove('editing'); }

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
    const valueEl = row.querySelector('dd.value');
    const pieces = (value || '').split(/\\r?\\n|,/).map(s=>s.trim()).filter(Boolean);
    if (pieces.length > 1) {
      valueEl.innerHTML = pieces.map(p => '<span class="chip">' + p.replace(/</g,'&lt;') + '</span>').join('');
    } else {
      valueEl.textContent = value || 'Not yet computed';
    }
    valueEl.classList.toggle('empty', !value);
    valueEl.setAttribute('data-value', value);
    const src = row.querySelector('dd.source');
    if (src) {
      src.className = 'source ' + (value ? 'src-override' : 'src-pending');
      src.textContent = value ? 'Edited' : 'Not set';
    }
    row.classList.remove('editing');
    row.classList.add('save-pulse');
    setTimeout(() => row.classList.remove('save-pulse'), 1400);
  } catch (e) {
    alert('Save failed: ' + e.message);
  } finally { btn.disabled = false; btn.textContent = 'Save'; }
}

async function patchField(key, value) {
  const r = await fetch(`/api/community-brief/${TOKEN}/field`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key, value}),
  });
  const d = await r.json();
  if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
  return d;
}

async function saveJsonField(key, textareaId) {
  const ta = document.getElementById(textareaId);
  try { await patchField(key, ta.value); alert('Saved.'); }
  catch (e) { alert('Save failed: ' + e.message); }
}

async function saveTracking(key) {
  const rows = [];
  document.querySelectorAll('#trk-table tbody tr').forEach(tr => {
    const num = tr.querySelector('.trk-num').value.trim();
    const utm = tr.querySelector('.trk-utm').value.trim();
    if (num || utm) rows.push({source: tr.getAttribute('data-source'),
                               tracking_number: num, utm: utm});
  });
  try { await patchField(key, JSON.stringify(rows)); alert('Tracking saved.'); }
  catch (e) { alert('Save failed: ' + e.message); }
}

function _collectDocs() {
  const out = [];
  document.querySelectorAll('#doc-list li').forEach(li => {
    out.push({label: li.getAttribute('data-label'), url: li.getAttribute('data-url'),
              kind: li.getAttribute('data-kind') || ''});
  });
  return out;
}
async function addDoc(key) {
  const label = document.getElementById('doc-label').value.trim();
  const url = document.getElementById('doc-url').value.trim();
  const kind = document.getElementById('doc-kind').value.trim();
  if (!url) { alert('A link (URL) is required.'); return; }
  const docs = _collectDocs();
  docs.push({label: label || url, url, kind});
  try { await patchField(key, JSON.stringify(docs)); location.reload(); }
  catch (e) { alert('Save failed: ' + e.message); }
}
async function removeDoc(btn, key) {
  btn.closest('li').remove();
  try { await patchField(key, JSON.stringify(_collectDocs())); }
  catch (e) { alert('Save failed: ' + e.message); }
}

async function loadPreview() {
  const area = document.getElementById('preview-area');
  area.innerHTML = '<div class="preview-pane">Generating preview…</div>';
  try {
    const r = await fetch(`/api/community-brief/${TOKEN}/preview`, {method: 'POST'});
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    area.innerHTML = '<div class="preview-pane">' + (d.prose || '').replace(/&/g,'&amp;').replace(/</g,'&lt;') + '</div>';
    area.scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch (e) {
    area.innerHTML = '<div class="err">Preview failed: ' + e.message + '</div>';
  }
}

async function markReviewed() {
  if (!confirm('Mark this Community Brief as reviewed clean? You can keep editing afterwards.')) return;
  try {
    const r = await fetch(`/api/community-brief/${TOKEN}/approve`, {method: 'POST'});
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    location.reload();
  } catch (e) {
    alert('Mark-reviewed failed: ' + e.message);
  }
}

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


# ── Community Brief auto-capture scan (daily cron) ─────────────────────────
#
# Trigger: plestatus == "RPM Managed". For each such property with no brief
# yet, run the AI capture, persist a brief + token, and write the approval
# link onto the company (rpm_brief_approval_url). Also advances the AptIQ
# exact-match retry clock (~30 days) on every property in scope.

@property_brief_bp.route("/api/internal/community-brief-capture-scan",
                         methods=["POST", "GET"])
def community_brief_capture_scan():
    # Internal server-to-server endpoint — same shared-secret gate as the
    # other /api/internal/* routes. Without this it ran unauthenticated,
    # exposing RPM-managed company data and letting anyone trigger a
    # capture scan that writes fluency_* overrides.
    expected = os.getenv("INTERNAL_API_KEY", "")
    if not (expected and request.headers.get("X-Internal-Key") == expected):
        return jsonify({"error": "Authentication required"}), 401

    import community_brief_capture as cap
    args = request.args
    dry_run = (args.get("dry_run") or "").lower() in ("1", "true", "yes")
    run_async = (args.get("async") or "").lower() in ("1", "true", "yes")
    force = (args.get("force") or "").lower() in ("1", "true", "yes")
    limit = args.get("limit", type=int)
    ids_raw = (args.get("company_ids") or "").strip()
    company_ids = {s.strip() for s in ids_raw.split(",") if s.strip()} or None

    try:
        companies = cap.fetch_rpm_managed_companies()
    except Exception as e:
        logger.exception("capture-scan: company fetch failed")
        return jsonify({"error": f"company fetch failed: {e}"}), 502

    if run_async and not dry_run:
        def _bg():
            try:
                res = cap.run_scan(companies, dry_run=False, limit=limit,
                                   force=force, company_ids=company_ids)
                logger.info("capture-scan async done: scanned=%d captured=%d",
                            res["scanned"], res["captured"])
            except Exception:
                logger.exception("capture-scan async run failed")
        threading.Thread(target=_bg, daemon=True, name="cb-capture-scan").start()
        return jsonify({"status": "dispatched", "scope_count": len(companies),
                        "force": force, "company_ids": list(company_ids or [])}), 202

    summary = cap.run_scan(companies, dry_run=dry_run, limit=limit,
                           force=force, company_ids=company_ids)
    return jsonify(summary)


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
