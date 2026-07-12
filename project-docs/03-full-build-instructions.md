# 03 — Full Production Build (Stage 6)

## Objective
Turn an authorized, interested lead into a finished, paid production website. This is the only stage that produces the actual deliverable the customer pays for — and per `Site Goal.md`, it stays under the operator's (Austin's) control. The agent assists and scaffolds; **it never publishes to a customer's live domain or treats a build as final without the operator's say-so.**

**Trigger:** status `AUTHORIZED`, set only by the operator's explicit `/build <lead_id>` command (doc 02). The agent must never self-authorize.

---

## Guiding principle
The sample (doc 01) was a fast, public, single-page pitch built from public data. The full build is a real client project: real content, real assets, real domain, and real money. So this stage is **operator-led with agent assistance**, not fully autonomous.

---

## STEP 1 — Open the project
On `AUTHORIZED`:
- Create a project workspace for the client (folder/repo), seeded from the lead's `source_profile` and the deployed `sample_url` as the starting design direction.
- Notify the operator (Telegram) that the build workspace is ready and list what's needed from the client (see Step 2).

## STEP 2 — Gather real inputs (operator-mediated)
The agent prepares a short intake the operator collects from the client during their conversation:
- Final business details, real copy, logo, real photos, services/menu, pricing.
- Pages wanted (Home, About, Services, Contact, etc.) vs. single-page.
- Domain: do they own one? If not, who registers it?
- Any integrations: contact form destination, booking, maps, social links, analytics.

The agent must use **real, client-approved** content — no invented details carried over from the sample's public-data guesses.

## STEP 3 — Build
- Expand the sample into the agreed scope: multi-page if requested, real assets, polished responsive design, SEO basics (titles, meta, sitemap), fast load, accessibility.
- Keep the stack simple and maintainable (static site or lightweight framework as fits the project).
- Wire up the contact form to actually deliver (e.g. to the client's email) and any agreed integrations.
- Stage it on a **preview URL** (e.g. a temporary path behind the same Cloudflare Tunnel, or any static host) — not the client's live domain yet.

## STEP 4 — Operator + client review
- Send the preview URL to the operator (Telegram). The operator reviews and shares with the client for approval and revisions.
- Iterate on feedback. **Do not go live until the operator confirms approval.**

## STEP 5 — Go live (operator-gated)
Only after explicit operator approval:
- Connect the client's real domain (DNS) and deploy to production.
- Verify HTTPS, all pages, forms, and links work on mobile and desktop.
- Hand off: confirm the client has what they need; the operator handles payment and the client relationship.

## STEP 6 — Close out
- Set `delivered_at` + status `DELIVERED`.
- Telegram confirmation to the operator.

---

## Human-in-the-loop guardrails (non-negotiable)
- Build only on explicit `/build` authorization.
- Never publish to a customer's live domain without explicit operator approval of the preview.
- Never fabricate content, testimonials, or credentials in a production site.
- Payment and the customer relationship are the operator's — the agent does not negotiate price or send invoices unless the operator later defines that separately.

---

## Note
This stage is intentionally the least automated — it's where quality and the client relationship matter most, and where the Site Goal deliberately keeps Austin in control. Start with the operator-led flow above; automate more of it later only if it proves safe and repeatable.
