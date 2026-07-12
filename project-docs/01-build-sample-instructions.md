# 01 — Build Sample Website (Stage 2)

## Objective
For each qualified lead, automatically generate a tailored, modern, single-page sample website and deploy it to a clean public URL. This sample is the centerpiece of the cold email — it must look like a real, better version of *their* site so they want the full build.

> **Read `sample-design-system.md` first.** This doc is the mechanics (gather → generate → gate → serve). `sample-design-system.md` is the *quality engine* — image sourcing, per-niche art direction, layout variety, fixing the lead's specific gap, and the multi-pass generate→critique loop. Sample quality is the biggest lever on reply rate, so the design-system doc governs STEP 2 and STEP 3 below; where they differ, it wins.

**Trigger:** a lead in state `QUALIFIED` (set by stage 1).
**Output:** a live public URL + state `SAMPLE_BUILT` written to the DB.

---

## STEP 1 — Gather source material about the business

Pull together everything that makes the sample specifically theirs. Use only publicly available data; respect each site's Terms of Service.

Sources, in order:
1. **Their existing site** (if `OUTDATED`): scrape with `requests` + `beautifulsoup4`. (`playwright` is **off by default** — it won't fit the free VM's RAM, see doc 00.) If a site is JS-heavy and `requests` returns a near-empty shell, **do not enable a browser — fall back to the Google Places data in source #2**, which is the primary material for the sample anyway. Capture: business name, tagline, list of services/products, hours, phone, address, any "about" copy, and image/logo URLs (incl. `og:image` and the hero image).
2. **Google Places / Maps data** (always available from stage 1): name, category, address, phone, hours, rating, review count, **review text** (great for pulling real testimonials and understanding what customers praise), and **Places Photos** (their real storefront/work/food — the primary imagery for the sample; see the image ladder below).
3. **Facebook/Yelp** (if found in stage 1): additional description, photos, services.

### Images (do this deliberately — it's the #1 quality lever)
Follow the **image source ladder and photo-quality gate in `sample-design-system.md`**: Places Photos first (their real business), then their site's `og:image`/hero, then free niche stock (Unsplash/Pexels), and a typographic hero only as a last resort. Enforce the quality gate (reject sub-~800px / junk / wrong-aspect photos; a clean stock image beats a real ugly one). Record `image_source` on the lead so calibration can see whether their-own-photos is actually winning.

Assemble everything into a single structured `business_profile` (dict / JSON) that gets passed to the generator — including `category` (drives the niche brief), `qualify_status` + `qualify_reason` (the gap to fix), the selected images, and the real review quotes. If a field is missing, omit it — never invent fake details, fake awards, or fake testimonials.

---

## STEP 2 — Generate the HTML with Claude (multi-pass)

Use the Claude API (`claude-opus-4-8`) to generate **one self-contained, responsive, single-page** website: HTML with inline CSS (one file, no external build step). Keep JS minimal or none.

**This is a multi-pass generate → critique → refine loop, not a single shot** (see `sample-design-system.md`, Principle 5):
1. **Pass 1 — generate** using the niche brief (looked up from `category`) + the chosen layout archetype (`hash(business_id) % archetypes`) + the `business_profile` + selected images + the `qualify_reason` gap to fix.
2. **Pass 2 — critique + refine** against the rubric; regenerate **once** applying the fixes. Cap total attempts at ~3 so cost stays bounded.

The **vision** part of Pass 2 runs only during the **calibration phase on the laptop** (real screenshot + Opus vision grade); on the **autonomous VM** Pass 2 is a **text-only self-critique** — see `sample-design-system.md`, Principle 6. Same rubric either way.

### Requirements for the generated page
- **Mobile-first and fully responsive** (this is often the exact thing their current site fails at).
- **Fast and lightweight** — inline CSS, system fonts or one Google Font, no heavy frameworks.
- **Modern, clean design** appropriate to the industry (a plumber and a salon should not look identical).
- Real sections: hero with business name + what they do, services/menu, why-choose-us, a testimonial or two pulled from **real reviews**, hours/location, and a clear contact CTA (their real phone/address).
- Use their **real** business info. Pull genuine review quotes for testimonials (lightly cleaned, attributed generically e.g. "— Google review").
- **No placeholder text** ("lorem ipsum"), no broken images. If you don't have a real image, use a tasteful CSS color/gradient hero or a relevant free stock image — never a broken `<img>`.
- Accessible: semantic HTML, alt text, sufficient contrast.

