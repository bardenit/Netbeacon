"""Alert notifications — webhook and email — triggered by network events."""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
import urllib.request
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from app.config import get_config

logger = logging.getLogger(__name__)

# In-memory rate limit: (device_id, event_type) -> last sent time
# Safe for single-process deployments.
_rate_limit: dict[tuple, datetime] = {}

EVENT_EMOJI = {
    "device_up":         "✅",
    "device_down":       "🔴",
    "device_degraded":   "🟡",
    "port_up":           "↑",
    "port_down":         "↓",
    "mac_appeared":      "⊕",
    "mac_disappeared":   "⊖",
}


def _should_send(device_id: Optional[int], event_type: str, rate_minutes: int) -> bool:
    key = (device_id, event_type)
    last = _rate_limit.get(key)
    if last and (datetime.utcnow() - last) < timedelta(minutes=rate_minutes):
        return False
    _rate_limit[key] = datetime.utcnow()
    # Prune stale entries (older than 24h)
    cutoff = datetime.utcnow() - timedelta(hours=24)
    for k in [k for k, v in _rate_limit.items() if v < cutoff]:
        del _rate_limit[k]
    return True


def send_alert(
    device_id: Optional[int],
    device_name: str,
    event_type: str,
    detail: str,
    force: bool = False,
):
    """Fire configured alerts for an event. Runs synchronously (call from thread pool)."""
    cfg = get_config()
    alert_cfg = cfg.get("alerts", {})

    if not alert_cfg.get("enabled", False):
        return

    on_events = alert_cfg.get("on_events", ["device_down", "device_up", "device_degraded"])
    if event_type not in on_events and not force:
        return

    rate_minutes = int(alert_cfg.get("rate_limit_minutes", 60))
    if not force and not _should_send(device_id, event_type, rate_minutes):
        logger.debug("Alert rate-limited: %s / %s", device_name, event_type)
        return

    emoji = EVENT_EMOJI.get(event_type, "●")
    title = f"NetBeacon: {emoji} {device_name} — {event_type.replace('_', ' ').title()}"
    body = (
        f"{detail}\n"
        f"Device: {device_name}\n"
        f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )

    webhook_url = alert_cfg.get("webhook_url")
    if webhook_url:
        _send_webhook(webhook_url, title, body, event_type, device_name, detail)

    email_cfg = alert_cfg.get("email", {})
    if email_cfg.get("smtp_host"):
        _send_email(email_cfg, title, body)


def _send_webhook(url: str, title: str, body: str, event_type: str, device_name: str, detail: str):
    if not url.startswith(("http://", "https://")):
        logger.error("Webhook URL must use http:// or https:// scheme — skipping")
        return
    try:
        emoji = EVENT_EMOJI.get(event_type, "●")
        if "hooks.slack.com" in url:
            # Slack-formatted payload
            payload = {
                "text": f"{emoji} *{title}*",
                "attachments": [{
                    "color": "#e74c3c" if "down" in event_type else "#2ecc71",
                    "text": body,
                    "footer": "NetBeacon",
                }],
            }
        else:
            # Generic JSON webhook (Teams, Discord, custom)
            payload = {
                "title": title,
                "body": body,
                "event_type": event_type,
                "device": device_name,
                "detail": detail,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Webhook alert sent: %s → HTTP %s", event_type, resp.status)
    except Exception as e:
        logger.error("Webhook send failed for %s: %s", event_type, e)


def _send_email(email_cfg: dict, subject: str, body: str):
    try:
        smtp_host = email_cfg["smtp_host"]
        smtp_port = int(email_cfg.get("smtp_port", 587))
        username = email_cfg.get("smtp_user")
        password = email_cfg.get("smtp_password")
        from_addr = email_cfg.get("from", username or "netbeacon@localhost")
        to_addrs = email_cfg.get("to", [])
        use_tls = email_cfg.get("tls", True)

        if not to_addrs:
            logger.warning("Email alert configured but no 'to' addresses set")
            return

        if isinstance(to_addrs, str):
            to_addrs = [to_addrs]

        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)
        msg.attach(MIMEText(body, "plain"))

        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            if use_tls:
                server.starttls(context=context)
            if username and password:
                server.login(username, password)
            server.sendmail(from_addr, to_addrs, msg.as_string())

        logger.info("Email alert sent: %s → %s", subject, to_addrs)
    except Exception as e:
        logger.error("Email send failed: %s", e)


