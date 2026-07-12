# Agent Brief: Find Local Businesses With Outdated or No Website + Contact Email

## Objective
Build a list of local businesses that either (a) have **no website** or (b) have an **outdated/broken website**, then find a valid **contact email** for each. Output a clean spreadsheet-ready list for cold outreach (web design/redesign services).

## Inputs Required From the Operator
Before starting, confirm these three things:
1. **Location** — city/region/ZIP (e.g., "Dallas, TX" or "Phoenix metro").
2. **Industry/niche** — e.g., contractors, auto repair, restaurants, salons, law firms, dentists, landscapers, HVAC, plumbers. (Trades and local service businesses lag most on web presence — best targets.)
3. **Target count** — how many qualified leads to gather (e.g., 25, 50, 100).

These three inputs come from **standing campaign config** (set once in `.env` — see doc 04) so the funnel auto-tops-up on its own, **and** can be supplied ad-hoc by an operator `/find` command from Telegram (doc 02) for a one-off area/niche. Either way, the job needs all three before it runs.

---

## STEP 1 — Pull the business list from maps

Use Google Maps (primary) and Yelp (secondary) for each `[industry] in [location]` search.

For each business, capture these raw fields:
- Business name
- Phone number
- Address
- Website field (the URL Google/Yelp shows — or note if **blank**)
- Google rating + review count (signals an active, real business worth pitching)
- Category

