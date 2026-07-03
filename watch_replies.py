"""Stage 4 daemon (doc 02): watch Gmail for replies, classify with Claude,
notify the operator on Telegram, and handle operator commands.

Run as a systemd service on the VM:  python watch_replies.py
"""
import base64
import json
import logging
import re
import time

import requests

import config
import costs
import db
import find_leads
import notify
import send_email

log = logging.getLogger(__name__)

POLL_SECONDS = 75
EST_CLASSIFY_USD = 0.01
TG_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


# ---------------------------------------------------------------- gmail side

def _body_text(payload) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _body_text(part)
        if text:
            return text
    return ""


def _header(msg, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def classify_reply(conn, lead, text: str) -> dict:
    if costs.check(conn, "claude", EST_CLASSIFY_USD) == "block":
        return {"class": "QUESTION", "summary": text[:200],
                "suggested_reply_hint": ""}  # fail toward notifying a human
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=config.MODEL_CLASSIFIER, max_tokens=300,
        system="You classify replies to a cold email that pitched a free sample website.",
        messages=[{"role": "user", "content": f"""Classify this reply into exactly one of:
INTERESTED (wants more / asks price or next steps / positive)
QUESTION (engaged but asking something first)
NOT_INTERESTED (polite no / not now)
OPT_OUT (stop / unsubscribe / remove me / hostile)
AUTO_REPLY (out-of-office / autoresponder / delivery notice)

Reply from {lead['business_name']}:
\"\"\"{text[:2000]}\"\"\"

Return STRICT JSON only: {{"class": "...", "summary": "one line", "suggested_reply_hint": "..."}}"""}],
    )
    costs.record(conn, "claude", "classify_reply",
                 costs.claude_cost(config.MODEL_CLASSIFIER,
                                   resp.usage.input_tokens,
                                   resp.usage.output_tokens),
                 lead_id=lead["id"], tokens_in=resp.usage.input_tokens,
                 tokens_out=resp.usage.output_tokens)
    try:
        return json.loads(re.search(r"\{.*\}", resp.content[0].text, re.S).group(0))
    except (AttributeError, json.JSONDecodeError):
        return {"class": "QUESTION", "summary": text[:200],
                "suggested_reply_hint": ""}


def handle_reply(conn, lead, text: str, from_addr: str):
    result = classify_reply(conn, lead, text)
    cls = result.get("class", "QUESTION")
    summary = result.get("summary", text[:200])
    log.info("lead %s reply classified %s: %s", lead["id"], cls, summary)

    if cls in ("INTERESTED", "QUESTION"):
        db.transition(conn, lead["id"], "REPLIED_INTERESTED", cls,
                      reply_text=text[:4000], reply_class=cls,
                      replied_at=db.now())
        notify.send(notify.interested_lead_alert(lead, summary))
        db.update_lead(conn, lead["id"], notified_at=db.now())
    elif cls == "NOT_INTERESTED":
        db.transition(conn, lead["id"], "CLOSED_LOST", "reply: not interested",
                      reply_text=text[:4000], reply_class=cls,
                      replied_at=db.now())
    elif cls == "OPT_OUT":
        db.suppress(conn, lead["email"], "OPT_OUT")
        db.transition(conn, lead["id"], "OPTED_OUT", "reply: opt out",
                      reply_text=text[:4000], reply_class=cls,
                      replied_at=db.now())
    # AUTO_REPLY: leave EMAILED
    conn.commit()


def handle_bounce(conn, text: str):
    """Bounce guard: suppress the bounced address, flag the lead."""
    for addr in set(re.findall(find_leads.EMAIL_RE, text)):
        addr = addr.lower()
        if addr == (config.SENDER_EMAIL or "").lower():
            continue
        row = conn.execute(
            "SELECT * FROM leads WHERE lower(email) = ? AND status = 'EMAILED'",
            (addr,)).fetchone()
        if row:
            db.suppress(conn, addr, "BOUNCE")
            db.transition(conn, row["id"], "BOUNCED", "delivery failure")
            conn.commit()
            notify.send(f"↩️ Bounce: {row['business_name']} ({addr}) suppressed.")


def poll_gmail(service):
    with db.connect() as conn:
        resp = service.users().messages().list(
            userId="me", q="in:inbox newer_than:3d", maxResults=50).execute()
        for stub in resp.get("messages", []):
            gid = stub["id"]
            if conn.execute("SELECT 1 FROM seen_messages WHERE gmail_id=?",
                            (gid,)).fetchone():
                continue
            msg = service.users().messages().get(
                userId="me", id=gid, format="full").execute()
            conn.execute(
                "INSERT OR IGNORE INTO seen_messages (gmail_id, created_at)"
                " VALUES (?,?)", (gid, db.now()))
            conn.commit()

            from_hdr = _header(msg, "From").lower()
            if config.SENDER_EMAIL and config.SENDER_EMAIL.lower() in from_hdr:
                continue  # our own sends
            text = _body_text(msg.get("payload", {})) or msg.get("snippet", "")
            if "mailer-daemon" in from_hdr or "postmaster" in from_hdr:
                handle_bounce(conn, text)
                continue
            lead = conn.execute(
                "SELECT * FROM leads WHERE gmail_thread_id = ?",
                (msg.get("threadId"),)).fetchone()
            if lead is None:
                continue
            if lead["status"] in ("REPLIED_INTERESTED", "HANDED_OFF",
                                  "AUTHORIZED", "BUILDING", "DELIVERED"):
                continue  # human conversation now — automation stays out
            handle_reply(conn, lead, text, from_hdr)


# ---------------------------------------------------------------- telegram side