def send_digest(subject_prefix: str = "Daily"):
    """Send a digest email with network health summary."""
    cfg = get_config()
    alert_cfg = cfg.get("alerts", {})
    if not alert_cfg.get("enabled", False):
        return
    email_cfg = alert_cfg.get("email", {})
    if not email_cfg.get("smtp_host") or not email_cfg.get("to"):
        logger.info("Digest: email not configured, skipping")
        return

    from app.database import SessionLocal
    from app.models import Device, Port, MacEntry, ArpEntry, Event

    now = datetime.utcnow()
    yesterday = now - timedelta(hours=24)

    with SessionLocal() as db:
        devices = db.query(Device).all()
        total_sw = len(devices)
        online_sw = sum(1 for d in devices if d.poll_status == "ok")
        error_sw = sum(1 for d in devices if d.poll_status == "error")

        ports = db.query(Port).all()
        up_ports = sum(1 for p in ports if p.oper_status == 1)
        flapping = sum(1 for p in ports if (p.flap_count or 0) > 0)
        error_ports_count = sum(1 for p in ports if (p.rx_errors or 0) + (p.tx_errors or 0) > 0)

        new_macs = db.query(MacEntry).filter(MacEntry.first_seen >= yesterday).count()

        recent_events = (
            db.query(Event)
            .filter(Event.created_at >= yesterday)
            .order_by(Event.created_at.desc())
            .limit(20)
            .all()
        )

    events_html = "".join(
        f"<tr><td style='padding:4px 8px;color:#94a3b8'>{e.created_at.strftime('%H:%M')}</td>"
        f"<td style='padding:4px 8px'>{e.event_type.replace('_',' ').title()}</td>"
        f"<td style='padding:4px 8px;color:#e2e8f0'>{e.detail or ''}</td></tr>"
        for e in recent_events
    )

    html = f"""
    <html><body style='background:#0f172a;color:#e2e8f0;font-family:monospace;padding:24px'>
    <h2 style='color:#818cf8'>NetBeacon — {subject_prefix} Digest</h2>
    <p style='color:#94a3b8'>{now.strftime('%Y-%m-%d %H:%M UTC')}</p>
    <table style='border-collapse:collapse;margin-bottom:24px'>
      <tr><td style='padding:6px 16px 6px 0;color:#94a3b8'>Switches online</td><td style='color:#10b981;font-weight:bold'>{online_sw}/{total_sw}</td></tr>
      <tr><td style='padding:6px 16px 6px 0;color:#94a3b8'>Switches in error</td><td style='color:{"#ef4444" if error_sw else "#10b981"};font-weight:bold'>{error_sw}</td></tr>
      <tr><td style='padding:6px 16px 6px 0;color:#94a3b8'>Active ports</td><td style='color:#e2e8f0'>{up_ports}/{len(ports)}</td></tr>
      <tr><td style='padding:6px 16px 6px 0;color:#94a3b8'>Flapping ports</td><td style='color:{"#f59e0b" if flapping else "#10b981"};font-weight:bold'>{flapping}</td></tr>
      <tr><td style='padding:6px 16px 6px 0;color:#94a3b8'>Ports with errors</td><td style='color:{"#f59e0b" if error_ports_count else "#10b981"}'>{error_ports_count}</td></tr>
      <tr><td style='padding:6px 16px 6px 0;color:#94a3b8'>New devices (24h)</td><td style='color:#818cf8'>{new_macs}</td></tr>
    </table>
    {"<h3 style='color:#818cf8'>Recent Events</h3><table style='border-collapse:collapse'>" + events_html + "</table>" if recent_events else ""}
    <p style='color:#475569;font-size:11px;margin-top:32px'>Sent by NetBeacon</p>
    </body></html>
    """

    subject = f"NetBeacon {subject_prefix} Digest — {online_sw}/{total_sw} switches OK"
    try:
        smtp_host = email_cfg["smtp_host"]
        smtp_port = int(email_cfg.get("smtp_port", 587))
        username = email_cfg.get("smtp_user")
        password = email_cfg.get("smtp_password")
        from_addr = email_cfg.get("from", username or "netbeacon@localhost")
        to_addrs = email_cfg.get("to", [])
        if isinstance(to_addrs, str):
            to_addrs = [to_addrs]
        use_tls = email_cfg.get("tls", True)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)
        msg.attach(MIMEText(html, "html"))

        import ssl as _ssl
        context = _ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            if use_tls:
                server.starttls(context=context)
            if username and password:
                server.login(username, password)
            server.sendmail(from_addr, to_addrs, msg.as_string())
        logger.info("Digest email sent: %s → %s", subject, to_addrs)
    except Exception as e:
        logger.error("Digest email failed: %s", e)


def get_alert_config_summary() -> dict:
    """Returns alert config without secrets (for UI display)."""
    cfg = get_config()
    alert_cfg = cfg.get("alerts", {})
    webhook_url = alert_cfg.get("webhook_url", "")
    email_cfg = alert_cfg.get("email", {})
    return {
        "enabled": alert_cfg.get("enabled", False),
        "on_events": alert_cfg.get("on_events", ["device_down", "device_up", "device_degraded"]),
        "rate_limit_minutes": alert_cfg.get("rate_limit_minutes", 60),
        "webhook_configured": bool(webhook_url),
        "webhook_type": (
            "slack" if "hooks.slack.com" in webhook_url
            else "teams" if "office.com" in webhook_url
            else "generic" if webhook_url
            else None
        ),
        "email_configured": bool(email_cfg.get("smtp_host")),
        "email_to": email_cfg.get("to", []) if email_cfg.get("smtp_host") else [],
    }
