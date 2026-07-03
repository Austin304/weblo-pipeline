"""Telegram notifications (doc 02). One function; used by every job."""
import logging

import requests

import config

log = logging.getLogger(__name__)

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def send(text: str, markdown: bool = True) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.warning("telegram not configured; message dropped: %s", text[:80])
        return False
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text}
    if markdown:
        payload["parse_mode"] = "Markdown"
    try:
        r = requests.post(f"{_API}/sendMessage", json=payload, timeout=15)
        if r.status_code != 200 and markdown:
            # markdown parse failures are common; retry plain
            payload.pop("parse_mode")
            r = requests.post(f"{_API}/sendMessage", json=payload, timeout=15)
        r.raise_for_status()
        return True
    except requests.RequestException:
        log.exception("telegram send failed")
        return False


def interested_lead_alert(lead, reply_summary: str) -> str:
    return (
        f"🔔 *Interested lead — {lead['business_name']}*\n\n"
        f"They said:\n\"{reply_summary}\"\n\n"
        f"Sample: {lead['sample_url']}\n"
        f"Lead #: {lead['id']}   |   Their email: {lead['email']}\n\n"
        f"Reply /handoff {lead['id']} when you've taken over,\n"
        f"or /build {lead['id']} to authorize the full build."
    )
