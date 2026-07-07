"""Stage 2 — build a tailored sample site per QUALIFIED lead (doc 01).

Quality engine per sample-design-system.md: image ladder, per-niche art
direction, layout archetypes, gap-fixing, multi-pass generate -> critique.
Publishing = writing samples_cache/<slug>/index.html for serve_samples.py.
"""
import json
import logging
import re
import secrets
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import config
import costs
import db

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3          # total generation attempts (doc 01 STEP 2/3)
GEN_MAX_TOKENS = 16000
EST_GEN_USD = 0.45        # worst-case pre-call estimate (doc 08: estimate from max_tokens)

# --- Principle 2: per-niche art direction ---------------------------------
NICHE_BRIEFS = {
    "trades": dict(
        match=("plumb", "hvac", "electric", "roof", "heating", "air_condition"),
        mood="Trust + urgency",
        palette="High-contrast; deep blue or navy + a warm accent (red/orange)",
        type_pairing="Bold geometric sans (headings) + clean sans (body)",
        leading_section="Hero with giant click-to-call + '24/7 emergency' if applicable",
        primary_cta="Tap-to-call phone",
        imagery="Trucks, crews at work, before/after"),
    "outdoor": dict(
        match=("landscap", "lawn", "tree", "pool", "garden"),
        mood="Fresh, outdoorsy, capable",
        palette="Greens + earthy neutrals",
        type_pairing="Friendly sans; slightly rounded",
        leading_section="Full-bleed photo hero of finished work",
        primary_cta="Get a free quote",
        imagery="Lush finished projects, wide shots"),
    "auto": dict(
        match=("auto", "car_repair", "detail", "body_shop", "tire", "mechanic"),
        mood="Rugged, dependable",
        palette="Charcoal/black + a single bold accent",
        type_pairing="Industrial/condensed headings + plain sans",
        leading_section="Hero + services grid",
        primary_cta="Book service / call",
        imagery="Shop, cars, work in progress"),
    "beauty": dict(
        match=("salon", "spa", "barber", "aesthet", "beauty", "nail", "med_spa",
               "medical_spa", "skin"),
        mood="Elegant, airy, aspirational",
        palette="Soft neutrals, blush/sage, lots of whitespace",
        type_pairing="Serif display + light sans",
        leading_section="Imagery-forward hero, minimal text",
        primary_cta="Book appointment",
        imagery="Interiors, results, clean product shots"),
    "food": dict(
        match=("restaurant", "cafe", "bakery", "bar", "pizza", "food", "coffee",
               "diner", "grill"),
        mood="Warm, appetite-driven",
        palette="Warm tones matched to cuisine",
        type_pairing="Characterful display + readable body",
        leading_section="Big food hero, then menu",
        primary_cta="View menu / order / reserve",
        imagery="Food close-ups, interior ambiance"),
    "professional": dict(
        match=("law", "attorney", "account", "insurance", "financ", "tax",
               "bookkeep", "notary"),
        mood="Authoritative, restrained",
        palette="Navy/charcoal + subtle gold or slate",
        type_pairing="Classic serif or refined sans",
        leading_section="Hero with credibility line + credentials",
        primary_cta="Free consultation",
        imagery="Office, headshots, city skyline"),
    "medical": dict(
        match=("dent", "orthodont", "medical", "chiro", "vet", "clinic",
               "physical_therap", "doctor", "wellness"),
        mood="Clean, reassuring, modern",
        palette="Calm blues/teals + white",
        type_pairing="Rounded, approachable sans",
        leading_section="Hero + trust markers (insurance, new-patient)",
        primary_cta="Request appointment",
        imagery="Clean office, friendly staff, patients"),
    "fitness": dict(
        match=("gym", "fitness", "yoga", "martial", "crossfit", "pilates",
               "studio"),
        mood="High-energy, bold",
        palette="Dark base + one electric accent",
        type_pairing="Heavy condensed headings",
        leading_section="Bold motion-y hero",
        primary_cta="Start free trial / class",
        imagery="Action shots, space, community"),
    "contractor": dict(
        match=("contractor", "remodel", "handyman", "paint", "construction",
               "flooring", "kitchen", "bath"),
        mood="Solid, proven",
        palette="Neutral + one confident accent",
        type_pairing="Sturdy sans",
        leading_section="Before/after or portfolio-led hero",
        primary_cta="Get an estimate",
        imagery="Project galleries, before/after"),
    "service": dict(
        match=("clean", "moving", "pest", "junk", "storage", "laundry"),
        mood="Friendly, reliable, quick",
        palette="Bright, clean, one strong accent",
        type_pairing="Approachable sans",
        leading_section="Hero + simple 3-step 'how it works'",
        primary_cta="Get a quote / book",
        imagery="Crews, results, happy-home shots"),
}
FALLBACK_BRIEF = dict(
    match=(),
    mood="Modern, professional, warm",
    palette="One confident brand color + neutrals",
    type_pairing="Clean sans pairing",
    leading_section="Hero with clear value prop",
    primary_cta="Contact / call",
    imagery="Best available real photo")

