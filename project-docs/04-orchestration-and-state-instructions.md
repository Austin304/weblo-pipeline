# 04 — Orchestration & State (The Backbone)

## Objective
Define the single source of truth for every lead and the scheduler that moves leads through the pipeline. Every other component reads from and writes to this state. This runs on the always-on GCP `e2-micro` VM (doc 00).

---

## The state store: SQLite

One file (`DB_PATH`, default `leads.db`). One main table, `leads`.

### Schema
```sql
CREATE TABLE IF NOT EXISTS leads (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  -- discovery (stage 1)
  business_name     TEXT NOT NULL,
  category          TEXT,
  phone             TEXT,
  address           TEXT,
  timezone          TEXT,        -- IANA tz (e.g. America/Chicago), derived from address; used for send windowing
  existing_website  TEXT,
  rating            REAL,
  review_count      INTEGER,
  email             TEXT,
  email_status      TEXT DEFAULT 'unverified',  -- CANONICAL: found | not_found | unverified. Set by stage 1. 'found' → email track; 'not_found' → postcard track (doc 06).
  email_verified    INTEGER DEFAULT 0,          -- derived convenience flag: 1 only when email_status='found'. Never set independently of email_status.
  qualify_status    TEXT,        -- NO_SITE | OUTDATED
  qualify_reason    TEXT,
  source_profile    TEXT,        -- JSON blob of gathered material for sample
  -- sample (stage 2)
  sample_url        TEXT,
  sample_slug       TEXT,        -- unique URL path slug under SAMPLE_BASE_URL; used for visit tracking (email + postcard tracks)
  sample_built_at   TEXT,
  -- email (stage 3)
  email_subject     TEXT,
  email_body        TEXT,
  gmail_thread_id   TEXT,
  gmail_message_id  TEXT,
  emailed_at        TEXT,
  followups_sent    INTEGER DEFAULT 0,
  -- reply / handoff (stage 4-5)
  reply_text        TEXT,
  reply_class       TEXT,        -- INTERESTED | QUESTION | NOT_INTERESTED | OPT_OUT | AUTO_REPLY
  replied_at        TEXT,
  notified_at       TEXT,
  -- build (stage 6)
  authorized_at     TEXT,
  delivered_at      TEXT,
  -- lifecycle
  status            TEXT NOT NULL DEFAULT 'FOUND',
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  notes             TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_phone  ON leads(phone);
CREATE INDEX        IF NOT EXISTS idx_leads_email  ON leads(email);
CREATE INDEX        IF NOT EXISTS idx_leads_status ON leads(status);

-- permanent suppression list (opt-outs, hard bounces, spam complaints)
CREATE TABLE IF NOT EXISTS suppressed (
  email      TEXT PRIMARY KEY,
  reason     TEXT,         -- OPT_OUT | BOUNCE | COMPLAINT | MANUAL
  created_at TEXT
);

-- one row per sample view, written by serve_samples.py on every GET /<slug>
CREATE TABLE IF NOT EXISTS sample_visits (
  id          INTEGER PRIMARY KEY,
  lead_id     INTEGER NOT NULL REFERENCES leads(id),
  visited_at  TEXT NOT NULL,   -- UTC
  source      TEXT,            -- postcard | email | direct  (from ?utm_source on the URL)
  user_agent  TEXT,
  ip          TEXT,
  referrer    TEXT
);
CREATE INDEX IF NOT EXISTS idx_visits_lead ON sample_visits(lead_id);
```

`db.py` should expose helpers: `add_lead`, `get_leads_by_status`, `update_lead`, `is_suppressed`, `suppress`, `log_visit`. Open the DB in **WAL mode** (`PRAGMA journal_mode=WAL;`) so the scheduled jobs, the reply daemon, and the samples web app can read/write concurrently without locking each other out.

