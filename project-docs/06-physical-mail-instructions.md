# 06 — Physical Mail Outreach (Track 2 — runs DURING warm-up)

## Objective
Reach qualifying prospects **by physical postcard** while the email domain is warming (~2 weeks, doc 05). Physical mail is a completely separate reputation system from email — it does **not** touch the warming domain and is immune to the 2-week clock — so it is the one outreach channel that can go live on day one.

This track reuses the existing pipeline (leads in `leads.db`, samples served by `serve_samples.py` behind the Cloudflare Tunnel, Telegram for operator contact). It only adds a sending channel. It does **not** replace the email track — it complements it.

> Read doc 00 (system overview) and doc 05 (warm-up) first. This doc owns the physical-mail stage only.

---

## Decisions already made (do not re-litigate)

| Area | Decision |
|---|---|
| Provider | **Lob** (`lob` Python SDK / REST API). Print-and-mail + address verification in one API. |
| Autonomy | **Approve each batch.** The agent assembles a batch and sends it to the operator on Telegram for one-tap approval **before anything mails**. No piece is sent without approval. |
| Mail piece | **Standard 4×6 postcard**, double-sided, AI/template-rendered HTML. Cheapest per piece, no envelope, QR + sample URL fit fine. |
| Targeting | **Only leads with no findable email.** Physical mail reaches prospects the email track cannot touch at all — no overlap, no double-spend. |
| State | Same **SQLite** `leads.db`. New `mail_pieces` table (schema below). |
| Operator contact | Same **Telegram bot** as doc 02 (approval + interest notifications). |
| Response signal | A **QR code / unique URL per lead** → a scan/visit is the "interest" signal (mail has no "reply" like email). On a visit, notify the operator exactly like the email track's interest handoff. |

---

## Where this fits in the pipeline

```
[1] FIND LEADS      → adds email_status to each lead (found / not_found)
[2] BUILD SAMPLE    → deploys sample to a UNIQUE per-lead URL (needed for QR tracking)
[T2] PHYSICAL MAIL  → THIS DOC: select no-email leads → render postcard → operator approves → Lob mails it
[5] OPERATOR HANDOFF→ on QR scan / sample visit, notify operator (same as email track)
```

Physical mail slots in parallel with the email track and shares everything upstream. The only hard prerequisite it adds is that **stage 1 must record whether an email was found**, and **stage 2 must deploy each sample to a unique, trackable URL**.

---

## Prerequisites this track depends on (verify before building)

1. **`leads.db` records email discovery outcome per lead.** Add/confirm an `email_status` column on the leads table: `found`, `not_found`, or `unverified` (after the ZeroBounce / MX-SMTP attempt in doc 00). The mail track selects only `not_found`.
2. **Each lead has a postal address.** Google Places returns `formatted_address`; ensure it's stored as discrete fields (line1, city, state, zip) or parse it. Lob's verification API will normalize/validate it before sending.
3. **Each sample deploys to a unique URL.** Stage 2 (doc 01) deploys every sample under `SAMPLE_BASE_URL/<sample_slug>` on your own subdomain and stores `sample_slug` on the lead. The postcard's QR/URL points at that same slug, so a scan maps to exactly one lead.
4. **A valid `MAIL_FROM` address** (the virtual-mailbox street address from the Setup Guide, Phase 5). Lob requires a real return address.

---

## Config additions (`.env`)

```
# --- Lob (physical mail) ---
LOB_API_KEY_TEST=                 # test key — build & preview against this first (no real mail, no charge)
LOB_API_KEY_LIVE=                 # live key — used ONLY after operator approval
LOB_MODE=test                     # test | live  (flip to live only when ready, like WARMUP_COMPLETE for email)

# --- Return address (CAN-SPAM-style footer + Lob requirement) ---
MAIL_FROM_NAME=                   # same person/studio name as SENDER_NAME
MAIL_FROM_LINE1=
MAIL_FROM_CITY=
MAIL_FROM_STATE=
MAIL_FROM_ZIP=

# --- Caps / budget guardrails ---
MAIL_BATCH_CAP=10                 # max pieces per approval batch (start small)
MAIL_MONTHLY_BUDGET_USD=100       # HARD stop — agent refuses to queue past this in a calendar month
POSTCARD_SIZE=4x6

# --- Tracking ---
SAMPLE_BASE_URL=                  # e.g. https://samples.yourstudio.com — used to build per-lead URLs + QR
MAIL_UTM=utm_source=postcard      # appended to tracked URLs so analytics distinguishes mail visits
```

