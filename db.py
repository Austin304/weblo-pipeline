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
"""

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


def sent_today(conn) -> int:
    """Emails sent in the current UTC day (cap accounting)."""
    today = now()[:10]
    return conn.execute(
        "SELECT COUNT(*) FROM leads WHERE emailed_at LIKE ?", (f"{today}%",)
    ).fetchone()[0]


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {config.DB_PATH}")