**Dedup on insert (per the leads brief):** skip a candidate if its **phone** matches an existing lead, OR its **full email address** matches, OR its **email domain** matches *and that domain is a custom business domain* (i.e. not a free-mail provider — gmail.com, yahoo.com, outlook.com, hotmail.com, aol.com, icloud.com, etc.). Never dedup on a free-mail domain alone — many distinct small businesses share `gmail.com`, and collapsing them would silently drop real leads.

---

## The status state machine

```
FOUND
  ├─(qualify, passed)→ QUALIFIED
  └─(qualify, failed)→ SKIP        (didn't pass filter)
QUALIFIED
  └─(build sample)→ SAMPLE_BUILT ────────→ SAMPLE_FAILED
SAMPLE_BUILT
  ├─(email found → send email)→ EMAILED ──→ BOUNCED
  ├─(no email found → postcard track, doc 06)→ stays SAMPLE_BUILT; mail progress tracked in `mail_pieces`
  └─(no email AND address undeliverable/missing)→ PHONE_ONLY   (terminal; surfaced in /status for a manual call)
EMAILED
  ├─(reply: interested/question)→ REPLIED_INTERESTED
  ├─(reply: not interested)→ CLOSED_LOST
  ├─(reply: opt out)→ OPTED_OUT          (also add to `suppressed`)
  └─(no reply, after N days)→ stays EMAILED; optional follow-up
REPLIED_INTERESTED
  └─(operator takes over; notified)→ HANDED_OFF
HANDED_OFF
  └─(operator authorizes via Telegram)→ AUTHORIZED
AUTHORIZED
  └─(full build)→ BUILDING → DELIVERED
```

Rules:
- A status only moves forward through valid transitions. Log every transition.
- **`SAMPLE_BUILT` leads split by channel:** `email_status='found'` → email track; `email_status='not_found'` → postcard track (doc 06). Postcard progress lives in the `mail_pieces` table, not in `leads.status`.
- **No lead stays `unverified`.** `email_status` defaults to `unverified`; stage 1 must resolve it to `found`/`not_found`, retrying verification once on a transient failure (see find-leads brief). As a backstop, if a lead reaches `SAMPLE_BUILT` still `unverified`, the `send_emails` job retries verification once and, failing that, sets it `not_found` so it falls to the postcard track rather than stranding.
- **The two outreach tracks are mutually exclusive.** A lead is emailed *or* mailed, never both. An emailed lead that never replies (even after its one follow-up) is **not** re-routed to the postcard track — it simply closes out; likewise a postcard lead is never emailed.
- **`PHONE_ONLY` is terminal** — set when a lead can be neither emailed (`not_found`) nor mailed (no usable/deliverable address), but has a phone. No automated outreach; it appears in `/status` for the operator to optionally call or walk in.
- Once a lead is `REPLIED_INTERESTED` or beyond, **stop all automated emailing to it** — it's now a human conversation.
- Never email an address in `suppressed`.

---

## The scheduler (`run.py`)

Runs on the always-on GCP VM. Two kinds of work: **scheduled batch jobs** (`cron`) and **two long-running processes** (the reply watcher + the samples web app), each kept alive as a `systemd` service (auto-restart on crash).

### Scheduled jobs
| Job | Cadence | What it does |
|---|---|---|
| `find_leads` | weekly auto-top-up **+ ad-hoc** | Top up the funnel toward the target count of `QUALIFIED` leads. Inputs (location, niche, count) come from **standing campaign config** in `.env` (the weekly auto-top-up), and can also be triggered ad-hoc by the operator's `/find` Telegram command (doc 02) for a one-off area/niche. See find-leads-brief.md. |
| `build_samples` | hourly | For each `QUALIFIED` lead, build + deploy a sample (doc 01) → `SAMPLE_BUILT`. |
| `send_emails` | every few minutes, business hours only | Send to `SAMPLE_BUILT` leads **that have an email** (`email_status='found'` — never select null-email leads; those go to the postcard track), **respecting all pacing rules** in `email-agent-instructions.md`: ≤ `DAILY_SEND_CAP`/day, 60–120s apart with random jitter, **9am–5pm in the lead's `timezone` column** (set at qualify time), ramp volume weekly. Skip suppressed addresses. → `EMAILED`. |
| `followups` | daily | **One** polite follow-up to `EMAILED` leads with no reply after ~5–7 days (`followups_sent < 1`). Must be a *fresh* email body, not a resend — see the follow-up section in `email-writing-instructions.md`. After one follow-up, no further automated email. |
| `mail_job` | 1–2× daily | **Postcard track (doc 06).** Select `SAMPLE_BUILT` leads with `email_status='not_found'`, verify addresses (Lob), render a postcard in test mode, and send the batch to the operator on Telegram for approval. On approval, mail via Lob (live) and record in `mail_pieces`. Enforces `MAIL_BATCH_CAP` + `MAIL_MONTHLY_BUDGET_USD`. Runs from day one, in parallel with email warm-up. |