# --- Principle 3: layout archetypes ---------------------------------------
ARCHETYPES = [
    "SPLIT HERO — headline/CTA on one side, image on the other; content sections below",
    "FULL-BLEED IMAGE HERO — edge-to-edge photo with text overlaid; content below",
    "PHOTO-GRID LED — compact hero, then a strong gallery/portfolio grid high on the page",
    "MENU/SERVICES-FORWARD — minimal hero; the services/menu list is the first real block",
]

RUBRIC = """- Bespoke, not templated: looks designed for THIS business (most important)
- Hero lands: a real marketing HEADLINE + one strong CTA over imagery in the first
  screen — NOT an app-store-style card (stacked name + description + star-rating badge)
- Not basic: premium feel, whitespace, clear type scale, section variety (not a plain
  centered text stack)
- Real & specific: real name, services, review quotes, contact; zero invented facts
- Imagery: a real, good photo present and well-placed (not a gradient default)
- Visual craft: balanced spacing, consistent type scale, sufficient contrast
- Beats their current site: visibly fixes the recorded weakness
- Mobile: responsive, no horizontal scroll, tap targets big enough"""


def niche_brief(category: str) -> dict:
    cat = (category or "").lower()
    for brief in NICHE_BRIEFS.values():
        if any(m in cat for m in brief["match"]):
            return brief
    return FALLBACK_BRIEF


def slugify(lead) -> str:
    city = ""
    m = re.search(r",\s*([A-Za-z .]+),\s*[A-Z]{2}", lead["address"] or "")
    if m:
        city = m.group(1).strip()
    base = re.sub(r"[^a-z0-9]+", "-", f"{lead['business_name']} {city}".lower()).strip("-")
    return base[:48] or f"lead-{lead['id']}"


def unique_slug(conn, lead) -> str:
    """Readable business/city base + a random unguessable token.

    This makes each sample link effectively private: the page is unlisted
    (no index, bad slugs 404) AND the URL can't be guessed, so only the
    recipient who was sent the exact link — by email or postcard — can open
    it. token_hex(5) = 10 hex chars (~1e12 combinations)."""
    base = slugify(lead)
    for _ in range(5):
        slug = f"{base}-{secrets.token_hex(5)}"
        if db.get_lead_by_slug(conn, slug) is None:
            return slug
    return f"{base}-{secrets.token_hex(8)}"


# --- Principle 1: image ladder --------------------------------------------

def places_photo_urls(conn, photo_refs: list[str], limit: int = 4) -> list[str]:
    """Resolve legacy photo references to public lh3 URIs.

    The photo endpoint 302-redirects to a googleusercontent URL; we capture
    the Location header so the sample HTML never embeds our API key.
    """
    urls = []
    for ref in photo_refs[:limit]:
        if costs.check(conn, "places", costs.PLACES_PHOTO_USD) == "block":
            break
        try:
            r = requests.get(
                "https://maps.googleapis.com/maps/api/place/photo",
                params={"maxwidth": 1600, "photoreference": ref,
                        "key": config.GOOGLE_PLACES_API_KEY},
                allow_redirects=False, timeout=20,
            )
            costs.record(conn, "places", "photo_media", costs.PLACES_PHOTO_USD)
            uri = r.headers.get("Location", "")
            if r.status_code in (301, 302) and uri.startswith("https://"):
                urls.append(uri)
            else:
                log.warning("photo resolve unexpected status %s", r.status_code)
        except requests.RequestException:
            log.warning("photo resolve failed")
    return urls


def site_images(website: str) -> list[str]:
    if not website:
        return []
    try:
        r = requests.get(website, timeout=12,
                         headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "lxml")
        out = []
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            out.append(requests.compat.urljoin(r.url, og["content"]))
        for img in soup.find_all("img", src=True)[:10]:
            src = requests.compat.urljoin(r.url, img["src"])
            if src.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                out.append(src)
        return out[:4]
    except requests.RequestException:
        return []


