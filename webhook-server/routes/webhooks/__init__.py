"""Webhook receivers — inbound from HubSpot, ClickUp, AptIQ, etc.

Each file is one source. Each handler:
  1. Validates signature (HMAC against the source's secret)
  2. Parses the payload
  3. Writes one or more loop_event rows (ADR 0010)
  4. Optionally fires downstream actions (ADR 0014 Pattern C)
  5. Returns 200 fast — work is queued, not done synchronously
"""

from .hubspot import hubspot_webhook_bp


def register_webhook_blueprints(app):
    app.register_blueprint(hubspot_webhook_bp)
