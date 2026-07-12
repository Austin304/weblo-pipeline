# 05 — Email Domain Setup & Warm-Up (DO THIS FIRST)

## Objective
Stand up a professional sending identity on a custom domain and warm it up so cold outreach lands in the inbox. **Start this on day one** — using a **paid warm-up service, warm-up takes ~2 weeks** and runs in parallel with building the rest of the pipeline, so the domain is ready when the pipeline is. (The warm-up is the real clock here — the coding itself is faster than the warm-up.)

> Why not the personal Gmail? A `@gmail.com` address looks unprofessional for B2B outreach, can't be authenticated as a real sending domain, and isn't warmed for bulk either. A custom domain is strictly better long-term — the only catch is it must be warmed, which is what this doc handles.

---

## STEP 1 — Buy the domain
- Pick a clean, professional, business-like name (e.g. a short studio/agency name). Avoid hyphens/numbers and anything spammy.
- Register it (Cloudflare Registrar is at-cost and convenient since samples already use Cloudflare; Namecheap/Porkbun also fine).

## STEP 2 — Set up Google Workspace
- Create Google Workspace on the domain → mailbox `you@yourstudio.com` (this becomes `SENDER_EMAIL`).
- Workspace uses the **Gmail API**, so the send/read code in `email-agent-instructions.md` and doc 02 works unchanged — just authenticated as the new address instead of the personal Gmail.

## STEP 3 — Authenticate the domain (critical for deliverability)
Add these DNS records (Workspace provides the values):
- **SPF** — TXT record authorizing Google to send for the domain.
- **DKIM** — generate the key in the Workspace admin console and publish the TXT record. Confirm it's "authenticating."
- **DMARC** — start with `p=none` (monitor), e.g. `v=DMARC1; p=none; rua=mailto:dmarc@yourstudio.com`. Tighten to `quarantine` later once clean.

Without SPF + DKIM + DMARC, cold email goes to spam no matter how good the copy is. Verify all three pass (e.g. send a test to a Gmail address and check "Show original" → SPF/DKIM/DMARC = PASS).

## STEP 4 — Mailbox hygiene
- Set a real display name (`SENDER_NAME`) and a simple human signature.
- Set `Reply-To` = the same sending address (per email-agent-instructions.md).
- Get the **physical mailing address** for the CAN-SPAM footer (a UPS Store mailbox / PO Box — see doc 00). Put it in `BUSINESS_MAILING_ADDRESS`.

## STEP 5 — Sign up for Postmaster Tools
- Add the domain to **postmaster.google.com** to monitor domain reputation and spam rate throughout warm-up and beyond.

---

## STEP 6 — Warm up the domain (~2 weeks, paid service, in the background)

A brand-new domain has zero reputation. Ramp it up while sending genuine, engaged-with email. **Do not send a single cold-outreach email until warm-up is well underway and reputation looks healthy.**

**Chosen approach: Option B — a paid warm-up service (~2 weeks).** Option A (free, manual, ~3–4 weeks) is kept below as a no-cost fallback. A 2-week warm-up has slightly less reputation cushion than the full 3–4 weeks, so the careful first-sends ramp in **Step 7** is what makes it safe — treat your first real cold sends as an extension of warm-up.

### Option A — Manual / organic warm-up (free) — fallback only
- Week 1: ~5–10 emails/day to friends, your own other accounts, and known contacts who will **open and reply**. Replies are the strongest positive signal.
- Week 2: ~15–20/day, keep getting replies; have a couple of contacts move any spam-foldered ones to "Not Spam."
- Week 3: ~25–40/day, begin mixing in a few low-stakes real outreach emails.
- Week 4: ramp toward the `DAILY_SEND_CAP`; reputation in Postmaster Tools should be Medium/High and spam rate near zero before scaling cold sends.

### Option B — Warm-up service (paid, hands-off, faster) — CHOSEN
- Tools like Instantly, Smartlead, or Warmbox auto-send and auto-reply within a network of inboxes to build reputation (~$30–50/mo). Connect the Workspace mailbox and let it run for the **~2-week** warm-up window, then taper as real cold volume ramps (Step 7). The postcard track (doc 06) runs in parallel during this same window, so outreach is happening even while email warms.

### Throughout warm-up
- Keep the engaged-reply ratio high; avoid sudden volume spikes.
- Watch Postmaster Tools. If reputation dips or spam rate rises, slow down (cut volume ~50%) and recover (see "Recovery" in email-agent-instructions.md).
- The ramp here feeds `DAILY_SEND_CAP` in doc 04 — start the cap low and raise it weekly, never by more than ~20%/week.

---

## STEP 7 — First real cold sends (the careful ramp)

Because we chose the shorter **2-week** warm-up, the first weeks of *real* cold outreach are themselves part of staying safe — a 2-week domain has less reputation cushion, so ramp into volume while watching the gauge rather than jumping to the full cap.

1. **Start at `DAILY_SEND_CAP = 10`/day** (set in `.env`), to your best leads only (NO_SITE, high review count).
2. Watch **Google Postmaster Tools daily.** Healthy = spam rate near zero, domain reputation Medium/High.
3. Each clean week, raise the cap **by ~20%** (10 → 12 → 15 → 18 → … toward ~40).
4. If spam rate rises or reputation dips at any point: **cut the cap ~50% and slow down** (see "Recovery" in `email-agent-instructions.md`), then resume ramping only once it's clean again.
5. **~40/day is a solid steady-state target** for this setup; only push past it if Postmaster stays green.

The 60–120s spacing (with random jitter) and the 9am–5pm recipient-local-time window (doc 04) stay constant at every volume — only the daily cap ramps.

---

## Done-when
- Domain registered, Workspace live, SPF + DKIM + DMARC all PASS.
- Mailbox has a human name/signature and a valid mailing address for the footer.
- Postmaster Tools shows healthy reputation after the warm-up ramp.
- Only then does the `send_emails` job in doc 04 begin real cold outreach.