---

## DB schema addition (`mail_pieces`)

One row per postcard attempt, linked to a lead. Keep all mail state here, not on the leads table.

```sql
CREATE TABLE IF NOT EXISTS mail_pieces (
  id              INTEGER PRIMARY KEY,
  lead_id         INTEGER NOT NULL REFERENCES leads(id),
  status          TEXT NOT NULL,      -- eligible | pending_approval | approved | sent | failed | suppressed
  tracking_slug   TEXT NOT NULL,      -- = the lead's sample_slug (doc 01/04); the unique URL path / QR target for this lead
  address_verified INTEGER DEFAULT 0, -- 1 once Lob verification passes (deliverable)
  lob_id          TEXT,               -- Lob postcard id after submission
  cost_usd        REAL,               -- per-piece cost Lob reports
  preview_url     TEXT,               -- Lob-rendered front/back preview (shown to operator)
  batch_id        TEXT,               -- groups pieces approved together
  created_at      TEXT,
  approved_at     TEXT,
  mailed_at       TEXT,
  scanned_at      TEXT                -- set when the QR/URL is visited → interest signal
);
```

Suppression: if a recipient ever asks not to be contacted (rare for mail, but possible), set `status='suppressed'` and never re-mail that lead — mirror the email opt-out suppression in doc 00.

---

## The flow (stage by stage)

### Step A — Select eligible leads
Query leads where **all** are true:
- `email_status = 'not_found'` (the targeting decision — mail only reaches the un-emailable)
- a sample exists and is deployed (`sample_url` / `tracking_slug` present)
- a postal address is present
- no existing `mail_pieces` row for this lead with status in (`sent`, `pending_approval`, `approved`, `suppressed`) — no double-mailing

Insert a `mail_pieces` row with `status='eligible'` for each, up to `MAIL_BATCH_CAP`.

### Step B — Verify addresses (before spending anything)
For each eligible piece, call **Lob US Verifications** (`/v1/us_verifications`). Keep only `deliverability = deliverable` (or `deliverable_unnecessary_unit`). Set `address_verified=1`. For undeliverable ones, mark the piece `status='failed'` (note reason) so money isn't wasted on returns — and because that lead can be neither emailed nor mailed, flip the **lead** to status `PHONE_ONLY` (doc 04) so it surfaces in `/status` for a manual call instead of silently disappearing.

### Step C — Render the postcard (test mode)
Build front + back from HTML templates (see "Postcard content" below), with per-lead merge fields (business name, sample URL, QR image). Create the postcard against **`LOB_API_KEY_TEST`** so Lob returns a **preview** (front/back PNG/PDF) and a quoted cost **without mailing or charging**. Store `preview_url` and `cost_usd`. Set `status='pending_approval'`.

### Step D — Operator approval (Telegram)
Reuse `notify.py`. Send the operator one message per batch:
- batch size, per-piece cost, **batch total**, month-to-date spend vs `MAIL_MONTHLY_BUDGET_USD`
- a few preview images (front/back) from Step C
- buttons / reply commands: **APPROVE** (mail the whole batch) · **REJECT** (discard) · optionally **APPROVE PARTIAL** (reply with the ids to send)

Nothing proceeds without an explicit approve. If the operator doesn't respond, pieces stay `pending_approval` (no timeout auto-send — money must always be gated).

### Step E — Submit live
On approval: re-create each approved postcard against **`LOB_API_KEY_LIVE`** (Lob Postcards API, `size=4x6`, `from`=MAIL_FROM, `to`=verified address, `front`/`back`=rendered HTML). Store `lob_id`, real `cost_usd`, `batch_id`, set `status='sent'`, `mailed_at=now`. Enforce the `MAIL_MONTHLY_BUDGET_USD` hard stop here too — refuse and alert the operator if a batch would cross it.

