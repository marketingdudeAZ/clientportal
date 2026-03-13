"""Phase 7: AM email notifications."""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, PORTAL_BASE_URL

logger = logging.getLogger(__name__)


def notify_am(
    deal_id: str,
    company_id: str,
    uuid: str,
    selections: dict,
    totals: dict,
) -> None:
    """Send email notification to AM when a configurator submission creates a Deal."""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning("SMTP not configured — skipping AM notification")
        return

    hubspot_deal_url = f"https://app.hubspot.com/contacts/deals/{deal_id}"
    portal_url = f"{PORTAL_BASE_URL}?uuid={uuid}"

    # Build selections summary
    lines = []
    for channel, sel in selections.items():
        tier = sel.get("tier", "Variable")
        monthly = sel.get("monthly", 0)
        setup = sel.get("setup", 0)
        line = f"  - {channel.replace('_', ' ').title()}: {tier} (${monthly:,}/mo"
        if setup > 0:
            line += f" + ${setup:,} setup"
        line += ")"
        lines.append(line)

    selections_text = "\n".join(lines)

    subject = f"New Configurator Submission — Deal #{deal_id}"

    body = f"""A client has submitted their budget configurator selections.

Deal: {hubspot_deal_url}
Portal: {portal_url}

Selections:
{selections_text}

Monthly Total: ${totals.get('monthly', 0):,}
Setup Fees: ${totals.get('setup', 0):,}
Monthly Change: ${totals.get('delta', 0):,}

A Quote has been auto-generated and sent to the client. You can review and modify the Deal in HubSpot before the client signs.
"""

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = SMTP_USER  # AM receives at the configured email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("AM notification sent for deal %s", deal_id)
    except Exception as e:
        logger.error("Failed to send AM notification: %s", e)


def notify_am_reminder(deal_id: str, company_id: str, uuid: str) -> None:
    """Send reminder if AM hasn't reviewed Paid Media recs within 48 hours."""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning("SMTP not configured — skipping reminder")
        return

    subject = f"Reminder: Paid Media Review Pending — Property {uuid}"
    body = f"""This is a reminder that Paid Media recommendations for property {uuid} have not been reviewed.

Please review at: {PORTAL_BASE_URL.replace('client-portal', 'am-review')}?uuid={uuid}

Recommendations will remain hidden from the client configurator until explicitly approved.
"""

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = SMTP_USER
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("AM reminder sent for property %s", uuid)
    except Exception as e:
        logger.error("Failed to send AM reminder: %s", e)
