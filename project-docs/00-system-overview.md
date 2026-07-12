# 00 — System Overview & Handoff (READ THIS FIRST)

This is the master document for the autonomous web-design lead pipeline. Read it before any other file. It explains how the pieces fit, the tech stack, where state lives, and which document owns which stage.

The product goal is defined in **`Site Goal.md`** — read that second.

> **Operator (human) setup:** the one-time accounts, purchases, and cost caps that a person must do before the build are in **`SETUP-GUIDE.md`** — a click-by-click walkthrough. This doc and 01–08 are for the build agent; SETUP-GUIDE is for Austin.

---

## What we're building

A mostly-autonomous Python pipeline that: finds local businesses with bad/no websites → builds each a tailored sample site → **cold-emails the sample to leads with a findable email, and mails a postcard of it to the leads without one** → notifies the operator (Austin) on a phone when someone is interested → and, only on the operator's explicit command, builds the full paid site.

The operator stays in the loop for exactly two things:
1. Talking to interested prospects.
2. Giving the explicit "build it" command.

Everything else runs on its own.

---

## The pipeline, end to end

```
[1] FIND LEADS        → find-leads-brief.md            (already written)
[2] BUILD SAMPLE      → 01-build-sample-instructions.md  (mechanics)
                        sample-design-system.md         (quality engine: art direction, images, multi-pass)
[3] SEND COLD EMAIL   → email-agent-instructions.md  (plumbing/deliverability, already written)
                        email-writing-instructions.md (email copy, already written)
[4] WATCH + NOTIFY    → 02-reply-detection-and-notification-instructions.md
[5] OPERATOR HANDOFF  → operator talks to the prospect (human)
[6] AUTHORIZE + BUILD → 03-full-build-instructions.md  (triggered by operator command)

[T2] PHYSICAL MAIL    → 06-physical-mail-instructions.md  (postcards to the no-email leads; runs from day 1, in parallel with email warm-up)

Supporting:
- ORCHESTRATION/STATE → 04-orchestration-and-state-instructions.md  (the backbone that runs 1–4 + T2)
- EMAIL DOMAIN SETUP  → 05-domain-setup-and-warmup-instructions.md  (do this FIRST, runs in parallel)
- VM PROVISIONING/DEPLOY → 07-vm-provisioning-and-deploy-instructions.md  (stand up the always-free GCP box everything runs on; do on day one alongside doc 05)
- COST CONTROLS/BUDGET → 08-cost-controls-and-budget-instructions.md  (provider hard caps + in-code spend ledger so the pipeline can't run up a surprise bill)
- METRICS/FEEDBACK    → 09-metrics-and-feedback-instructions.md  (kill-switch when it's not working + accelerator toward what is; reports what, not why)
```

---

## Decisions already made (do not re-litigate)

| Area | Decision |
|---|---|
| Language | Python |
| AI provider | **Claude API (Anthropic)**. Use `claude-opus-4-8` for sample-site HTML generation and email copy (quality); `claude-haiku-4-5-20251001` or `claude-sonnet-4-6` for reply classification (cheap/fast). |
| Sample sites | AI-generated single-page static HTML/CSS, served by a small **local web app on the always-on VM, exposed publicly via a Cloudflare Tunnel** at `samples.<your-domain>/<slug>` (path-based, on your own domain). The same app logs every visit straight to `leads.db` — this is what makes visit tracking work with no edge→local gap (Cloudflare still fronts it with TLS/CDN/DDoS, no inbound ports opened). Doc 01. |
| State store | **SQLite** (single local `.db` file). Schema in doc 04. |
| Notifications | **Telegram bot** — phone push, two-way (operator can reply to authorize the build). Doc 02. |
| Send identity | **Custom domain + Google Workspace** (e.g. `you@yourstudio.com`), NOT the personal Gmail. Doc 05. |
| Warm-up | Buy domain now; warm via a **paid service (~2 weeks)** in the background while the pipeline is built, then ramp real sends slowly (doc 05 Step 7). Doc 05. |
| Second channel | **Physical postcards via Lob** to leads with no findable email — runs from day 1, in parallel with warm-up. Operator approves each batch on Telegram. The two tracks are **mutually exclusive** (a lead is emailed or mailed, never both). Doc 06. |
| Run environment | A **GCP `e2-micro` VM on the always-free tier** (Linux, ~1 GB RAM), separate from Austin's laptop. `$0/mo`, runs forever. `cron` runs the scheduled batch jobs; `systemd` keeps the two long-running processes alive (auto-restart on crash). The Cloudflare Tunnel is **outbound-only**, so no inbound ports and no reserved static IP are needed — that's what keeps it inside the free tier. The ~1 GB RAM is the one real constraint: it comfortably runs the daemons + tunnel + SQLite, but **not** headless Chromium (see the scraping row). |
| Site scraping / qualify | **`requests` + `beautifulsoup4` only; `playwright` is OFF by default** — Chromium won't fit the free VM's ~1 GB RAM. JS-heavy prospect sites that return a near-empty shell fall back to Google Places data (the primary source for samples anyway). Enable `playwright` only on a ≥2 GB box if ever needed. Docs 01 + find-leads. |
| Cost control | **Two layers (doc 08):** provider-side hard caps (Anthropic monthly spend limit + dedicated key; Google Places per-day quota + budget alert) **and** an in-code `spend_ledger` that pauses a job + alerts on Telegram before crossing a monthly budget. Global ceiling = `PIPELINE_MONTHLY_BUDGET_USD`. The pipeline cannot quietly run up a bill. |