**How to extract at scale (pick what's available):**
- **Manual / small batch:** Search `[industry] [city]` in Google Maps, scroll the left results panel, record each listing.
- **Programmatic / large batch:** Use the **Google Places API** (Text Search + Place Details). Key fields: `name`, `formatted_phone_number`, `website`, `rating`, `user_ratings_total`, `business_status`. The `website` field being absent is the strongest "no website" signal.
- **Scraping tools (if no API key):** Apify "Google Maps Scraper", Outscraper, or PhantomBuster all export Maps results to CSV including the website field.

---

## STEP 2 — Qualify each business (the filter that matters)

Mark each business as one of: **NO_SITE**, **OUTDATED**, or **SKIP**.

**NO_SITE** (highest priority):
- No website field on the listing, OR
- The "website" link points to a Facebook/Instagram page instead of a real site, OR
- Link is dead (returns error / domain for sale / parked page).

**OUTDATED** (strong targets) — visit the site and check for:
- No HTTPS (URL is `http://` only) — big red flag.
- **Not mobile responsive** — load it on a phone-width viewport; if text is tiny and you must pinch-zoom, it's outdated.
- Copyright footer year is old (e.g., "© 2016").
- Flash, table-based layout, or visibly dated design (early-2010s look).
- Broken images, "Lorem ipsum" placeholder text, or a free template subdomain (`wixsite.com`, `weebly.com`, `godaddysites.com`).
- Slow load (>5 sec) — optionally confirm with PageSpeed Insights.

**SKIP:**
- Already has a modern, fast, mobile-friendly site.
- `business_status` is not "OPERATIONAL" (closed/temporarily closed).
- National chains/franchises (corporate handles their web).
- Fewer than ~5 reviews (may be inactive or not worth the effort).

---

## STEP 3 — Find a contact email

The website field is often blank, so emails take digging. Try these in order and stop when you find a valid one:

1. **Their own website** (if outdated): check the Contact / About / footer pages. Best source.
2. **Facebook page:** Most local businesses have one. The "About"/"Intro" section often lists an email and is usually more current than their website.
3. **Yelp / Yellowpages / BBB listings:** sometimes expose an email.
4. **WHOIS lookup** (if they own a domain): the registrant email is sometimes public.
5. **Guess + verify common patterns** for the domain: `info@`, `contact@`, `hello@`, `office@`, `[firstname]@`. Then **verify** before trusting it (see below).
6. **Email-finder tools (optional):** Hunter.io / Snov.io / Apollo.io can find emails from a domain, but they cost a subscription. The agent does steps 1–5 itself for free, so a paid finder is an optional upgrade only if free finding yields too few emails.

**Always verify deliverability before trusting an address** (bounces hurt the sender reputation you spent weeks warming — see `email-agent-instructions.md`):
- **Primary: ZeroBounce API** (`ZEROBOUNCE_API_KEY` in `.env`). Pay-as-you-go (~$0.003–0.005/check, ~$5/mo at this volume). Discard anything flagged invalid/risky/catch-all.
- **Fallback (free): DNS MX lookup + SMTP RCPT probe** — cheaper but less accurate; use if no verifier key is configured.
- This is the one bounce gate. Set **`email_status`** on the lead: `found` if an address passes verification, otherwise `not_found`. (`email_verified` is just the derived 1/0 form of this — see doc 04.)
- **Retry once on a *transient* failure** (network error, timeout, verifier rate-limit, or an ambiguous "unknown" result) before deciding. Only a clean pass → `found`; anything that still doesn't pass after the one retry → `not_found`. Never leave a qualified lead at the default `unverified` — that state strands it between both tracks (doc 04).

If no email passes verification, set `email_status='not_found'`. These leads are **not** dropped — they route to the **postcard track (doc 06)**, which mails them their sample. For that they need a valid **mailing address** (Google Places provides it), so make sure `address` is populated. **If a lead has no email *and* no usable mailing address** (so it can be neither emailed nor mailed), set its status to **`PHONE_ONLY`** when a phone number exists — it's surfaced in `/status` for you to optionally call or walk in — or drop it (logged) if it has no contact method at all. The postcard track also flips a lead to `PHONE_ONLY` later if Lob finds its address undeliverable (doc 06, Step B).

---

## STEP 4 — Write results directly to the database

`find_leads.py` writes each qualified business **straight into the `leads` table** (see doc 04) — there is no manual spreadsheet step. This keeps the pipeline autonomous while staying auditable (every lead sits in `QUALIFIED` before any email is sent, and you can inspect the DB or run `/status` in Telegram at any time).

For each business:
1. **Dedup before insert** — skip if its **phone** already exists, OR its **full email address** already exists, OR its **email domain** exists *and that domain is a custom business domain* (not a free-mail provider like gmail.com / yahoo.com / outlook.com / hotmail.com / aol.com / icloud.com). The `UNIQUE` index on phone backs the phone check. **Never dedup on a free-mail domain alone** — many distinct businesses use `gmail.com`, so collapsing them would silently drop real leads. Re-runs must never create duplicates.
2. Insert as `status = FOUND`, then apply the STEP 2 filter and set `qualify_status` (`NO_SITE` / `OUTDATED`) + `qualify_reason`. Only businesses that pass become `QUALIFIED`; the rest are logged and skipped (`SKIP`).
3. Derive and store the lead's **`timezone`** from its state/region in the address (e.g. `TX → America/Chicago`). The `send_emails` job uses this to send during the recipient's local 9am–5pm window (doc 04).
4. Populate the columns below; store the gathered material as JSON in `source_profile` for the sample stage.

Fields written per lead: business_name, category, phone, address, **timezone**, existing_website, qualify_status, qualify_reason, email, **email_status** (`found`/`not_found`), email_verified (derived), rating, review_count, source_profile, notes.

> Optional review export: the agent may also dump a CSV of newly-found `QUALIFIED` leads (same fields) for you to eyeball — but the DB is the source of truth, not the CSV.

Prioritize processing **NO_SITE first**, then **OUTDATED**; within each, higher review count first (more established = better client).

---

## Quality Bar
- Every row must be reachable by **some** channel: a verified email (`email_status='found'`), OR a valid mailing address for the postcard track (`email_status='not_found'`), OR — failing both — a phone number (`PHONE_ONLY`). A row with no email, no address, and no phone is dropped, not stored.
- "Reason" column must be specific ("no HTTPS + © 2015 footer", not just "old").
- Deduplicate by phone number and full email address (and by domain only when it's a custom business domain, never a free-mail one).
- Aim for the operator's target count of **qualified** leads, not raw listings.

---

## Compliance Note (do not skip)
This list is for **B2B** outreach. Cold email to businesses is generally permitted under **CAN-SPAM (US)**, but the operator must:
- Use accurate "From"/subject lines.
- Include a real physical mailing address.
- Include a clear unsubscribe/opt-out and honor it.
- (If contacting EU/Canada businesses, GDPR / CASL have stricter rules — flag for the operator to review.)
This brief covers gathering publicly listed business contact info only. It does not authorize bulk spam, scraping in violation of a site's Terms of Service, or use of purchased personal-email lists. The operator is responsible for compliant outreach.
```
