# 08 — Cost Controls & Budget Guardrails (Don't Get a Surprise Bill)

## Objective
Make the pipeline **incapable of quietly running up a bill.** Two outreach/AI services are usage-based and uncapped by default — the **Claude API** (every sample build + email/postcard copy + reply classification) and the **Google Places API** (lead finding). A bug (a retry storm, a runaway `find→build` loop) could otherwise spend real money fast. This doc adds two independent layers of protection so the operator's exposure is bounded and visible.

> Read doc 00 (system overview) and doc 04 (orchestration) first. This doc owns cost control across **all** paid services; the Lob postcard budget (doc 06) plugs into the same ledger.

---

## The two layers (both required)

| Layer | What it is | Why it's needed |
|---|---|---|
| **1. Provider-side hard caps** | Spend/quota limits set **once** in each vendor's console (Anthropic, Google Cloud). | The external backstop. A code bug can't bypass it — the vendor itself refuses further calls. This is the real safety net. |
| **2. In-code spend ledger + self-pacing** | The pipeline logs every paid call's estimated cost to SQLite, checks month-to-date spend before each call, and **stops the job + alerts the operator on Telegram** before crossing a budget. | The graceful layer. The pipeline polices itself and tells the operator *why* it paused, instead of erroring out when a vendor cuts it off mid-run. |

---

## LAYER 1 — Provider-side hard caps (set these up first, once)

| Service | Hard cap to set | Where | Notes |
|---|---|---|---|
| **Anthropic (Claude)** | A **monthly spend limit** on the workspace, and a **dedicated API key** for this project (so its spend is isolated and visible). | Anthropic Console → workspace settings / limits + billing. | The dedicated key means a leak or bug is scoped to this project, not the whole account. |
| **Google Places** | A **per-day request quota** on the Places API (a *true* hard cap), **plus** a project **Budget alert** at a low threshold. | Google Cloud Console → APIs & Services → Quotas; Billing → Budgets & alerts. | GCP budget *alerts* only notify — they don't stop spend. The **API quota** is what actually caps it. Set both. |
| **ZeroBounce** (optional) | Buy **prepaid credits**, don't enable auto-recharge. | ZeroBounce dashboard. | Prepaid = a natural hard cap: when credits run out, verification falls back to the free MX/SMTP check (find-leads brief) — no surprise charge. Skippable entirely (leave `ZEROBOUNCE_API_KEY` blank). |
| **Lob** (postcards) | Governed in code by `MAIL_MONTHLY_BUDGET_USD` + per-batch operator approval + `test` mode (doc 06). | `.env` + Lob dashboard. | Already gated — nothing mails without an explicit operator APPROVE. The ledger here also tracks Lob spend so it counts toward the global cap. |
| **GCP VM** | A **$1 Budget alert** on the project (doc 07). | Google Cloud Billing. | The always-free `e2-micro` is $0; the $1 alert catches any accidental over-provision. |

These caps are the floor. Even if Layer 2 had a bug, the vendor stops at the console limit.

---

## LAYER 2 — In-code spend ledger + self-pacing

### Config additions (`.env`)
```
# ---------- COST GUARDRAILS (doc 08) ----------
PIPELINE_MONTHLY_BUDGET_USD=150     # GLOBAL hard ceiling across ALL paid APIs; pipeline self-pauses at 100%
CLAUDE_MONTHLY_BUDGET_USD=50        # Anthropic API (sample builds + email/postcard copy + reply classification)
PLACES_MONTHLY_BUDGET_USD=20        # Google Places (lead finding)
ZEROBOUNCE_MONTHLY_BUDGET_USD=10    # email verification (only consulted if ZEROBOUNCE_API_KEY is set)
BUDGET_WARN_PCT=80                  # fire a Telegram nudge when any envelope crosses this % of its cap
# MAIL_MONTHLY_BUDGET_USD (doc 06) is the Lob envelope and also rolls up into PIPELINE_MONTHLY_BUDGET_USD
```
All budgets are **calendar-month** envelopes. Start conservative (the defaults above) and raise once real spend is understood.

### DB schema addition (`spend_ledger`)
One row per billable API call. Lives in the same `leads.db` (doc 04), in WAL mode.
```sql
CREATE TABLE IF NOT EXISTS spend_ledger (
  id            INTEGER PRIMARY KEY,
  service       TEXT NOT NULL,     -- claude | places | zerobounce | lob
  operation     TEXT,              -- build_sample | write_email | classify_reply | find_leads | verify_email | postcard
  lead_id       INTEGER,           -- nullable; links spend to a lead when applicable
  cost_usd      REAL NOT NULL,     -- estimated USD for this call
  tokens_in     INTEGER,           -- Claude only
  tokens_out    INTEGER,           -- Claude only
  created_at    TEXT NOT NULL      -- UTC
);
CREATE INDEX IF NOT EXISTS idx_spend_service_time ON spend_ledger(service, created_at);
```

### Pricing constants (`costs.py`)
Claude cost is computed from each response's `usage` block. **Current per-1M-token prices (confirm against the live Anthropic pricing page before launch — these change):**

| Model (`.env` choice) | Input $/1M | Output $/1M | Used for |
|---|---|---|---|
| `claude-opus-4-8` | **$5.00** | **$25.00** | Sample HTML + email/postcard copy (quality) |
| `claude-haiku-4-5` | **$1.00** | **$5.00** | Reply classification (cheap/fast) |
| `claude-sonnet-4-6` | $3.00 | $15.00 | Alt classifier (if chosen over Haiku) |