---

## Tech stack / dependencies

- **Python 3.11+**
- `anthropic` — Claude API
- `google-api-python-client`, `google-auth`, `google-auth-oauthlib` — Gmail (read + send)
- `requests` / `httpx` — Telegram, Cloudflare, general HTTP
- `beautifulsoup4`, `lxml` — scraping existing sites for sample source material
- `sqlite3` (stdlib) — state
- `flask` (or `fastapi` + `uvicorn`) — the local web app that serves samples and logs every visit to `leads.db` (doc 01, doc 04)
- `cloudflared` — Cloudflare Tunnel that exposes that local app publicly at `samples.<your-domain>` (no inbound ports opened)
- **`playwright` — OFF by default, not installed.** Chromium needs more RAM than the free `e2-micro` (~1 GB) has, so the default path uses `requests` + `beautifulsoup4` only (docs 01 + find-leads are written around this). Enable `playwright` **only** on a box with ≥2 GB RAM if you later want to render JS-heavy sites or screenshot them; the pipeline is designed to run fully without it.

---

## Secrets / config (`.env` — never commit)

The new agent should create a single `.env` consumed by all components. **It's phased to match the build order:** the CORE + Lob (postcard) vars are active first, because the postcard track goes live on day one; the email-track vars (`SENDER_EMAIL`, `SENDER_NAME`, `GMAIL_OAUTH_CREDENTIALS`, `ZEROBOUNCE_API_KEY`, `DAILY_SEND_CAP`) stay **commented out** until the email channel is built after warm-up (doc 05). The full set, with the email block shown commented:

```
ANTHROPIC_API_KEY=
GOOGLE_PLACES_API_KEY=
ZEROBOUNCE_API_KEY=                                 # email verification (optional; falls back to free MX/SMTP check if blank)
PEXELS_API_KEY=                                      # free stock photos (image ladder step 3; optional but kills gradient heroes)
UNSPLASH_ACCESS_KEY=                                 # fallback stock source (optional)
GMAIL_OAUTH_CREDENTIALS=path/to/credentials.json   # token.json generated on first auth
CLOUDFLARE_API_TOKEN=                                # manage DNS + the Tunnel
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_TUNNEL_NAME=samples                       # named Cloudflare Tunnel that fronts the local samples web app
SAMPLES_PORT=8788                                    # local port the samples web app listens on (the Tunnel points here)
SAMPLE_BASE_URL=                                     # custom subdomain for samples, e.g. https://samples.yourstudio.com (path-based per lead; required for visit tracking)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=                                    # the operator's chat id
SENDER_EMAIL=                                        # the warmed Workspace address
SENDER_NAME=
BUSINESS_MAILING_ADDRESS=                            # CAN-SPAM footer (see below)
DAILY_SEND_CAP=10                                    # START at 10; raise ~20%/week toward ~40 as Postmaster stays clean (doc 05 Step 7)
DB_PATH=leads.db
CAMPAIGN_LOCATION=                                   # standing lead-gen target, e.g. "Dallas, TX" (weekly auto-top-up; doc 04). An ad-hoc /find run overrides it per-run.
CAMPAIGN_NICHE=                                      # e.g. "plumbers"
CAMPAIGN_TARGET_COUNT=50                             # how many QUALIFIED leads to keep in the funnel
# --- postcard track (doc 06) adds its own vars: LOB_API_KEY_TEST / LOB_API_KEY_LIVE, LOB_MODE, MAIL_FROM_*, MAIL_BATCH_CAP, MAIL_MONTHLY_BUDGET_USD, POSTCARD_SIZE, MAIL_UTM ---
```

