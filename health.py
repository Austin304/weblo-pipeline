"""Pipeline self-check + the daily heartbeat that makes silence impossible.

The failure this exists to prevent: in the last week of July the funnel ran dry
and the `find`/`build` cron jobs were switched off, so nothing reached the
operator's phone for a week. From the phone, a healthy-but-idle pipeline, a dead
cron, and an expired Gmail token all look identical — no messages. The operator
is at work on weekdays and cannot go look.

So: every job records that it ran (`db.kv` key `last_run:<job>`), `check()`
inspects the whole system, and `heartbeat()` pushes one Telegram message a day
that ALWAYS arrives — including, loudly, when the answer is "nothing happened."
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import requests

import config
import costs
import db
import notify

log = logging.getLogger(__name__)

# A job quiet for longer than this is treated as broken, not idle.
STALE_AFTER_HOURS = {"find": 24 * 8, "build": 6, "send": 2, "requalify": 24 * 8}
# Fewer emailable leads than this in reserve and the funnel needs a top-up.
FUNNEL_LOW_WATER = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def record_run(job: str):
    """Called by run.py after a job finishes. The heartbeat reads these to tell
    'cron is running and there's simply nothing to do' apart from 'cron is dead'."""
    try:
        with db.connect() as conn:
            db.kv_set(conn, f"last_run:{job}", db.now())
            conn.commit()
    except Exception:  # never let bookkeeping break a real job
        log.exception("record_run(%s) failed", job)


def _age_hours(ts: str | None) -> float | None:
    when = _parse(ts)
    return None if when is None else (_now() - when).total_seconds() / 3600


def gmail_ok() -> tuple[bool, str]:
    """Live-test the Gmail credential. This is the check that catches the
    External+Testing OAuth trap, where the refresh token silently dies after 7
    days and every send fails from then on."""
    if not (config.SENDER_EMAIL and config.SENDER_NAME):
        return False, "SENDER_EMAIL/SENDER_NAME not set"
    try:
        import send_email
        svc = send_email.gmail_service()
        addr = svc.users().getProfile(userId="me").execute().get("emailAddress")
        if addr and addr.lower() != config.SENDER_EMAIL.lower():
            return False, f"authorized as {addr}, expected {config.SENDER_EMAIL}"
        return True, addr or "ok"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


def samples_site_ok() -> tuple[bool, str]:
    url = config.SAMPLE_BASE_URL or f"http://localhost:{config.SAMPLES_PORT}"
    try:
        r = requests.get(f"{url}/healthz", timeout=10)
        return r.status_code == 200, f"HTTP {r.status_code}"
    except requests.RequestException as e:
        return False, type(e).__name__