### Step F — Track interest + hand off
A scan/visit of the per-lead tracking URL is the interest signal:
- Append `?{MAIL_UTM}` to the QR/URL target so visits are attributable to the postcard channel.
- **Visits are detected by `serve_samples.py`** — the local web app that serves every sample at `SAMPLE_BASE_URL/<sample_slug>`, fronted by the Cloudflare Tunnel (doc 01, doc 04). On each `GET /<slug>` it writes a `sample_visits` row (with `source='postcard'` from the UTM) and then serves the page. Because the serving process and the database are the same machine, this fires reliably and in real time — there is no edge→local gap. (Cloudflare Web Analytics is sampled and delayed, so it is at most an optional secondary dashboard — never the notification trigger.)
- On the **first** visit for a postcard lead: set `scanned_at` on its `mail_pieces` row, and **notify the operator on Telegram exactly like the email track's interest handoff (doc 02, stage 5)** — "Lead X scanned their postcard / viewed their sample." The operator then takes over personally (their job, per Site Goal).
- Note the channel split (doc 02/04): postcard visits **push** this notification; email-lead visits are logged silently and seen via `/views`. The same `serve_samples.py` handles both — it branches on the lead's `email_status`.

---

## Postcard content (4×6, double-sided)

Keep it human, specific, and low-pressure — the hook is that you *already built them something*.

- **Front:** large, clean. Business name + a one-line hook ("We built a free preview of a new website for {business_name}") + a prominent **QR code that opens their customized sample**. The QR (to the live sample) is the visual hook — do **not** put a screenshot/thumbnail of the sample on the card, since rendering the HTML sample to an image would require the headless browser that's off by default on the free VM (doc 00).
- **Back:** 2–3 sentence message in the operator's voice, the **typed sample URL** (in case the QR isn't scanned), the QR again, and a footer with:
  - sender name + the **physical return address** (`MAIL_FROM_*`)
  - a soft opt-out line (e.g. "Not interested? No need to do anything — you won't hear from us again.")

Generate the copy with `claude-opus-4-8` (same quality tier as the email copy, per doc 00), personalized per business, reusing the tone rules in `email-writing-instructions.md`.

Template files: `postcard_templates/front.html`, `postcard_templates/back.html` — Lob accepts HTML with `{{merge}}` variables and renders to print. Mind Lob's 4×6 bleed/safe-area dimensions (≈6.25"×4.25" full bleed); follow Lob's HTML template guide.

---

## Suggested files (extends the doc 00 repo layout)

```
/pipeline
  send_mail.py             # THIS track: select → verify → render(test) → submit(live). Lob client.
  postcard_templates/
    front.html
    back.html
  qr.py                    # build per-lead QR pointing at SAMPLE_BASE_URL/<slug>?utm...
  # reuses: db.py (new table), notify.py (approval + interest), config.py (.env), run.py (schedule the job)
```

`run.py` adds a recurring `mail_job`: run Steps A–D on a schedule (e.g. once or twice daily), then Step E fires when an approval arrives via the Telegram handler. Step F runs continuously off the `serve_samples.py` visit log (doc 01/04) — no separate endpoint to build for this track.

---

## Caps, cost & expectations
- ~**$0.50–0.75 per 4×6 postcard** all-in (print + postage) on Lob. Budget accordingly via `MAIL_MONTHLY_BUDGET_USD`.
- **Always build/preview in `test` mode first** (`LOB_MODE=test`) — free, no mail sent — and only flip to `live` once a real batch is approved. Treat the test/live key split like the email `WARMUP_COMPLETE` gate.
- Physical delivery takes **a few business days**, so this is inherently lower-volume and slower-feedback than email. That's fine — its whole purpose is to work the funnel during the otherwise-dead 2-week warm-up window and to reach the un-emailable leads thereafter.
- Start with `MAIL_BATCH_CAP=10` and a low monthly budget; raise only after seeing scan/response rates.

---

## Compliance note (flag for review — not legal advice)
Physical direct mail is **not** governed by CAN-SPAM the way email is, but direct-mail advertising has its own norms and rules that can vary by state/locality. Including a clear return address and a soft opt-out is good practice. **Treat this as general guidance and confirm specifics with a qualified professional before scaling volume** — do not treat anything here as a final compliance determination. Flag any non-US recipients for the operator to review separately.

---

## Done-when
- `leads.db` has the `mail_pieces` table and an `email_status` column populated by stage 1.
- Each sample deploys to a unique, trackable per-lead URL; QR codes resolve to it with the UTM tag.
- A `test`-mode batch renders correct front/back previews and a cost quote, delivered to the operator on Telegram.
- Operator APPROVE triggers a real `live` send; the postcard's `lob_id`, cost, and `mailed_at` are recorded.
- A scan/visit flips `scanned_at` and pushes an interest notification to the operator — the human handoff fires.
- Monthly budget hard-stop and no-double-mail dedup are both enforced and verified.