---

## Suggested repo layout

```
/pipeline
  .env
  leads.db
  config.py            # loads .env, constants
  db.py                # SQLite schema + helpers (doc 04)
  costs.py             # pricing + spend ledger / budget gate (doc 08)
  find_leads.py        # stage 1 (find-leads-brief.md)
  build_sample.py      # stage 2 (doc 01)
  serve_samples.py     # local web app: serves /<slug> samples + logs every visit to leads.db (doc 01/04); fronted by Cloudflare Tunnel
  write_email.py       # email copy via Claude (email-writing-instructions.md)
  send_email.py        # Gmail send + pacing (email-agent-instructions.md)
  watch_replies.py     # stage 4 daemon (doc 02)
  notify.py            # Telegram (doc 02)
  full_build.py        # stage 6 (doc 03)
  send_mail.py         # track 2 — postcards via Lob (doc 06)
  qr.py                # per-lead QR codes for postcards (doc 06)
  /postcard_templates  # front.html / back.html (doc 06)
  run.py               # scheduler / entrypoint (doc 04)
  /samples_cache       # generated HTML before deploy
  /logs
```

---

## Legal / compliance note (CAN-SPAM) — required setup

US commercial email is governed by CAN-SPAM. It does **not** require a registered business, but it does require, on every cold email:
1. Accurate `From`, `Reply-To`, and subject lines (no deception).
2. A **real physical postal address** in the footer. A UPS Store mailbox or PO Box satisfies this without using a home address. **This is the one missing prerequisite — get a mailbox before real outreach begins.**
3. A clear opt-out (e.g. "Reply STOP to opt out") that is honored promptly (within ~10 days). Opt-outs are tracked in the DB and must permanently suppress that address.

This is general guidance, not legal advice — confirm specifics with a qualified professional before launching outreach. EU/Canada recipients fall under GDPR/CASL, which are stricter; flag any non-US targets for the operator to review.

---

## Order of operations for the new agent

0. **Provision the VM (doc 07)** in parallel with doc 05 — stand up the always-free GCP `e2-micro`, install deps + `cloudflared`, so there's a running home for the state layer, the daemons, and the tunnel. Doc 05's 2-week warm-up is the long pole, so kick both off on day one.
1. **Doc 05 first** — buy domain + Workspace and start warm-up immediately via a **paid warm-up service (~2 weeks)**, running in the background. Then ramp real sends carefully (doc 05 Step 7).
2. Build the **state layer (doc 04)** — everything else writes to it.
3. Build **stage 1** (find-leads-brief.md) → **stage 2** (doc 01). Stage 1 must set `email_status` (`found`/`not_found`); stage 2 serves each sample at `SAMPLE_BASE_URL/<sample_slug>` via the local samples app (`serve_samples.py`) fronted by the Cloudflare Tunnel. Stand up the tunnel + samples app here, since both outreach tracks link to these URLs.
4. Build the **postcard track (doc 06)** — it runs from day one during warm-up and reaches the `email_status='not_found'` leads. Needs the state layer + stages 1–2 (it mails the samples); does **not** need the warmed email domain, so it delivers outreach while email waits.
5. Build **email copy + send** (the two existing email docs), pointed at the warming domain, sending only to `email_status='found'` leads. Do **not** send real cold email until the domain is warm.
6. Build **stage 4 watch + notify (doc 02)** — the Gmail reply watcher, the Telegram notifications, and the `/views` command. (Sample visit-logging itself lives in `serve_samples.py` from step 3; doc 06 Step F consumes it for the postcard interest signal.)
7. Build **metrics/feedback (doc 09)** once sends + replies are flowing — the bounce guard first (protects the warmed domain), then the funnel view, then the positive-rate kill switch, then the accelerator. It reads the same DB and reuses the doc-02 notifier.
8. Build **stage 6 full-build framework (doc 03)** last — it's operator-triggered and least time-sensitive.