def select_images(conn, lead, profile: dict) -> tuple[list[str], str]:
    """Image source ladder; returns (urls, image_source)."""
    photos = places_photo_urls(conn, profile.get("photo_names", []))
    if photos:
        return photos, "places"
    if lead["qualify_status"] == "OUTDATED":
        imgs = site_images(lead["existing_website"])
        if imgs:
            return imgs, "their_site"
    # No stock API keys configured -> typographic hero is the honest last resort
    return [], "typographic"


# --- Principles 4-5: prompt + multi-pass -----------------------------------

def _claude():
    import anthropic
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _call_claude(conn, system: str, user: str, lead_id: int,
                 operation: str) -> str | None:
    if costs.check(conn, "claude", EST_GEN_USD) == "block":
        log.warning("claude budget blocked; %s skipped", operation)
        return None
    client = _claude()
    resp = client.messages.create(
        model=config.MODEL_QUALITY,
        max_tokens=GEN_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    usage = resp.usage
    costs.record(conn, "claude", operation,
                 costs.claude_cost(config.MODEL_QUALITY, usage.input_tokens,
                                   usage.output_tokens),
                 lead_id=lead_id, tokens_in=usage.input_tokens,
                 tokens_out=usage.output_tokens)
    conn.commit()
    return resp.content[0].text


def build_prompt(lead, brief: dict, archetype: str, images: list[str],
                 profile: dict) -> str:
    image_block = "\n".join(
        f"  {i+1}. {url}  (role: {'hero' if i == 0 else 'supporting'})"
        for i, url in enumerate(images)
    ) or ("  none available — use a strong TYPOGRAPHIC hero on a solid or subtle "
          "gradient background. Never use a broken <img> or an invented URL.")
    business = {
        "name": lead["business_name"], "category": lead["category"],
        "phone": lead["phone"], "address": lead["address"],
        "rating": lead["rating"], "review_count": lead["review_count"],
        "hours": profile.get("hours"), "summary": profile.get("summary"),
        "reviews": profile.get("reviews"),
    }
    return f"""You are an expert web designer creating a sample site to win this local business as a client.
Generate ONE complete, single-file, responsive HTML page (inline CSS, minimal/no JS), mobile-first.

DESIGN DIRECTION (niche category: {lead['category']}):
  Mood: {brief['mood']}
  Palette direction: {brief['palette']}      (choose specific values in this direction)
  Type pairing: {brief['type_pairing']} (system fonts or ONE Google Font)
  Lead with: {brief['leading_section']}
  Primary CTA: {brief['primary_cta']}
  Imagery style: {brief['imagery']}
LAYOUT ARCHETYPE (commit to this structure): {archetype}
IMAGES TO USE (real, already selected — place them well; do not invent others):
{image_block}
THEIR CURRENT SITE'S WEAKNESS: {lead['qualify_reason'] or 'no web presence at all'}
  -> This sample must visibly FIX that weakness.

HERO (get this right — it's the first impression): lead with a strong, specific
marketing HEADLINE — a benefit or hook written for THIS business — set over a large
hero image, with the primary CTA nearby. Do NOT open with an "app-store card": a
stacked business name + one-line description + a star-rating badge. That layout reads
generic and templated. Star ratings and review counts belong in the testimonials
section further down, never as a hero badge.
CRAFT (avoid "basic"): make it feel premium and designed, not a plain vertical stack of
centered text blocks. Use generous whitespace, a clear type scale (large confident
headings, comfortable body), real visual variety between sections (alternating layouts,
an image band, a stats or services grid), and thoughtful detail. Aim for "a designer
made this for me," not "a competent template."

Required sections: a hero (per HERO above), services, why-choose-us,
testimonials pulled ONLY from the provided real reviews (lightly cleaned, attributed
"— Google review"), hours/location, and a clear contact CTA using their real phone/address.
Include <meta name="viewport">. Semantic HTML, alt text, sufficient contrast.
No lorem ipsum. No placeholder text. No fixed widths wider than the viewport.
A tiny tasteful footer line "Sample design" is acceptable; never plaster SAMPLE across the page.

Use ONLY the real details below — never invent services, awards, or testimonials.

BUSINESS PROFILE:
{json.dumps(business, indent=2)}

Return only the HTML, nothing else."""


def critique(conn, lead, html: str) -> tuple[bool, str]:
    """Pass 2 text-only self-critique against the rubric (Principle 6, Phase B)."""
    prompt = f"""Review this generated sample website HTML against the rubric. Be harsh.

RUBRIC (score each 1-5):
{RUBRIC}

THE BUSINESS: {lead['business_name']} ({lead['category']})
THE WEAKNESS THIS MUST FIX: {lead['qualify_reason'] or 'no web presence'}

Return STRICT JSON only: {{"verdict": "PASS" or "FAIL", "scores": {{...}}, "fixes": ["specific fix", ...]}}
FAIL if any of bespoke / hero / mobile / imagery scores below 4.

HTML:
{html[:60000]}"""
    raw = _call_claude(conn, "You are a strict design reviewer.", prompt,
                       lead["id"], "sample_critique")
    if raw is None:
        return True, ""  # budget-blocked: don't fail the sample over the critique
    try:
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        return data.get("verdict") == "PASS", "; ".join(data.get("fixes", []))
    except (AttributeError, json.JSONDecodeError):
        log.warning("critique returned unparseable JSON; passing by default")
        return True, ""


def structural_gate(html: str, lead, images: list[str]) -> str | None:
    """Doc 01 STEP 3 hard-defect gate. Returns None if OK, else the failure."""
    lower = html.lower()
    if lead["business_name"].lower() not in lower:
        return "business name missing"
    if "lorem ipsum" in lower or "placeholder" in lower:
        return "placeholder text present"
    if "<meta name=\"viewport\"" not in lower and "<meta name='viewport'" not in lower:
        return "no viewport meta"
    for section in ("hero", "service", "contact"):
        if section not in lower:
            return f"missing {section} section"
    srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)', html, re.I)
    for src in srcs:
        if src.startswith("data:"):
            continue
        if not any(src.startswith(u[:40]) or src == u for u in images):
            return f"invented image URL: {src[:80]}"
    if images and not srcs:
        return "good real photo available but page fell back to no images"
    try:
        BeautifulSoup(html, "lxml")
    except Exception:
        return "HTML does not parse"
    return None