### The long-running processes (two)
1. **`watch_replies.py`** — the reply daemon (see doc 02). Polls Gmail, classifies replies, updates state, fires Telegram notifications, and handles the operator's Telegram commands (`/handoff`, `/build`, `/find`, `/status`, `/views`, `/stop`).
2. **`serve_samples.py`** — the samples web app (see doc 01). A small Flask/FastAPI server listening on `SAMPLES_PORT`, exposed publicly at `SAMPLE_BASE_URL` by the Cloudflare Tunnel (`cloudflared`). Both must be kept alive on the VM as `systemd` services (auto-restart on crash, same as the reply daemon).

### Sample visit tracking (`serve_samples.py`)
Every `GET /<sample_slug>` does two things before returning the HTML:
- **Log the visit:** insert a `sample_visits` row (`lead_id`, `visited_at` UTC, `source` from `?utm_source`, user-agent, ip, referrer). This is the **primary** detection method and the source of truth — Cloudflare analytics is sampled/delayed and is never used to trigger anything.
- **Notify, by channel:**
  - **Postcard leads** (`email_status='not_found'`): on the lead's **first** visit, fire the same Telegram interest notification as the email handoff (doc 06, Step F). Physical mail has no inbox reply, so a visit *is* the interest signal.
  - **Email leads** (`email_status='found'`): **do not** notify on a visit — their interest signal is an actual reply (doc 02). Visits are still logged and surfaced on demand via the `/views` Telegram command, so the operator can see who has looked at their sample and when.

Serving locally (rather than a static host) is deliberate: the same process that serves the page writes the visit, so there is no edge→local gap and notifications are real-time.

---

## Operational requirements
- **Budget enforcement (doc 08):** every job that makes a **paid** API call (Claude, Google Places, ZeroBounce, Lob) must call the spend gate `costs.check(service, est)` **before** spending. On `block` it pauses that job for the cycle and fires a one-time Telegram alert; it never crashes the daemons. Every paid call is logged to the `spend_ledger` table (schema in doc 08). This is what makes the monthly budgets in `.env` real rather than advisory.
- **Idempotency:** every job must be safe to re-run. Select by status; never double-send (check `emailed_at`/`gmail_message_id` before sending). Idempotency is also a cost control — a re-run must never re-pay for work already done.
- **Pacing is enforced here, not just documented.** The `send_emails` job must hard-stop at the daily cap and enforce spacing — this is the account's survival.
- **Retries with backoff** on API errors (Claude, Gmail, Cloudflare). Don't hammer.
- **Logging:** structured logs to `/logs` for every state transition, send, and error. The operator needs to be able to see what happened.
- **Timestamps in UTC** in the DB; convert to recipient-local only for send-time windowing.

---

## Config
All tunables come from `.env` (see doc 00): `DAILY_SEND_CAP`, `DB_PATH`, the **standing campaign config** (`CAMPAIGN_LOCATION` / `CAMPAIGN_NICHE` / `CAMPAIGN_TARGET_COUNT`) that drives the weekly auto-top-up, follow-up delay, etc. No hardcoded secrets.