def _kv_get(conn, key: str, default: str = "0") -> str:
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _kv_set(conn, key: str, value: str):
    conn.execute("INSERT INTO kv (key, value) VALUES (?,?)"
                 " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))
    conn.commit()


def cmd_status(conn) -> str:
    counts = db.counts_by_status(conn)
    lines = [f"{status}: {n}" for status, n in counts.items()]
    spent = costs.month_to_date(conn)
    lines.append(f"\nMTD spend: ${spent:.2f} / ${config.PIPELINE_MONTHLY_BUDGET_USD:.0f}")
    lines.append(f"Sent today: {db.sent_today(conn)} / {config.DAILY_SEND_CAP}")
    return "📊 Funnel\n" + "\n".join(lines)


def cmd_views(conn, lead_id: int | None) -> str:
    if lead_id:
        rows = conn.execute(
            "SELECT v.*, l.business_name FROM sample_visits v"
            " JOIN leads l ON l.id = v.lead_id WHERE v.lead_id = ?"
            " ORDER BY v.visited_at DESC LIMIT 20", (lead_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT v.lead_id, l.business_name, COUNT(*) AS n,"
            " MAX(v.visited_at) AS last, MAX(v.source) AS source"
            " FROM sample_visits v JOIN leads l ON l.id = v.lead_id"
            " GROUP BY v.lead_id ORDER BY last DESC LIMIT 15").fetchall()
    if not rows:
        return "No sample visits logged yet."
    if lead_id:
        return "\n".join(f"{r['visited_at']} [{r['source']}]" for r in rows)
    return "👀 Recent sample visits\n" + "\n".join(
        f"#{r['lead_id']} {r['business_name']} — {r['n']} visit(s),"
        f" last {r['last']} [{r['source']}]" for r in rows)


def handle_command(text: str):
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    with db.connect() as conn:
        if cmd == "/status":
            notify.send(cmd_status(conn), markdown=False)
        elif cmd == "/views":
            lead_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            notify.send(cmd_views(conn, lead_id), markdown=False)
        elif cmd == "/handoff" and len(parts) > 1 and parts[1].isdigit():
            if db.transition(conn, int(parts[1]), "HANDED_OFF", "operator handoff"):
                conn.commit()
                notify.send(f"✅ Lead {parts[1]} handed off — automation will not email it again.")
            else:
                notify.send(f"Could not hand off lead {parts[1]} (check its status with /status).")
        elif cmd == "/build" and len(parts) > 1 and parts[1].isdigit():
            lead = db.get_lead(conn, int(parts[1]))
            if lead and lead["status"] == "REPLIED_INTERESTED":
                db.transition(conn, lead["id"], "HANDED_OFF", "implicit handoff before build")
            if db.transition(conn, int(parts[1]), "AUTHORIZED", "operator /build",
                             authorized_at=db.now()):
                conn.commit()
                notify.send(f"🏗️ Lead {parts[1]} AUTHORIZED. Full-build stage (doc 03) "
                            f"is not automated yet — build it manually and run"
                            f" /delivered when done.")
            else:
                notify.send(f"Could not authorize lead {parts[1]}.")
        elif cmd == "/stop" and len(parts) > 1:
            target = parts[1]
            if target.isdigit():
                lead = db.get_lead(conn, int(target))
                if lead and lead["email"]:
                    db.suppress(conn, lead["email"], "MANUAL")
                    conn.commit()
                    notify.send(f"🚫 {lead['email']} suppressed.")
            elif "@" in target:
                db.suppress(conn, target, "MANUAL")
                conn.commit()
                notify.send(f"🚫 {target} suppressed.")
        elif cmd == "/find":
            m = re.match(r"/find\s+(.+?)\s+in\s+(.+?)(?:\s+(\d+))?$", text.strip(),
                         re.I)
            if not m:
                notify.send("Usage: /find <niche> in <location> [count]")
                return
            niche, location = m.group(1), m.group(2)
            count = int(m.group(3)) if m.group(3) else config.CAMPAIGN_TARGET_COUNT
            notify.send(f"🔎 Searching {niche} in {location} (target {count})…")
            try:
                stats = find_leads.top_up(location, niche, count)
                notify.send(f"Done: {stats}", markdown=False)
            except Exception as e:
                notify.send(f"find failed: {e}", markdown=False)
        else:
            notify.send("Commands: /status /views [id] /handoff <id> /build <id>"
                        " /find <niche> in <location> [count] /stop <id|email>")


def poll_telegram():
    with db.connect() as conn:
        offset = int(_kv_get(conn, "tg_offset", "0"))
    try:
        r = requests.get(f"{TG_API}/getUpdates",
                         params={"offset": offset + 1, "timeout": 25}, timeout=35)
        updates = r.json().get("result", [])
    except requests.RequestException:
        return
    for upd in updates:
        with db.connect() as conn:
            _kv_set(conn, "tg_offset", str(upd["update_id"]))
        msg = upd.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            continue  # only the configured operator is honored
        text = msg.get("text", "")
        if text.startswith("/"):
            try:
                handle_command(text)
            except Exception:
                log.exception("command failed: %s", text)


# ---------------------------------------------------------------- main loop

def main():
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    service = None
    last_gmail = 0.0
    log.info("watch_replies daemon starting")
    while True:
        try:
            poll_telegram()  # long-polls ~25s; doubles as the loop's sleep
            if time.time() - last_gmail >= POLL_SECONDS:
                if service is None and config.SENDER_EMAIL:
                    try:
                        service = send_email.gmail_service()
                    except Exception:
                        log.exception("gmail auth unavailable; replies not polled")
                if service is not None:
                    poll_gmail(service)
                last_gmail = time.time()
        except KeyboardInterrupt:
            break
        except Exception:
            log.exception("watch loop error; backing off")
            time.sleep(30)


if __name__ == "__main__":
    main()