### Prompt template (starting point — the agent should refine)
The prompt must carry the **art direction**, not just the data — a bare "you're an expert designer, here's a JSON" prompt regresses to a generic template (the exact failure mode we're avoiding). Inject the niche brief, the archetype, the images, and the gap to fix:
```
You are an expert web designer creating a sample site to win this local business as a client.
Generate ONE complete, single-file, responsive HTML page (inline CSS, minimal/no JS), mobile-first.

DESIGN DIRECTION (niche: {niche}):
  Mood: {mood}
  Palette direction: {palette}      (choose specific values in this direction)
  Type pairing: {type_pairing}
  Lead with: {leading_section}
  Primary CTA: {primary_cta}
  Imagery style: {imagery}
LAYOUT ARCHETYPE (commit to this structure): {archetype}
IMAGES TO USE (real, already selected — place them well; do not invent others):
  {image_urls_with_roles}
THEIR CURRENT SITE'S WEAKNESS: {qualify_reason}
  → This sample must visibly FIX that weakness.

Use ONLY the real details below — never invent services, awards, or testimonials.
Testimonial quotes come only from the provided reviews, attributed generically ("— Google review").

BUSINESS PROFILE:
{business_profile_json}

Return only the HTML, nothing else.
```

Pass 2 (critique) prompt scores the result against the rubric in `sample-design-system.md` and returns either PASS or a specific fix list; regenerate once on a fail.

### Optional "this is a sample" framing
The sample should look like their real site (that's what sells it). Do **not** plaster "SAMPLE" across it. The framing that this is a free mockup belongs in the **email** (see `email-writing-instructions.md`), not on the page. A tiny, tasteful footer line like "Sample design — not affiliated" is optional and acceptable.

---

## STEP 3 — Quality gate (before deploy)

This is the **structural** gate — it runs *in addition to* the rubric critique in STEP 2 (Pass 2). The rubric judges whether the page looks *good*; this gate catches hard defects that must never ship. Reject and regenerate (max ~2 retries) if any of these fail:
- Page contains the real business name.
- No "lorem ipsum" / placeholder strings.
- No obviously broken markup; HTML parses cleanly.
- Renders at a phone-width viewport without horizontal scroll. Verify this **structurally** (responsive inline CSS present, a `<meta name="viewport">` tag, no fixed widths wider than the viewport) — since you generate the HTML yourself with inline responsive CSS, you control this. A live `playwright` render check is off by default (doc 00) and only available on a ≥2 GB box.
- At least the hero, services, and contact sections are present.
- **At least one real image is used** when one passed the quality gate in STEP 1 (i.e. the page didn't silently fall back to a gradient hero despite a good photo being available). All `<img>` src values are reachable (no broken links).

If it still fails after retries, mark the lead `SAMPLE_FAILED` and skip it (log why). Don't email a broken sample.

---

## STEP 4 — Publish the sample (local web app + Cloudflare Tunnel)

Samples are **served by a small web app on the always-on GCP VM** (`serve_samples.py`), exposed publicly through a **Cloudflare Tunnel** at the custom subdomain `samples.<your-domain>` (this is `SAMPLE_BASE_URL` in `.env`). This replaces a separate static host (e.g. Cloudflare Pages): serving from the VM lets the *same* process that returns the page also log the visit straight to `leads.db`, which is what makes visit tracking work with no edge→local gap. Cloudflare still fronts everything with TLS/CDN/DDoS — no inbound ports are opened on the VM (the tunnel is outbound-only).

- Write the generated HTML to the samples directory (e.g. `samples_cache/<sample_slug>/index.html`) where `serve_samples.py` looks it up. No upload/deploy step — publishing is just writing the file the local app serves.
- Each sample lives at a unique **path** — e.g. `https://samples.yourstudio.com/joes-plumbing-dallas`. Give each a **clean, unique, business-specific slug** (`sample_slug`) — e.g. `joes-plumbing-dallas`. Slugs must be unique (append city or a short hash if needed). Store the slug on the lead (doc 04); both the email and postcard tracks use it.
- **`serve_samples.py` responsibilities** (full spec in doc 04): on each `GET /<sample_slug>` it (1) logs a row to `sample_visits` and (2) serves the HTML. On the **first** visit of a **postcard** lead it also fires the operator interest notification (doc 06, Step F); email-lead visits are logged only, viewable on demand via `/views` (doc 02). It reads `?utm_source=…` to record the visit's channel (postcard / email / direct).
- Path-based on **your own domain** is required so visits can be **tracked** per-lead. The URL must be a **full, clean, real URL** — no shorteners. (The email rules ban shortened links.)
- Confirm the URL returns HTTP 200 (through the tunnel) before marking success.

---

## STEP 5 — Record state

Write back to the DB (see doc 04):
- `sample_url`
- `sample_slug` (the unique path slug — used for visit tracking by both the email and postcard tracks)
- `sample_built_at` (timestamp)
- `status = SAMPLE_BUILT`

This makes the lead eligible for the email stage.

---

## Quality bar
- Every deployed sample loads, is mobile-responsive, and uses the business's real details.
- No two samples are visually identical — design varies by industry and business.
- A human glancing at it should think "that's a real, nice website for this business," not "that's a template."
- Never deploy a sample with invented facts, fake reviews, or broken assets.
