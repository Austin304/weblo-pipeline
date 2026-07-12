# Operator Setup Guide — Do These Before the Build

This is your follow-along checklist. It covers **only the things a human has to do** — creating accounts, spending money, and setting safety caps. The actual pipeline build is done by the AI build agent afterward (Phase 11).

**How to use this:** go top to bottom. Each step says *where to go*, *what to click*, *what to enter*, and *how to know it worked*. As you collect each secret value, paste it straight into the `.env` file in this folder (open it in a text editor) at the variable name called out in **→ `.env`** lines.

**Two notes before you start:**
- Button labels and menu names on these websites change over time. If a label here doesn't match exactly, look for the closest match or use the site's search/help — the *flow* stays the same.
- The compliance/legal lines (CAN-SPAM, mailing address) are **general guidance, not legal advice** — confirm specifics with a qualified professional before you start real outreach.

**Time:** ~2 hours of actual work, but spread it across a day — Phase 3 (domain warm-up) and Phase 5 (mailbox) have waiting periods, so start those first.

---

## Phase 1 — Lock down the Cloudflare token (5 min) 🔒

You pasted a Cloudflare API token in chat earlier. Even though it's rolled, do this to be safe.

1. Go to **https://dash.cloudflare.com** and log in.
2. Top-right: click your **profile icon → My Profile → API Tokens** (or go straight to **https://dash.cloudflare.com/profile/api-tokens**).
3. Look at the token list. If you see an **old** token you previously shared, click the **⋯** next to it → **Delete** (or **Roll**). Keep only the current `weblo-pipeline` token.
4. Open the [.env](.env) file in this folder. Confirm line 10 (`CLOUDFLARE_API_TOKEN=`) holds your **current** token, not the old one.
5. **OneDrive check:** This project is in your `Downloads` folder. Open File Explorer, right-click the `Site` folder — if you see OneDrive status icons (cloud/checkmark), your secrets may be syncing to the cloud. Move the whole `Site` folder to a non-synced location like `C:\weblo\` to be safe.

✅ **Done when:** only your current token exists in Cloudflare, and `.env` matches it.

---

## Phase 2 — Confirm your domain is on Cloudflare (5 min)

You already own **webloapp.com** (it's in your `.env`). Just confirm it.

1. At **https://dash.cloudflare.com**, look at the site list on the home page.
2. Confirm **webloapp.com** is listed. Click it — you'll land on its **Overview** page.
3. Click **DNS** in the left menu. You'll use this DNS screen several times below (it's where you add the email-authentication records).

✅ **Done when:** you can see webloapp.com and open its DNS page.

---

## Phase 3 — Google Workspace + email setup (45 min) — **DO THIS EARLY**

This sets up your professional sending address (`you@webloapp.com`). The 2-week warm-up clock (Phase 4) can't start until this is done, so do it first.

### 3a. Create the Workspace account
1. Go to **https://workspace.google.com** → click **Start Free Trial** (or **Get Started**).
2. When asked about a domain, choose **"Use a domain I already own"** and enter **webloapp.com**.
3. Create your admin username — e.g. **austin@webloapp.com** (this becomes your `SENDER_EMAIL`). Set a strong password.
4. Finish signup. You'll land in the **Google Admin console** at **https://admin.google.com**.

→ `.env`: uncomment and set `SENDER_EMAIL=austin@webloapp.com` and `SENDER_NAME=Weblo` (in the EMAIL TRACK section at the bottom).

### 3b. Verify you own the domain
1. In the Admin console setup wizard, Google asks you to **verify the domain** by adding a **TXT record**. It shows you a value like `google-site-verification=xxxx`.
2. Open a second tab → Cloudflare → webloapp.com → **DNS → Records → Add record**:
   - **Type:** TXT · **Name:** `@` · **Content:** paste the `google-site-verification=...` value → **Save**.
3. Back in Google, click **Verify**. (May take a few minutes.)

### 3c. Turn on email (MX records)
1. Google's wizard then gives you **MX records** to route mail. In Cloudflare DNS → **Add record** for each MX row Google lists:
   - **Type:** MX · **Name:** `@` · **Mail server:** the value Google shows (e.g. `smtp.google.com`) · **Priority:** the number Google shows → **Save**.
2. Back in Google, click **Activate Gmail** / **Continue**.

### 3d. Authenticate the domain (SPF, DKIM, DMARC) — critical for inbox delivery
Without these, cold email goes straight to spam. Add three TXT records in Cloudflare DNS:

- **SPF** — Type: TXT · Name: `@` · Content: `v=spf1 include:_spf.google.com ~all`
- **DKIM** — In Admin console go to **Apps → Google Workspace → Gmail → Authenticate email**. Click **Generate new record**, copy the host/value it shows. In Cloudflare add: Type: TXT · Name: the host Google shows (e.g. `google._domainkey`) · Content: the long `v=DKIM1...` value. Then back in Google click **Start authentication**.
- **DMARC** — Type: TXT · Name: `_dmarc` · Content: `v=DMARC1; p=none; rua=mailto:austin@webloapp.com`

### 3e. Verify it all passes
1. From your new `austin@webloapp.com`, send a test email to a personal Gmail.
2. In Gmail, open it → **⋮ → Show original**. You want **SPF: PASS, DKIM: PASS, DMARC: PASS**.

✅ **Done when:** all three say PASS. (Doc 05 in this folder has more detail if a step needs it.)

---

## Phase 4 — Start the email warm-up service (15 min) — **THE LONG POLE**

A brand-new domain has zero reputation. A warm-up service quietly builds it over ~2 weeks while the rest gets built. Start it the same day as Phase 3.

1. Pick one: **Instantly** (https://instantly.ai), **Smartlead** (https://smartlead.ai), or **Warmbox** (https://warmbox.ai). Any is fine (~$30–50/mo).
2. Sign up → find the **"Warmup"** or **"Email Warmup"** section.
3. **Connect your mailbox:** choose **Google / Gmail**, sign in as `austin@webloapp.com`, grant access.
4. **Turn warmup ON.** Leave default settings. Let it run ~2 weeks.

✅ **Done when:** the service shows your mailbox connected and "warming." **Do not send any real cold email until this has run ~2 weeks** (the build agent enforces this, but know the rule).

---

## Phase 5 — Get a mailing address (≈20 min, 100% online)

US cold email legally needs a real physical postal address in the footer, and Lob needs a return address for postcards. Use a mailbox service, **not** your home address.

**Chosen path: a virtual mailbox service** (a rented *street* address you can set up entirely remotely). This beats a UPS Store (needs an in-person visit) and a USPS PO Box (weaker optics + usually an in-person ID/key step). With a virtual mailbox, all mail goes to the provider's facility, they scan it to your email, and you read it from anywhere — ideal if you move around. The provider's facility street address is what you put in `.env`.

**Have ready:** a photo ID, a second ID/address document for Form 1583 (bank statement, utility bill, lease, or passport — doesn't need to match where you currently stay), a payment method (~$10–15/mo), and a phone/webcam for online notarization.

1. **Pick a provider:** **iPostal1** (https://ipostal1.com — most locations), **Stable** (https://www.usestable.com — cleanest for business), or **Anytime Mailbox** (https://www.anytimemailbox.com). All set up fully online.
2. **Choose a location** → pick a real facility address (nearby metro or your home state both fine). This becomes your business street address.
3. **Pick the basic plan** (scan-to-email is all you need) and complete signup.
4. **File Form 1583 with built-in online notarization** — upload your two IDs and do the ~5-min webcam notary session in the signup flow. This is the legal step that activates the address.
5. **Turn on email/app notifications**, default new mail to "scan."
6. Copy your assigned address and write it one line: `Name, 123 Main St #100, City, ST 12345`.

→ `.env`:
- `BUSINESS_MAILING_ADDRESS=` (one-line, for email footers)
- `MAIL_FROM_NAME=Weblo` · `MAIL_FROM_LINE1=` · `MAIL_FROM_CITY=` · `MAIL_FROM_STATE=` · `MAIL_FROM_ZIP=` (split out, for Lob)

✅ **Done when:** you have a usable street address and it's in `.env`. *(General guidance, not legal advice — confirm CAN-SPAM specifics with a professional before launching.)*

---

## Phase 6 — Telegram bot (10 min)

This is how the pipeline pings your phone when a lead is interested, and how you approve postcard batches.

1. On your phone, install **Telegram** and open it.
2. In the search bar, type **@BotFather** and open the verified result (blue check).
3. Send **`/newbot`**. It asks for a **name** (e.g. `Weblo Pipeline`) then a **username** ending in `bot` (e.g. `weblo_pipeline_bot`).
4. BotFather replies with a **token** like `8123456789:AAE...`. Copy it.
   → `.env`: `TELEGRAM_BOT_TOKEN=`
5. **Get your chat ID:** In Telegram, search your new bot's username, open it, tap **Start**, and send it any message ("hi").
6. On a computer, open this URL in a browser (paste your token in place of `<TOKEN>`):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   In the JSON that appears, find `"chat":{"id":123456789` — that number is your chat ID.
   → `.env`: `TELEGRAM_CHAT_ID=123456789`

✅ **Done when:** both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are in `.env`.

---

## Phase 7 — Anthropic (Claude) account + key + spend cap (10 min) 💰

This is the AI that writes the sample sites and emails. **The spend cap here is your #1 cost protection** — do not skip it.

1. Go to **https://console.anthropic.com** and sign in / create an account.
2. **Set a spending limit FIRST:** click **Settings** (gear, top or left) → **Billing** (or **Limits / Usage limits**). Add a payment method, then set a **monthly spend limit** — start at **$50** to match `CLAUDE_MONTHLY_BUDGET_USD`. This is the hard ceiling the vendor enforces even if code misbehaves.
3. **Create the API key:** Settings → **API Keys** → **Create Key**. Name it `weblo-pipeline` (a dedicated key keeps this project's spend isolated). Copy the `sk-ant-...` value — **it's shown only once.**
   → `.env`: `ANTHROPIC_API_KEY=sk-ant-...`

✅ **Done when:** a monthly spend limit is set **and** the key is in `.env`.

---

## Phase 8 — Google Cloud: lead-finding + the server + cost caps (25 min) 💰

This covers the Google Places API (finds businesses), the cost caps on it, and the free server that runs everything.

### 8a. Create the project
1. Go to **https://console.cloud.google.com**. Top bar, click the **project dropdown → New Project** → name it `weblo-pipeline` → **Create**. Make sure it's selected in the top bar.
2. You'll need **billing enabled**: search "Billing" in the top search bar → link a payment method.

### 8b. Enable Places API + get a key
1. Top search bar: type **"Places API"** → open it → click **Enable**.
2. Left menu: **APIs & Services → Credentials → Create Credentials → API key**. Copy it.
   → `.env`: `GOOGLE_PLACES_API_KEY=`
3. Click the new key → under **API restrictions**, restrict it to **Places API** → Save (limits blast radius if leaked).

### 8c. Cap Places spending (do both)
1. **Hard quota cap:** **APIs & Services → Places API → Quotas & System Limits**. Find **Requests per day**, click the pencil, set a low daily cap (e.g. 1000). This *actually stops* spend.
2. **Budget alert:** top search **"Budgets & alerts"** → **Create Budget** → scope to this project → set amount to **$20** (matches `PLACES_MONTHLY_BUDGET_USD`) → set alert thresholds at 50% / 90% / 100% → Finish. *(Alerts notify; the quota above is the real cap.)*

### 8d. Create the free server (the always-on machine)
1. Top search: **"Compute Engine"** → **Create Instance**.
2. Set: **Name** `weblo-pipeline` · **Region** `us-central1` (must be a free-tier region) · **Machine type** `e2-micro` · **Boot disk** → Change → **Debian 12**, **30 GB**, **Standard persistent disk** · leave HTTP/HTTPS **unchecked** → **Create**.
3. **Set a $1 safety alert:** Budgets & alerts → Create Budget → **$1** → this catches any accidental paid resource. (The e2-micro itself is free.)

✅ **Done when:** Places key in `.env`, a daily quota + $20 budget set, and the `weblo-pipeline` VM shows a green "running" dot. *(The build agent configures the software on this VM later — see doc 07.)*

---

## Phase 9 — Lob (postcards) (10 min)

For mailing sample-site postcards to leads with no email.

1. Go to **https://dashboard.lob.com** → sign up.
2. Find **Settings → API Keys**. You'll see a **Test** key and a **Live** key.
   → `.env`: `LOB_API_KEY_TEST=` (test) and `LOB_API_KEY_LIVE=` (live)
3. Leave `LOB_MODE=test` for now — nothing real mails until you flip it to `live` after approving a batch. Your `MAIL_MONTHLY_BUDGET_USD=100` and per-batch Telegram approval keep this gated.

✅ **Done when:** both Lob keys are in `.env`.

---

## Phase 10 — Final `.env` check (10 min)

Open [.env](.env) and confirm every blank below is filled (email-track ones can stay commented until warm-up finishes):

**Active now (postcard track + core):**
- `ANTHROPIC_API_KEY` ✅ (Phase 7)
- `GOOGLE_PLACES_API_KEY` ✅ (Phase 8)
- `CLOUDFLARE_API_TOKEN` ✅ (Phase 1) · `CLOUDFLARE_ACCOUNT_ID` (find it: in Cloudflare, the hex string in the URL after `dash.cloudflare.com/`)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` ✅ (Phase 6)
- `CAMPAIGN_LOCATION` (e.g. `Dallas, TX`), `CAMPAIGN_NICHE` (e.g. `plumbers`) — your first target
- `LOB_API_KEY_TEST`, `LOB_API_KEY_LIVE` ✅ (Phase 9)
- `MAIL_FROM_*`, `BUSINESS_MAILING_ADDRESS` ✅ (Phase 5)
- Budgets are pre-filled (`PIPELINE_MONTHLY_BUDGET_USD=150`, etc.) — adjust if you want.

**Commented until email warm-up finishes (~2 weeks):**
- `SENDER_EMAIL`, `SENDER_NAME` (Phase 3) — fill but leave commented per the file's structure until the build agent turns on email.

✅ **Done when:** no required blank remains in the active section.

---

## Phase 11 — Hand off to the build agent

You're done with the human part. Now point the AI build agent at this folder and tell it:

> "Build the pipeline following the order of operations in `00-system-overview.md`. The `.env` is filled, the GCP VM exists, and the cost caps are set. Start with doc 07 (configure the VM), then doc 04, then the rest."

The agent provisions software on your VM, builds each stage, and wires in the cost guardrails (doc 08). It **cannot** spend real money beyond your Phase 7/8 caps, and it **won't** send real cold email until your domain has warmed ~2 weeks.

---

## Quick reference — where each thing lives

| What | Where | Fills in `.env` |
|---|---|---|
| Cloudflare token/DNS | dash.cloudflare.com | `CLOUDFLARE_*` |
| Email address + auth | workspace.google.com / admin.google.com | `SENDER_EMAIL`, `SENDER_NAME` |
| Warm-up | instantly.ai / smartlead.ai | — |
| Mailing address | ipostal1.com / usestable.com (virtual mailbox) | `MAIL_FROM_*`, `BUSINESS_MAILING_ADDRESS` |
| Phone alerts | Telegram → @BotFather | `TELEGRAM_*` |
| Claude AI + cap | console.anthropic.com | `ANTHROPIC_API_KEY` |
| Lead-finding + server + caps | console.cloud.google.com | `GOOGLE_PLACES_API_KEY` |
| Postcards | dashboard.lob.com | `LOB_API_KEY_*` |
