"""Stage 3 — Gmail send with pacing (email-agent-instructions.md).

Hard rules enforced in code: DAILY_SEND_CAP, 60-120s jittered spacing,
recipient-local 9am-5pm window, suppression list, verified addresses only,
plain text, CAN-SPAM footer, never double-send (idempotent by gmail_message_id).
"""
import base64
import logging
import random
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import config
import db
import notify
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


def _tz(tz_name: str | None):
    """A ZoneInfo for the lead, or None if this box has no tz database at all.
    The old two-step fallback re-raised when `tzdata` was missing (Windows ships
    no system zoneinfo), and an exception here kills the whole send job — the
    exact silent-stall failure the heartbeat exists to catch. Degrade instead."""
    for key in (tz_name, "America/Chicago"):
        if key:
            try:
                return ZoneInfo(key)
            except Exception:
                continue
    return None  # datetime.now(None) == naive system local time


def in_send_window(tz_name: str) -> bool:
    local = datetime.now(_tz(tz_name))
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


def deliver(conn, service, lead) -> bool:
    """Send a lead's already-stored cold-email copy for real, then transition
    to EMAILED and log the send for daily-cap accounting. Shared by the manual
    phone-approval path (watch_replies) so an approved send goes out exactly like
    an automatic one. Returns True on success; raises on a Gmail API failure so
    the caller can surface it. Idempotent guard: refuses if already emailed."""
    if lead["gmail_message_id"] or lead["emailed_at"]:
        log.info("lead %s already emailed; deliver() skipped", lead["id"])
        return False
    subject, body = lead["email_subject"], lead["email_body"]
    if not subject or not body:
        return False
    full_body = body + canspam_footer(lead["sample_url"])
    resp = _send(service, lead["email"], subject, full_body)
    db.transition(conn, lead["id"], "EMAILED", "cold email sent (phone-approved)",
                  emailed_at=db.now(),
                  gmail_thread_id=resp.get("threadId"),
                  gmail_message_id=resp.get("id"))
    db.record_send(conn, lead["id"], "cold")
    conn.commit()
    return True


def next_window_text(tz_name: str) -> str:
    """Human phrasing of when a lead's 9-5 send window next opens — so the
    approval receipt can say WHEN it will go instead of just 'later'."""
    local = datetime.now(_tz(tz_name))
    if local.weekday() < 5 and local.hour < 9:
        return "9am their time"
    days = 1
    while (local + timedelta(days=days)).weekday() >= 5:
        days += 1
    return "9am their time tomorrow" if days == 1 else "9am Monday their time"


def flush_approved(limit: int | None = None) -> dict:
    """Send leads the operator already approved (tapped ✅ outside the recipient's
    business hours) now that their window is open. Called by the `send` cron, so
    an evening tap becomes a next-morning delivery with no further action."""
    stats = {"sent": 0, "waiting": 0, "cap_left": 0, "suppressed": 0}
    with db.connect() as conn:
        approved = db.get_leads_by_status(conn, "APPROVED")
        if not approved:
            return stats
        cap_left = config.DAILY_SEND_CAP - db.sent_today(conn)
        stats["cap_left"] = max(cap_left, 0)
        if cap_left <= 0:
            stats["waiting"] = len(approved)
            return stats
        due = [l for l in approved if in_send_window(l["timezone"])]
        stats["waiting"] = len(approved) - len(due)
        if not due:
            return stats
        service = gmail_service()
        for lead in due[: min(cap_left, limit or cap_left)]:
            if db.is_suppressed(conn, lead["email"]):
                db.transition(conn, lead["id"], "HELD", "suppressed before delayed send")
                conn.commit()
                stats["suppressed"] += 1
                continue
            try:
                if deliver(conn, service, lead):
                    stats["sent"] += 1
                    notify.send(
                        f"📤 Sent to *{lead['business_name']}* — the one you "
                        f"approved outside their business hours just went out.")
            except Exception:
                log.exception("flush_approved: send failed for lead %s", lead["id"])
                continue
            time.sleep(random.uniform(60, 120))  # same pacing as a normal batch
    log.info("flush_approved done: %s", stats)
    return stats