def check(deep: bool = True) -> dict:
    """Collect every health fact. `deep=False` skips the network probes."""
    out = {"alerts": [], "funnel": {}, "jobs": {}, "spend": {}, "services": {}}
    with db.connect() as conn:
        counts = db.counts_by_status(conn)
        out["funnel"] = {
            "QUALIFIED": counts.get("QUALIFIED", 0),
            "SAMPLE_BUILT": counts.get("SAMPLE_BUILT", 0),
            "PENDING_APPROVAL": counts.get("PENDING_APPROVAL", 0),
            "APPROVED": counts.get("APPROVED", 0),
            "HELD": counts.get("HELD", 0),
            "EMAILED": counts.get("EMAILED", 0),
            "REPLIED_INTERESTED": counts.get("REPLIED_INTERESTED", 0),
        }
        # the number that actually matters: built samples with a real address,
        # i.e. how many approval taps are available to serve up
        out["funnel"]["ready_to_send"] = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='SAMPLE_BUILT'"
            " AND email_status='found' AND email IS NOT NULL"
            " AND emailed_at IS NULL"
        ).fetchone()[0]
        out["funnel"]["sent_today"] = db.sent_today(conn)
        out["spend"] = {
            "mtd": costs.month_to_date(conn),
            "budget": config.PIPELINE_MONTHLY_BUDGET_USD,
        }
        for job in STALE_AFTER_HOURS:
            ts = db.kv_get(conn, f"last_run:{job}")
            out["jobs"][job] = {"last_run": ts, "age_hours": _age_hours(ts)}

    # --- turn facts into alerts -------------------------------------------
    for job, limit in STALE_AFTER_HOURS.items():
        age = out["jobs"][job]["age_hours"]
        if age is None:
            out["alerts"].append(f"job `{job}` has NEVER recorded a run")
        elif age > limit:
            out["alerts"].append(
                f"job `{job}` last ran {age / 24:.1f} days ago (expected every "
                f"{limit}h) — cron may be off")

    pipeline_total = (out["funnel"]["ready_to_send"]
                      + out["funnel"]["QUALIFIED"]
                      + out["funnel"]["PENDING_APPROVAL"])
    if pipeline_total < FUNNEL_LOW_WATER:
        out["alerts"].append(
            f"funnel nearly empty — only {pipeline_total} lead(s) left across "
            f"qualified/built/pending. Run `requalify` or `find`.")

    if out["spend"]["mtd"] > out["spend"]["budget"] * (config.BUDGET_WARN_PCT / 100):
        out["alerts"].append(
            f"spend at ${out['spend']['mtd']:.2f} of "
            f"${out['spend']['budget']:.0f} budget")

    if deep:
        ok, detail = gmail_ok()
        out["services"]["gmail"] = {"ok": ok, "detail": detail}
        if not ok:
            out["alerts"].append(f"GMAIL BROKEN — sends will fail: {detail}")
        ok, detail = samples_site_ok()
        out["services"]["samples"] = {"ok": ok, "detail": detail}
        if not ok:
            out["alerts"].append(
                f"samples site unreachable ({detail}) — every sample link in a "
                f"sent email is dead right now")
    return out


def summary_text(h: dict) -> str:
    f, s = h["funnel"], h["spend"]
    lines = []
    if h["alerts"]:
        lines.append("⚠️ *Weblo — needs attention*\n")
        lines += [f"• {a}" for a in h["alerts"]]
        lines.append("")
    else:
        lines.append("✅ *Weblo — all systems normal*\n")

    approved = f" · {f['APPROVED']} approved, sending at 9am" if f.get("APPROVED") else ""
    lines.append(
        f"*Funnel:* {f['ready_to_send']} ready to send · "
        f"{f['PENDING_APPROVAL']} awaiting your tap{approved} · "
        f"{f['QUALIFIED']} queued to build")
    lines.append(
        f"*Today:* {f['sent_today']}/{config.DAILY_SEND_CAP} sent · "
        f"{f['EMAILED']} emailed all-time · {f['REPLIED_INTERESTED']} interested")
    lines.append(f"*Spend:* ${s['mtd']:.2f} / ${s['budget']:.0f} this month")

    svc = h.get("services", {})
    if svc:
        bits = [f"{'✅' if v['ok'] else '❌'} {k}" for k, v in svc.items()]
        lines.append("*Services:* " + "  ".join(bits))

    if f["PENDING_APPROVAL"]:
        lines.append("\nSend /pending to re-push anything waiting on approval.")
    return "\n".join(lines)


def heartbeat(force: bool = False) -> dict:
    """Push one summary to Telegram. Deduped to once per UTC day unless forced,
    so a cron misfire can't spam the phone."""
    h = check(deep=True)
    today = db.now()[:10]
    with db.connect() as conn:
        if not force and db.kv_get(conn, "heartbeat:last_date") == today:
            log.info("heartbeat already sent today; skipping")
            return h
        if notify.send(summary_text(h)):
            db.kv_set(conn, "heartbeat:last_date", today)
            conn.commit()
    return h


def print_report(h: dict):
    print(json.dumps(h, indent=2, default=str))
    print()
    # the Windows console is cp1252 and dies on the emoji that Telegram wants
    text = summary_text(h)
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))