def extract_html(raw: str) -> str:
    m = re.search(r"```(?:html)?\s*(.*?)```", raw, re.S)
    text = m.group(1) if m else raw
    start = text.find("<!DOCTYPE")
    if start == -1:
        start = text.find("<html")
    return text[start:].strip() if start != -1 else text.strip()


def build_one(conn, lead) -> bool:
    profile = json.loads(lead["source_profile"] or "{}")
    brief = niche_brief(lead["category"])
    archetype = ARCHETYPES[lead["id"] % len(ARCHETYPES)]
    images, image_source = select_images(conn, lead, profile)

    prompt = build_prompt(lead, brief, archetype, images, profile)
    html, failure = None, "no attempts made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = _call_claude(conn, "You are an expert web designer.", prompt,
                           lead["id"], f"sample_generate_a{attempt}")
        if raw is None:
            failure = "claude budget blocked"
            break
        candidate = extract_html(raw)
        failure = structural_gate(candidate, lead, images)
        if failure:
            log.info("lead %s attempt %s failed gate: %s", lead["id"], attempt, failure)
            prompt += f"\n\nPREVIOUS ATTEMPT FAILED THE QUALITY GATE: {failure}. Fix that."
            continue
        if attempt == 1:
            ok, fixes = critique(conn, lead, candidate)
            if not ok and fixes:
                log.info("lead %s critique fixes: %s", lead["id"], fixes[:200])
                prompt += f"\n\nA design review of your previous attempt required these fixes — apply them all:\n{fixes}"
                html = candidate  # keep as fallback if the refine pass regresses
                continue
        html = candidate
        failure = None
        break

    if html is None:
        db.transition(conn, lead["id"], "SAMPLE_FAILED", failure or "unknown")
        conn.commit()
        return False

    slug = unique_slug(conn, lead)
    out_dir = config.SAMPLES_CACHE_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    url = f"{config.SAMPLE_BASE_URL}/{slug}"
    notes = None
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            notes = f"url_unverified ({r.status_code})"
    except requests.RequestException:
        notes = "url_unverified (tunnel unreachable at build time)"
    if notes:
        log.warning("lead %s: %s — check serve_samples + tunnel", lead["id"], notes)

    db.transition(conn, lead["id"], "SAMPLE_BUILT", f"images={image_source}",
                  sample_url=url, sample_slug=slug, sample_built_at=db.now(),
                  image_source=image_source,
                  notes=notes if notes else lead["notes"])
    conn.commit()
    return True


def build_samples(limit: int = 10) -> dict:
    """The hourly build_samples job."""
    stats = {"built": 0, "failed": 0}
    with db.connect() as conn:
        for lead in db.get_leads_by_status(conn, "QUALIFIED", limit=limit):
            try:
                ok = build_one(conn, lead)
                stats["built" if ok else "failed"] += 1
            except Exception:
                log.exception("build_one crashed for lead %s", lead["id"])
                stats["failed"] += 1
    log.info("build_samples done: %s", stats)
    return stats


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    print(build_samples(n))