Per-call Claude cost = `tokens_in/1e6 * in_rate + tokens_out/1e6 * out_rate` (add cache-read at ~0.1× and cache-write at ~1.25× of the input rate if prompt caching is used; read the `usage` fields). Places/ZeroBounce/Lob costs are per-call/per-piece flat rates from each vendor.

`costs.py` exposes:
- `estimate_claude(model, tokens_in, tokens_out) -> usd`
- `record(service, operation, cost_usd, lead_id=None, tokens_in=None, tokens_out=None)` — insert a `spend_ledger` row.
- `month_to_date(service=None) -> usd` — sum of `cost_usd` for the current calendar month (all services if `service=None`).
- `budget_for(service) -> usd` — read the matching `*_MONTHLY_BUDGET_USD` from `.env`.
- `check(service, est_usd) -> "ok" | "warn" | "block"` — the gate (below).

### The spend gate (called before every paid API call)
```
check(service, est_usd):
  global_spent  = month_to_date()                 # all services
  service_spent = month_to_date(service)
  if global_spent + est_usd  > PIPELINE_MONTHLY_BUDGET_USD: return "block"
  if service_spent + est_usd > budget_for(service): return "block"
  if service_spent + est_usd > budget_for(service) * BUDGET_WARN_PCT/100: return "warn"
  return "ok"
```
- **`block`** → the job **skips that call and stops the job for this cycle**, logs it, and fires a **one-time** Telegram alert: *"⚠️ Claude budget reached ($50/mo). Sample builds paused until next month or until you raise CLAUDE_MONTHLY_BUDGET_USD."* (One alert per envelope per month — don't spam.)
- **`warn`** → proceed with the call, but fire a Telegram nudge once when the envelope first crosses `BUDGET_WARN_PCT`.
- **`ok`** → proceed.

After each successful paid call, immediately `record(...)` the actual cost (from `usage` for Claude, flat rate otherwise). For Claude, estimate `est_usd` *before* the call from `max_tokens` (worst case) so a single huge call can't blow the cap; reconcile to the real cost after.

---

## Where the gate plugs into the scheduler (doc 04)
Each paid job consults `check()` before spending:

| Job (doc 04) | Paid call(s) it gates | On `block` |
|---|---|---|
| `find_leads` | Google Places (`places`) | Stop topping up the funnel this cycle; alert. |
| `build_samples` | Claude sample build (`claude`) | Stop building more samples this cycle; already-built leads are unaffected. |
| `send_emails` | Claude email copy (`claude`) + ZeroBounce backstop (`zerobounce`) | Stop generating/sending this cycle. |
| `followups` | Claude follow-up copy (`claude`) | Skip follow-ups this cycle. |
| `mail_job` | Claude postcard copy (`claude`) + Lob (`lob`, via `MAIL_MONTHLY_BUDGET_USD`) | Stop assembling batches; already gated by approval too (doc 06). |

A `block` **pauses spending, it does not crash the pipeline** — the daemons (`serve_samples.py`, `watch_replies.py`) keep running, sample visits and replies are still tracked, and the operator can raise a budget via `.env` (then restart the affected job) or simply wait for the month to roll over.

---

## Other built-in cost discipline (already in the design — keep it)
These bound *how often* expensive calls happen, so the dollar ledger rarely has to intervene:
- **Volume caps cap cost.** `find_leads` only tops up to `CAMPAIGN_TARGET_COUNT` (not unbounded); `build_samples` only builds `QUALIFIED` leads; `DAILY_SEND_CAP` bounds emails; `MAIL_BATCH_CAP` bounds postcards.
- **Cheap model for the cheap job.** Reply classification uses `claude-haiku-4-5` (or Sonnet), never Opus (doc 00).
- **Bounded retries.** Sample generation retries **max ~2** (doc 01); API retries use backoff with a hard ceiling (doc 04) — a failing vendor must **not** loop and burn tokens/quota.
- **Idempotency.** Every job checks state before acting (`emailed_at`, `gmail_message_id`, `mail_pieces`) so a re-run never double-spends (doc 04).
- **Bounded `max_tokens`.** Set sample generation `max_tokens` to a sane ceiling so one call can't produce (and bill for) a runaway output.

---

## Suggested files (extends the doc 00 repo layout)
```
/pipeline
  costs.py     # pricing constants + estimate/record/month_to_date/budget_for/check (this doc)
  # reuses: db.py (spend_ledger table), notify.py (budget alerts), config.py (.env), run.py (jobs call check())
```

---

## Done-when
- Provider-side caps are set: Anthropic monthly spend limit + dedicated key; Places per-day quota + GCP budget alert; ZeroBounce prepaid (or disabled); the $1 VM budget alert (doc 07).
- `spend_ledger` table exists; every paid call writes a row with its estimated cost.
- Every paid job calls `check()` before spending and honors `block` (pause + one-time Telegram alert) and `warn` (nudge).
- `PIPELINE_MONTHLY_BUDGET_USD` is the global ceiling; per-service envelopes (`CLAUDE_/PLACES_/ZEROBOUNCE_/MAIL_`) sum within it.
- Raising a budget in `.env` and restarting the job resumes spending; nothing crashes when a budget is hit.
- A test confirms a simulated over-budget state blocks the call and fires exactly one alert.
```
