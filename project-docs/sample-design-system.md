# Sample Design System (the quality engine)

This doc is the art direction and generation method behind the sample sites. It exists because **sample quality is the single biggest lever on reply rate** — a forgettable, template-looking mockup makes the whole outreach read as spam, and a genuinely "oh, that's better than mine" mockup *is* the pitch. `01-build-sample-instructions.md` handles the mechanics (gather → generate → gate → serve). This doc handles the part that decides whether the output is good.

The failure mode we are designing against: single-shot generation from a data blob regresses to the mean — every business gets the same centered hero, three service cards, one testimonial strip. Competent, forgettable, and obviously templated the moment a recipient sees a neighbor's. Everything below exists to break that.

---

## Principle 1 — Real images before anything else

Nothing signals "AI template" faster than a CSS-gradient hero. Nothing sells "this is a real, better version of *my* business" faster than a photo of *their* storefront / their work / their food, cleanly framed. Images are the #1 visual quality lever, ahead of layout and copy.

**Image source ladder** (use the first that passes the quality gate; fall through on failure):

1. **Google Places Photos** — you already hit Places in stage 1, so photo references are free and available. These are *their real business*. Primary source.
2. **Their existing site** — `og:image`, the hero `<img>`, or the largest images scraped in STEP 1. Only if the site isn't `NO_SITE`.
3. **Niche stock** — free **Pexels** (`PEXELS_API_KEY`) or **Unsplash** (`UNSPLASH_ACCESS_KEY`) API, keyed by niche. Real photography, not gradients. *(Implemented — `build_sample.stock_images`; the prompt explicitly tells the model stock images are atmosphere, never "their" premises.)*
4. **Typographic hero (last resort)** — a strong type-driven hero on a solid/subtle-gradient background, *only* when no usable photo exists. Never the default.