def queue_batch(limit: int | None = None) -> dict:
    """Approval-mode counterpart to send_batch (config.SEND_REQUIRE_APPROVAL):
    draft cold-email copy for eligible SAMPLE_BUILT leads, park them at
    PENDING_APPROVAL, and push each to Telegram with Send/Revise/Skip buttons.
    NOTHING is emailed here — the operator releases each send by tapping ✅ Send
    on their phone (handled in watch_replies). The daily cap counts today's sends
    PLUS anything already awaiting a tap, so the phone never gets flooded past the
    day's budget."""
    stats = {"queued": 0, "already_pending": 0, "approved_waiting": 0,
             "suppressed": 0, "no_copy": 0, "cap_left": 0}
    if not _preflight():
        return stats
    with db.connect() as conn:
        pending = len(db.get_leads_by_status(conn, "PENDING_APPROVAL"))
        stats["already_pending"] = pending
        # APPROVED leads are committed sends still waiting on a business-hours
        # slot — they must count against today's room or the cap overshoots.
        approved = len(db.get_leads_by_status(conn, "APPROVED"))
        stats["approved_waiting"] = approved
        room = config.DAILY_SEND_CAP - db.sent_today(conn) - pending - approved
        stats["cap_left"] = max(room, 0)
        if room <= 0:
            log.info("queue_batch: no room (cap %s, sent %s, pending %s)",
                     config.DAILY_SEND_CAP, db.sent_today(conn), pending)
            return stats
        leads = [
            l for l in db.get_leads_by_status(conn, "SAMPLE_BUILT")
            if l["email_status"] == "found" and l["email"]
            and l["emailed_at"] is None and l["gmail_message_id"] is None
        ]
        cap = min(room, limit or room)
        for lead in leads:
            if stats["queued"] >= cap:
                break
            if db.is_suppressed(conn, lead["email"]):
                stats["suppressed"] += 1
                continue
            copy = write_email.write_cold_email(conn, lead)
            if copy is None:
                stats["no_copy"] += 1
                continue
            subject, body = copy
            db.update_lead(conn, lead["id"], email_subject=subject, email_body=body)
            db.transition(conn, lead["id"], "PENDING_APPROVAL",
                          "queued for phone approval")
            conn.commit()
            notify.send(notify.approval_preview(lead, subject, body),
                        buttons=notify.approval_buttons(lead["id"]))
            stats["queued"] += 1
    log.info("queue_batch done: %s", stats)
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


def draft_emails(limit: int = 30, regenerate: bool = False) -> dict:
    """Calibration helper: generate + store cold-email copy for SAMPLE_BUILT
    leads WITHOUT sending, and print each for grading alongside its sample.

    Needs no Gmail setup. In production only email_status='found' leads are
    actually sent; drafts are generated for all built samples so the copy
    generator itself can be graded. The CAN-SPAM footer + opt-out line is
    appended at real send time (see canspam_footer), not shown here."""
    stats = {"generated": 0, "reused": 0, "failed": 0}
    with db.connect() as conn:
        leads = [l for l in db.get_leads_by_status(conn, "SAMPLE_BUILT")
                 if l["sample_url"]]
        for lead in leads[:limit]:
            subject, body = lead["email_subject"], lead["email_body"]
            if regenerate or not (subject and body):
                copy = write_email.write_cold_email(conn, lead)
                if copy is None:
                    stats["failed"] += 1
                    continue
                subject, body = copy
                db.update_lead(conn, lead["id"], email_subject=subject,
                               email_body=body)
                conn.commit()
                stats["generated"] += 1
            else:
                stats["reused"] += 1
            flag = "EMAIL" if lead["email_status"] == "found" else "postcard-only"
            print("\n" + "=" * 70)
            print(f"#{lead['id']}  {lead['business_name']}   [{flag}]")
            print(f"sample: {lead['sample_url']}")
            print(f"words:  {len(body.split())}")
            print("-" * 70)
            print(f"Subject: {subject}\n")
            print(body)
        print("\n" + "=" * 70)
        print(f"draft summary: {stats}")
        print("(a physical-address + 'Reply STOP to opt out' footer is added at send time)")
    return stats


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    dry = "--dry-run" in sys.argv
    if "followups" in sys.argv:
        print(send_followups(dry_run=dry))
    elif "draft" in sys.argv:
        n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 30)
        draft_emails(limit=n, regenerate="--regenerate" in sys.argv)
    else:
        print(send_batch(dry_run=dry))
