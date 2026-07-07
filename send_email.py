"""Stage 3 — Gmail send with pacing (email-agent-instructions.md).

Hard rules enforced in code: DAILY_SEND_CAP, 60-120s jittered spacing,
recipient-local 9am-5pm window, suppression list, verified addresses only,
plain text, CAN-SPAM footer, never double-send (idempotent by gmail_message_id).
"""
import base64
import logging
import random
import time
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import config
import db
import write_email

log = logging.getLogger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    import os

    creds = None
    if os.path.exists(config.GMAIL_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(
            config.GMAIL_TOKEN_PATH, GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GMAIL_OAUTH_CREDENTIALS, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(config.GMAIL_TOKEN_PATH, "w") as fh:
            fh.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def canspam_footer(sample_url: str) -> str:
    return (
        f"\n\n--\n{config.SENDER_NAME}\n{config.BUSINESS_MAILING_ADDRESS}\n"
        f"Reply STOP to opt out."
    )


def in_send_window(tz_name: str) -> bool:
    try:
        local = datetime.now(ZoneInfo(tz_name or "America/Chicago"))
    except Exception:
        local = datetime.now(ZoneInfo("America/Chicago"))
    return local.weekday() < 5 and 9 <= local.hour < 17


def _send(service, to: str, subject: str, body: str,
          thread_id: str | None = None) -> dict:
    msg = MIMEText(body, "plain")
    msg["To"] = to
    msg["From"] = f"{config.SENDER_NAME} <{config.SENDER_EMAIL}>"
    msg["Reply-To"] = config.SENDER_EMAIL
    msg["Subject"] = subject
    payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if thread_id:
        payload["threadId"] = thread_id
    return service.users().messages().send(userId="me", body=payload).execute()


def _preflight() -> bool:
    if not (config.SENDER_EMAIL and config.SENDER_NAME):
        log.error("SENDER_EMAIL / SENDER_NAME not set — email track not live yet")
        return False
    if not config.BUSINESS_MAILING_ADDRESS:
        log.error("BUSINESS_MAILING_ADDRESS not set — CAN-SPAM footer required")
        return False
    return True


def send_batch(limit: int | None = None, dry_run: bool = False) -> dict:
    """The send_emails job (doc 04): cold email SAMPLE_BUILT leads."""
    stats = {"sent": 0, "skipped_window": 0, "skipped_suppressed": 0,
             "no_copy": 0, "cap_left": 0}
    if not _preflight():
        return stats
    with db.connect() as conn:
        cap_left = config.DAILY_SEND_CAP - db.sent_today(conn)
        stats["cap_left"] = max(cap_left, 0)
        if cap_left <= 0:
            log.info("daily cap reached; nothing sent")
            return stats
        leads = [
            l for l in db.get_leads_by_status(conn, "SAMPLE_BUILT")
            if l["email_status"] == "found" and l["email"]
            and l["emailed_at"] is None and l["gmail_message_id"] is None
        ]
        if not leads:
            return stats
        service = None if dry_run else gmail_service()
        batch = leads[: min(cap_left, limit or cap_left)]
        for lead in batch:
            if db.is_suppressed(conn, lead["email"]):
                stats["skipped_suppressed"] += 1
                continue
            if not in_send_window(lead["timezone"]):
                stats["skipped_window"] += 1
                continue

            subject, body = lead["email_subject"], lead["email_body"]
            if not subject or not body:
                copy = write_email.write_cold_email(conn, lead)
                if copy is None:
                    stats["no_copy"] += 1
                    continue
                subject, body = copy
                db.update_lead(conn, lead["id"], email_subject=subject,
                               email_body=body)
                conn.commit()

            full_body = body + canspam_footer(lead["sample_url"])
            if dry_run:
                log.info("[dry-run] would send to %s: %s", lead["email"], subject)
                stats["sent"] += 1
                continue
            try:
                resp = _send(service, lead["email"], subject, full_body)
            except Exception:
                log.exception("send failed for lead %s; leaving for next cycle",
                              lead["id"])
                continue
            db.transition(conn, lead["id"], "EMAILED", "cold email sent",
                          emailed_at=db.now(),
                          gmail_thread_id=resp.get("threadId"),
                          gmail_message_id=resp.get("id"))
            db.record_send(conn, lead["id"], "cold")
            conn.commit()
            stats["sent"] += 1
            if stats["sent"] < len(batch):
                time.sleep(random.uniform(60, 120))  # the account's survival
    log.info("send_batch done: %s", stats)
    return stats


def send_followups(dry_run: bool = False) -> dict:
    """The daily followups job: ONE fresh follow-up after ~5-7 quiet days."""
    stats = {"sent": 0}
    if not _preflight():
        return stats
    with db.connect() as conn:
        cap_left = config.DAILY_SEND_CAP - db.sent_today(conn)
        if cap_left <= 0:
            return stats
        cutoff_days = config.FOLLOWUP_AFTER_DAYS
        rows = conn.execute(
            "SELECT * FROM leads WHERE status = 'EMAILED' AND followups_sent < 1"
            " AND reply_text IS NULL AND emailed_at IS NOT NULL"
            " AND julianday('now') - julianday(emailed_at) >= ?", (cutoff_days,),
        ).fetchall()
        service = None if dry_run else (gmail_service() if rows else None)
        for lead in rows[:cap_left]:
            if db.is_suppressed(conn, lead["email"]) or not in_send_window(lead["timezone"]):
                continue
            copy = write_email.write_followup(conn, lead)
            if copy is None:
                continue
            subject, body = copy
            full_body = body + canspam_footer(lead["sample_url"])
            if dry_run:
                log.info("[dry-run] would follow up %s: %s", lead["email"], subject)
                stats["sent"] += 1
                continue
            try:
                _send(service, lead["email"], subject, full_body,
                      thread_id=lead["gmail_thread_id"])
            except Exception:
                log.exception("followup failed for lead %s", lead["id"])
                continue
            db.update_lead(conn, lead["id"], followups_sent=1)
            db.record_send(conn, lead["id"], "followup")
            conn.commit()
            stats["sent"] += 1
            time.sleep(random.uniform(60, 120))
    log.info("send_followups done: %s", stats)
    return stats


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    dry = "--dry-run" in sys.argv
    if "followups" in sys.argv:
        print(send_followups(dry_run=dry))
    else:
        print(send_batch(dry_run=dry))
