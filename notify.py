"""Telegram notifications (doc 02). Outbound messages + the phone-approval UI."""
import logging

import requests

import config

log = logging.getLogger(__name__)

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def _post(method: str, payload: dict) -> dict | None:
    """POST one Telegram Bot API call; return the parsed result or None."""
    try:
        r = requests.post(f"{_API}/{method}", json=payload, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        log.exception("telegram %s failed", method)
        return None


def _keyboard(buttons):
    """buttons: list of rows, each row a list of (label, callback_data) tuples."""
    return {"inline_keyboard": [
        [{"text": label, "callback_data": data} for (label, data) in row]
        for row in buttons
    ]}


def send(text: str, markdown: bool = True, buttons=None) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.warning("telegram not configured; message dropped: %s", text[:80])
        return False
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text}
    if markdown:
        payload["parse_mode"] = "Markdown"
    if buttons:
        payload["reply_markup"] = _keyboard(buttons)
    if _post("sendMessage", payload) is None and markdown:
        # markdown parse failures are common; retry once as plain text
        payload.pop("parse_mode", None)
        return _post("sendMessage", payload) is not None
    return True


def answer_callback(callback_id: str, text: str = "") -> None:
    """Clear the loading spinner on the tapped button (+ optional toast)."""
    _post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def edit_message(chat_id, message_id, text: str, markdown: bool = False) -> None:
    """Replace a message's text and drop its buttons (post-decision receipt)."""
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if markdown:
        payload["parse_mode"] = "Markdown"
    if _post("editMessageText", payload) is None and markdown:
        payload.pop("parse_mode", None)
        _post("editMessageText", payload)


# ---------------------------------------------------------------- message builders

def interested_lead_alert(lead, reply_summary: str) -> str:
    return (
        f"🔔 *Interested lead — {lead['business_name']}*\n\n"
        f"They said:\n\"{reply_summary}\"\n\n"
        f"Sample: {lead['sample_url']}\n"
        f"Lead #: {lead['id']}   |   Their email: {lead['email']}\n\n"
        f"Reply /handoff {lead['id']} when you've taken over,\n"
        f"or /build {lead['id']} to authorize the full build."
    )


def approval_preview(lead, subject: str, body: str) -> str:
    """The email exactly as it will go out, for a phone-side approval tap."""
    return (
        f"✉️ *Ready to send — {lead['business_name']}*\n"
        f"To: {lead['email']}\n"
        f"Sample: {lead['sample_url']}\n\n"
        f"*Subject:* {subject}\n\n"
        f"{body}\n\n"
        f"— Lead #{lead['id']}. Tap below (a CAN-SPAM footer is added on send)."
    )


def approval_buttons(lead_id: int):
    return [[
        ("✅ Send", f"send:{lead_id}"),
        ("✏️ Revise", f"revise:{lead_id}"),
        ("❌ Skip", f"skip:{lead_id}"),
    ]]
