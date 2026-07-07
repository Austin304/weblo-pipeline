"""SQLite state layer (doc 04). One DB file, WAL mode, shared by all jobs."""
import json
import logging
import sqlite3
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)

FREE_MAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com",
    "icloud.com", "live.com", "msn.com", "comcast.net", "att.net",
    "sbcglobal.net", "verizon.net", "me.com", "protonmail.com", "proton.me",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  business_name     TEXT NOT NULL,
  category          TEXT,
  phone             TEXT,
  address           TEXT,
  timezone          TEXT,
  existing_website  TEXT,
  rating            REAL,
  review_count      INTEGER,
  email             TEXT,
  email_status      TEXT DEFAULT 'unverified',
  email_verified    INTEGER DEFAULT 0,
  qualify_status    TEXT,
  qualify_reason    TEXT,
  source_profile    TEXT,
  sample_url        TEXT,
  sample_slug       TEXT,
  sample_built_at   TEXT,
  image_source      TEXT,
  email_subject     TEXT,
  email_body        TEXT,
  gmail_thread_id   TEXT,
  gmail_message_id  TEXT,
  emailed_at        TEXT,
  followups_sent    INTEGER DEFAULT 0,
  reply_text        TEXT,
  reply_class       TEXT,
  replied_at        TEXT,
  notified_at       TEXT,
  authorized_at     TEXT,
  delivered_at      TEXT,
  status            TEXT NOT NULL DEFAULT 'FOUND',
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  notes             TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_phone  ON leads(phone);
CREATE INDEX        IF NOT EXISTS idx_leads_email  ON leads(email);
CREATE INDEX        IF NOT EXISTS idx_leads_status ON leads(status);