**Photo quality gate** (their photos "first unless bad quality") — *implemented in `find_leads` (stores each photo's width/height) + `build_sample.rank_photos` (hero-first ordering; near-square/portrait photos never lead; if nothing hero-worthy exists the ladder falls to stock)*:
- Places returns dimensions — **reject anything under ~800px on the long edge**; heroes should be landscape and ≥1200px wide where possible.
- Reject obvious junk: near-square logos used as heroes, screenshots, heavily-watermarked images, photos that are >90% one flat color.
- During the **calibration phase** (below), the vision pass also rejects ugly/blurry/awkward photos the dimension check can't catch. In steady state, lean on dimensions + aspect ratio + count.
- If a lead has photos but none pass, fall to niche stock rather than shipping a bad photo — a clean stock image beats a real ugly one.

Store which source each image came from on the lead (`image_source`) so calibration can see whether "their photos" is actually winning.

---

## Principle 2 — Per-niche art direction, injected into the prompt

Don't ask for "a modern site." Hand the model a design brief for that specific industry. This table is the source of truth; the generator looks up the lead's `category` (from stage 1), maps it to the nearest niche row, and injects the brief into the prompt.

| Niche | Mood | Palette direction | Type pairing | Section that LEADS | Primary CTA | Imagery |
|---|---|---|---|---|---|---|
| **Plumbing / HVAC / electrical / roofing** | Trust + urgency | High-contrast; deep blue or navy + a warm accent (red/orange) | Bold geometric sans (headings) + clean sans (body) | Hero with **giant click-to-call** + "24/7 emergency" | Tap-to-call phone | Trucks, crews at work, before/after |
| **Landscaping / lawn / tree / pool** | Fresh, outdoorsy, capable | Greens + earthy neutrals | Friendly sans; slightly rounded | Full-bleed photo hero of finished work | Get a free quote | Lush finished projects, wide shots |
| **Auto repair / detailing / body shop** | Rugged, dependable | Charcoal/black + a single bold accent | Industrial/condensed headings + plain sans | Hero + services grid | Book service / call | Shop, cars, work in progress |
| **Restaurant / café / bakery / bar** | Warm, appetite-driven | Warm tones matched to cuisine | Characterful display + readable body | Big food hero, then menu | View menu / order / reserve | Food close-ups, interior ambiance |
| **Law / accounting / insurance / financial** | Authoritative, restrained | Navy/charcoal + subtle gold or slate | Classic serif or refined sans | Hero with credibility line + credentials | Free consultation | Office, headshots, city skyline |
| **Dental / medical / chiro / vet** | Clean, reassuring, modern | Calm blues/teals + white | Rounded, approachable sans | Hero + trust markers (insurance, new-patient) | Request appointment | Clean office, friendly staff, patients |
| **Fitness / gym / studio / martial arts** | High-energy, bold | Dark base + one electric accent | Heavy condensed headings | Bold motion-y hero | Start free trial / class | Action shots, space, community |
| **Contractor / remodel / handyman / painting** | Solid, proven | Neutral + one confident accent | Sturdy sans | Before/after or portfolio-led hero | Get an estimate | Project galleries, before/after |
| **Cleaning / moving / pest / misc. service** | Friendly, reliable, quick | Bright, clean, one strong accent | Approachable sans | Hero + simple 3-step "how it works" | Get a quote / book | Crews, results, happy-home shots |
| **(fallback) generic local business** | Modern, professional, warm | One confident brand color + neutrals | Clean sans pairing | Hero with clear value prop | Contact / call | Best available real photo |

Each brief specifies **palette direction, type pairing, mood, which section leads, the CTA, and imagery type** — that is most of the difference between "template" and "designed for me." Palettes are *directions*, not exact hex — let the model choose specific values so samples in the same niche still vary.

---

## Principle 3 — Layout variety (so 50 samples in one city don't rhyme)

Keep a small set of genuinely different **layout archetypes** and pick one deterministically per lead so a batch has visible variety but re-runs are stable:

```
archetype = ARCHETYPES[ hash(business_id) % len(ARCHETYPES) ]
```

Archetypes (each is a different *structure*, not a different color):
1. **Split hero** — headline/CTA on one side, image on the other.
2. **Full-bleed image hero** — edge-to-edge photo, text overlaid, content below.
3. **Photo-grid led** — compact hero, then a strong gallery/portfolio grid high on the page.
4. **Menu/services-forward** — minimal hero, the services/menu is the first real block (best for restaurants, trades with clear service lists).

Constrain the archetype in the prompt so the model commits to one strong structure instead of averaging them into mush. Niche can *bias* the choice (restaurants lean menu-forward; landscapers lean full-bleed) but the hash keeps variety within a niche.

---

## Principle 4 — Fix *their specific gap*

The sample is only "better than theirs" if it beats what they actually have. Stage 1 already records `qualify_status` + `qualify_reason` — feed that into the generator so the result is pointedly better, not just different:

- `NO_SITE` → the pitch is "you have zero web presence; here's a real one." Lead hard on credibility + being findable.
- `OUTDATED` + "not mobile responsive" → make **mobile the hero of the design**; that's the exact thing you're beating.
- `OUTDATED` + "© 2015 / dated look" → make it feel unmistakably current (modern type, spacing, motion-light polish).
- `OUTDATED` + "no HTTPS / free subdomain" → emphasize a clean, professional, owns-their-presence feel.

Pass `qualify_reason` into the prompt as an explicit instruction: *"Their current site's weakness is X — make sure this sample visibly fixes X."*

---

## Principle 5 — Multi-pass generation, not single-shot

Generate → critique → refine. Two passes, both on `claude-opus-4-8`:

1. **Pass 1 — generate.** Prompt = niche brief + chosen archetype + `business_profile` (real details) + selected images + the gap to fix. Produce one self-contained responsive HTML file. The system prompt also carries the niche's **style exemplar** and curated **LESSONS** (see `pipeline/exemplars/README.md`), and the base prompt is **prompt-cached** so retries re-read it at ~10% of input price.
2. **Pass 2 — critique + refine.** A rubric-driven review (see below) that either passes the page or returns specific fixes; regenerate **once** applying them. A PASS where any core factor scores a 4 ("fine, forgettable") gets the same single polish pass — funded by the caching savings. Cap total attempts at ~3 so cost stays bounded.

**Rubric (used by both the text critique and the vision grade):**
- **Bespoke, not templated** — does this look designed for *this* business, or like a generic skeleton? (single most important line)
- **Hero lands** — clear what the business is + one strong action, in the first screen.
- **Real & specific** — real name, real services, real review quotes, real contact. Zero invented facts.
- **Imagery** — a real, good photo is present and well-placed (not a gradient default).
- **Visual craft** — balanced spacing, consistent type scale, sufficient contrast, no awkward gaps.
- **Beats their current site** — visibly fixes the recorded `qualify_reason`.
- **Mobile** — responsive, no horizontal scroll, tap targets big enough.

Score each 1–5; regenerate if any critical line (bespoke / hero / mobile / imagery) is below 4.

---

## Principle 6 — Where the "look at it" check happens (calibration → autonomous, at $0)

The free VM can't run Chromium, and we're not paying for a screenshot API. So the vision loop lives where it has the most leverage and costs nothing:

**Phase A — Calibration (first ~30–50 samples, on the laptop).**
- Run generation on your own machine, which *can* run Playwright/Chromium for free.
- Pass 2 becomes a **real vision grade**: render the HTML → screenshot → Opus (vision) scores it against the rubric → regenerate on low scores. *(Implemented as `run.py vision <lead_id ...>` / `vision --url <url>` in `calibrate.py` — screenshots desktop fold, mobile fold, and full page, grades A–G, and prints a suggested `run.py grade` command to edit and paste. A screenshot costs ~a tenth of the input tokens the old 60KB-of-HTML text critique did.)*
- You also eyeball every one (this is the "grade the first 30" plan). Use this phase to harden the niche briefs, the prompt, the image logic, and the photo-quality thresholds. **Do not trust the pipeline until this phase looks consistently good.**
- Log rubric scores + your verdicts so you can see which niches/archetypes/image-sources win.

**Phase B — Autonomous (steady state, on the VM).**
- Once the design system is proven, the VM generates with a **text-only self-critique** Pass 2 (Opus reviews its own HTML against the rubric + `qualify_reason`, regenerates once if weak). No Chromium, no cost, fully autonomous.
- The dialed-in design system carries the quality; the text critique is the safety net, not the primary quality source.

**Ongoing spot-check.** Weekly, pull a handful of live samples back to the laptop for a vision grade. If scores drift, retune in Phase A and redeploy — don't patch blind on the VM.

This is the honest best-quality-at-$0 answer: real screenshots exactly where they change your decisions (tuning), free everywhere else.

---

## Hard rules (never violate — these override "make it look good")

- **Never invent facts.** No fake awards, fake testimonials, fake service claims, fake stats. Testimonials come only from real reviews, lightly cleaned, attributed generically ("— Google review").
- **Never ship a bad photo to look "real."** Clean stock beats a real ugly image.
- **Never plaster "SAMPLE" across the page** — the "this is a free mockup" framing lives in the email/postcard, not on the design. A tiny tasteful footer line is optional and fine.
- **Never ship a broken image or `lorem ipsum`.** Structural gate in doc 01 STEP 3 still applies on top of everything here.
- **Respect ToS / public data only** — same constraint as stage 1.

---

## What "good" means (the bar to hold)
A local business owner glances at their sample on their phone and thinks *"wait — that's my business, and it looks better than what I have. Who made this?"* If the reaction is "nice template" or "which company is this," it failed, no matter how clean the code is.