CREATE TABLE IF NOT EXISTS suppressed (
  email      TEXT PRIMARY KEY,
  reason     TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS sample_visits (
  id          INTEGER PRIMARY KEY,
  lead_id     INTEGER NOT NULL REFERENCES leads(id),
  visited_at  TEXT NOT NULL,
  source      TEXT,
  user_agent  TEXT,
  ip          TEXT,
  referrer    TEXT
);
CREATE INDEX IF NOT EXISTS idx_visits_lead ON sample_visits(lead_id);

CREATE TABLE IF NOT EXISTS spend_ledger (
  id          INTEGER PRIMARY KEY,
  service     TEXT NOT NULL,
  operation   TEXT,
  cost_usd    REAL NOT NULL,
  lead_id     INTEGER,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spend_service ON spend_ledger(service, created_at);

CREATE TABLE IF NOT EXISTS transitions (
  id         INTEGER PRIMARY KEY,
  lead_id    INTEGER NOT NULL,
  from_status TEXT,
  to_status  TEXT,
  detail     TEXT,
  created_at TEXT NOT NULL
);

-- one-time alert dedup for budget notifications (doc 08: one alert per envelope per month)
CREATE TABLE IF NOT EXISTS alerts_sent (
  key        TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);

-- inbox messages already handled by watch_replies (resume-safe across restarts)
CREATE TABLE IF NOT EXISTS seen_messages (
  gmail_id   TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);

-- misc daemon state (telegram update offset, etc.)
CREATE TABLE IF NOT EXISTS kv (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- one row per message actually sent (cold OR followup). The daily send cap is
-- counted from HERE so follow-ups and cold sends share one budget — a warming
-- domain's per-day volume must include every message, not just cold sends.
CREATE TABLE IF NOT EXISTS send_log (
  id        INTEGER PRIMARY KEY,
  lead_id   INTEGER NOT NULL,
  kind      TEXT NOT NULL,   -- cold | followup
  sent_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_send_log_day ON send_log(sent_at);

-- Manual sample grades (calibration, first ~30 samples). One row per grading
-- pass; the seven factors A-G each 1-5 (see GRADING-RUBRIC.md), overall is a
-- gut /10 kept separate on purpose. sample_slug pins WHICH build was graded,
-- since a rebuild mints a new slug.
CREATE TABLE IF NOT EXISTS grades (
  id           INTEGER PRIMARY KEY,
  lead_id      INTEGER NOT NULL,
  hero         INTEGER,   -- A: first 3 seconds
  design       INTEGER,   -- B: premium vs basic craft
  layout       INTEGER,   -- C: layout & variety
  imagery      INTEGER,   -- D: imagery use
  copy         INTEGER,   -- E: copy / voice
  trust        INTEGER,   -- F: accuracy / owner's eye
  beats        INTEGER,   -- G: beats their current site
  overall      INTEGER,   -- gut /10
  notes        TEXT,      -- "CHANGE FIRST: ... | KEEP: ..."
  sample_slug  TEXT,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_grades_lead ON grades(lead_id);
"""

GRADE_FACTORS = ("hero", "design", "layout", "imagery", "copy", "trust", "beats")

VALID_TRANSITIONS = {
    "FOUND": {"QUALIFIED", "SKIP", "PHONE_ONLY"},
    "QUALIFIED": {"SAMPLE_BUILT", "SAMPLE_FAILED"},
    "SAMPLE_BUILT": {"EMAILED", "PHONE_ONLY"},
    "EMAILED": {"REPLIED_INTERESTED", "CLOSED_LOST", "OPTED_OUT", "BOUNCED"},
    "REPLIED_INTERESTED": {"HANDED_OFF"},
    "HANDED_OFF": {"AUTHORIZED"},
    "AUTHORIZED": {"BUILDING"},
    "BUILDING": {"DELIVERED"},
}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def _email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if email and "@" in email else ""


def is_duplicate(conn, phone: str, email: str) -> bool:
    """Dedup rule from find-leads brief / doc 04."""
    if phone and conn.execute(
        "SELECT 1 FROM leads WHERE phone = ?", (phone,)
    ).fetchone():
        return True
    if email:
        if conn.execute(
            "SELECT 1 FROM leads WHERE lower(email) = lower(?)", (email,)
        ).fetchone():
            return True
        dom = _email_domain(email)
        if dom and dom not in FREE_MAIL_DOMAINS and conn.execute(
            "SELECT 1 FROM leads WHERE lower(email) LIKE ?", (f"%@{dom}",)
        ).fetchone():
            return True
    return False


def add_lead(conn, **fields) -> int | None:
    """Insert a lead; returns new id, or None if it deduped away."""
    if is_duplicate(conn, fields.get("phone"), fields.get("email")):
        return None
    fields.setdefault("status", "FOUND")
    fields["created_at"] = fields["updated_at"] = now()
    if isinstance(fields.get("source_profile"), (dict, list)):
        fields["source_profile"] = json.dumps(fields["source_profile"])
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    cur = conn.execute(
        f"INSERT INTO leads ({cols}) VALUES ({marks})", list(fields.values())
    )
    return cur.lastrowid


def get_lead(conn, lead_id: int):
    return conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()


def get_lead_by_slug(conn, slug: str):
    return conn.execute(
        "SELECT * FROM leads WHERE sample_slug = ?", (slug,)
    ).fetchone()


def get_leads_by_status(conn, status: str, limit: int | None = None):
    q = "SELECT * FROM leads WHERE status = ? ORDER BY (qualify_status = 'NO_SITE') DESC, review_count DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, (status,)).fetchall()


def update_lead(conn, lead_id: int, **fields):
    if isinstance(fields.get("source_profile"), (dict, list)):
        fields["source_profile"] = json.dumps(fields["source_profile"])
    fields["updated_at"] = now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE leads SET {sets} WHERE id = ?", [*fields.values(), lead_id]
    )


def transition(conn, lead_id: int, to_status: str, detail: str = "", **extra):
    row = get_lead(conn, lead_id)
    if row is None:
        raise ValueError(f"lead {lead_id} not found")
    frm = row["status"]
    allowed = VALID_TRANSITIONS.get(frm, set())
    if to_status not in allowed and to_status != frm:
        log.warning("lead %s: invalid transition %s -> %s (%s); skipping",
                    lead_id, frm, to_status, detail)
        return False
    update_lead(conn, lead_id, status=to_status, **extra)
    conn.execute(
        "INSERT INTO transitions (lead_id, from_status, to_status, detail, created_at)"
        " VALUES (?,?,?,?,?)", (lead_id, frm, to_status, detail, now()),
    )
    log.info("lead %s: %s -> %s %s", lead_id, frm, to_status, detail)
    return True


def is_suppressed(conn, email: str) -> bool:
    if not email:
        return False
    return conn.execute(
        "SELECT 1 FROM suppressed WHERE lower(email) = lower(?)", (email,)
    ).fetchone() is not None


def suppress(conn, email: str, reason: str):
    conn.execute(
        "INSERT OR IGNORE INTO suppressed (email, reason, created_at) VALUES (?,?,?)",
        (email, reason, now()),
    )


def log_visit(conn, lead_id: int, source: str, user_agent: str, ip: str,
              referrer: str) -> bool:
    """Insert a visit row; returns True if this is the lead's FIRST visit."""
    first = conn.execute(
        "SELECT 1 FROM sample_visits WHERE lead_id = ? LIMIT 1", (lead_id,)
    ).fetchone() is None
    conn.execute(
        "INSERT INTO sample_visits (lead_id, visited_at, source, user_agent, ip, referrer)"
        " VALUES (?,?,?,?,?,?)",
        (lead_id, now(), source, user_agent, ip, referrer),
    )
    return first


def counts_by_status(conn) -> dict:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM leads GROUP BY status ORDER BY n DESC"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def record_send(conn, lead_id: int, kind: str):
    """Log one actually-sent message for daily-cap accounting. `kind` is
    'cold' or 'followup' — both count against the same daily budget."""
    conn.execute(
        "INSERT INTO send_log (lead_id, kind, sent_at) VALUES (?,?,?)",
        (lead_id, kind, now()),
    )


def record_grade(conn, lead_id: int, factors: list[int], overall: int,
                 notes: str, sample_slug: str | None) -> int:
    """Store one manual grading pass. `factors` is the seven A-G scores in
    order (see GRADE_FACTORS / GRADING-RUBRIC.md)."""
    cols = ", ".join(GRADE_FACTORS)
    marks = ", ".join("?" * len(GRADE_FACTORS))
    cur = conn.execute(
        f"INSERT INTO grades (lead_id, {cols}, overall, notes, sample_slug, created_at)"
        f" VALUES (?, {marks}, ?, ?, ?, ?)",
        (lead_id, *factors, overall, notes, sample_slug, now()),
    )
    return cur.lastrowid


def grade_averages(conn) -> dict:
    """Mean of each factor + overall across the LATEST grade per lead, so
    re-grading one lead while iterating never skews the calibration numbers.
    n = number of DISTINCT graded leads (the real calibration count)."""
    cols = ", ".join(f"AVG({f}) AS {f}" for f in (*GRADE_FACTORS, "overall"))
    row = conn.execute(
        f"SELECT COUNT(*) AS n, {cols} FROM ("
        "  SELECT g.* FROM grades g"
        "  JOIN (SELECT lead_id, MAX(id) AS mid FROM grades GROUP BY lead_id) last"
        "    ON g.id = last.mid)"
    ).fetchone()
    return dict(row) if row else {}


def sent_today(conn) -> int:
    """Every message (cold + followup) sent in the current UTC day. This is the
    number the daily send cap is enforced against, so follow-ups and cold sends
    can never together exceed DAILY_SEND_CAP from a warming domain."""
    today = now()[:10]
    return conn.execute(
        "SELECT COUNT(*) FROM send_log WHERE sent_at LIKE ?", (f"{today}%",)
    ).fetchone()[0]


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {config.DB_PATH}")
