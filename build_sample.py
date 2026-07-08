"""Stage 2 — build a tailored sample site per QUALIFIED lead (doc 01).

Quality engine per sample-design-system.md: image ladder, per-niche art
direction, layout archetypes, gap-fixing, multi-pass generate -> critique.
Publishing = writing samples_cache/<slug>/index.html for serve_samples.py.
"""
import json
import logging
import re
import secrets
from collections import Counter
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
FACT_CHECK_USD = 0.03     # cheap Haiku grounding pass; real cost ~0.005

# --- Principle 2: per-niche art direction ---------------------------------
NICHE_BRIEFS = {
    "trades": dict(
        match=("plumb", "hvac", "electric", "roof", "heating", "air_condition"),
        mood="Trust + urgency",
        palette="High-contrast; deep blue or navy + a warm accent (red/orange)",
        type_pairing="Bold geometric sans (headings) + clean sans (body)",
        leading_section="Hero with giant click-to-call + '24/7 emergency' if applicable",
        primary_cta="Tap-to-call phone",
        imagery="Trucks, crews at work, before/after",
        stock_query="home service technician at work"),
    "outdoor": dict(
        match=("landscap", "lawn", "tree", "pool", "garden"),
        mood="Fresh, outdoorsy, capable",
        palette="Greens + earthy neutrals",
        type_pairing="Friendly sans; slightly rounded",
        leading_section="Full-bleed photo hero of finished work",
        primary_cta="Get a free quote",
        imagery="Lush finished projects, wide shots",
        stock_query="landscaped garden lawn"),
    "auto": dict(
        match=("auto", "car_repair", "detail", "body_shop", "tire", "mechanic"),
        mood="Rugged, dependable",
        palette="Charcoal/black + a single bold accent",
        type_pairing="Industrial/condensed headings + plain sans",
        leading_section="Hero + services grid",
        primary_cta="Book service / call",
        imagery="Shop, cars, work in progress",
        stock_query="auto repair shop mechanic"),
    "beauty": dict(
        match=("salon", "spa", "barber", "aesthet", "beauty", "nail", "med_spa",
               "medical_spa", "skin"),
        mood="Elegant, airy, aspirational",
        palette="Soft neutrals, blush/sage, lots of whitespace",
        type_pairing="Serif display + light sans",
        leading_section="Imagery-forward hero, minimal text",
        primary_cta="Book appointment",
        imagery="Interiors, results, clean product shots",
        stock_query="spa treatment room interior"),
    "food": dict(
        match=("restaurant", "cafe", "bakery", "bar", "pizza", "food", "coffee",
               "diner", "grill"),
        mood="Warm, appetite-driven",
        palette="Warm tones matched to cuisine",
        type_pairing="Characterful display + readable body",
        leading_section="Big food hero, then menu",
        primary_cta="View menu / order / reserve",
        imagery="Food close-ups, interior ambiance",
        stock_query="restaurant food interior"),
    "professional": dict(
        match=("law", "attorney", "account", "insurance", "financ", "tax",
               "bookkeep", "notary"),
        mood="Authoritative, restrained",
        palette="Navy/charcoal + subtle gold or slate",
        type_pairing="Classic serif or refined sans",
        leading_section="Hero with credibility line + credentials",
        primary_cta="Free consultation",
        imagery="Office, headshots, city skyline",
        stock_query="modern professional office"),
    "medical": dict(
        match=("dent", "orthodont", "medical", "chiro", "vet", "clinic",
               "physical_therap", "doctor", "wellness"),
        mood="Clean, reassuring, modern",
        palette="Calm blues/teals + white",
        type_pairing="Rounded, approachable sans",
        leading_section="Hero + trust markers (insurance, new-patient)",
        primary_cta="Request appointment",
        imagery="Clean office, friendly staff, patients",
        stock_query="modern medical clinic"),
    "fitness": dict(
        match=("gym", "fitness", "yoga", "martial", "crossfit", "pilates",
               "studio"),
        mood="High-energy, bold",
        palette="Dark base + one electric accent",
        type_pairing="Heavy condensed headings",
        leading_section="Bold motion-y hero",
        primary_cta="Start free trial / class",
        imagery="Action shots, space, community",
        stock_query="gym fitness workout"),
    "contractor": dict(
        match=("contractor", "remodel", "handyman", "paint", "construction",
               "flooring", "kitchen", "bath"),
        mood="Solid, proven",
        palette="Neutral + one confident accent",
        type_pairing="Sturdy sans",
        leading_section="Before/after or portfolio-led hero",
        primary_cta="Get an estimate",
        imagery="Project galleries, before/after",
        stock_query="home renovation construction"),
    "service": dict(
        match=("clean", "moving", "pest", "junk", "storage", "laundry"),
        mood="Friendly, reliable, quick",
        palette="Bright, clean, one strong accent",
        type_pairing="Approachable sans",
        leading_section="Hero + simple 3-step 'how it works'",
        primary_cta="Get a quote / book",
        imagery="Crews, results, happy-home shots",
        stock_query="professional home service"),
}
FALLBACK_BRIEF = dict(
    match=(),
    mood="Modern, professional, warm",
    palette="One confident brand color + neutrals",
    type_pairing="Clean sans pairing",
    leading_section="Hero with clear value prop",
    primary_cta="Contact / call",
    imagery="Best available real photo",
    stock_query="modern small business")

# --- Principle 3: layout archetypes ---------------------------------------
ARCHETYPES = [
    "SPLIT HERO — headline/CTA on one side, image on the other; content sections below",
    "FULL-BLEED IMAGE HERO — edge-to-edge photo with text overlaid; content below",
    "PHOTO-GRID LED — compact hero, then a strong gallery/portfolio grid high on the page",
    "MENU/SERVICES-FORWARD — minimal hero; the services/menu list is the first real block",
]

RUBRIC = """- Bespoke, not templated: looks designed for THIS business, and USES their real
  brand (logo + colors) when provided — not a generic per-niche palette (most important)
- Hero lands: a real marketing HEADLINE + one strong CTA over imagery in the first
  screen — NOT an app-store-style card (stacked name + description + star-rating badge)
- Not basic: premium feel, whitespace, clear type scale, section variety (not a plain
  centered text stack)
- Real & specific: real name, services, review quotes, contact; zero invented facts
- Imagery: a real, good photo present and well-placed (not a gradient default)
- Visual craft: balanced spacing, consistent type scale, sufficient contrast
- Beats their current site WITHOUT A DOUBT: a clear, major upgrade — if it only reads
  as comparable or marginally better than a dated small-business site, this scores low
- Mobile: responsive, no horizontal scroll, tap targets big enough"""


def niche_key(category: str, name: str = "") -> str:
    # category first (authoritative when specific); the business NAME is the
    # fallback for Places' generic types ("establishment", "point_of_interest")
    # — e.g. category=establishment, name="Nursing Aesthetic Institute" → beauty
    for text in ((category or "").lower(), (name or "").lower()):
        for key, brief in NICHE_BRIEFS.items():
            if any(m in text for m in brief["match"]):
                return key
    return "fallback"


def niche_brief(category: str, name: str = "") -> dict:
    return NICHE_BRIEFS.get(niche_key(category, name), FALLBACK_BRIEF)


def _style_context(key: str) -> str:
    """Feedback-loop text injected into the generator's system prompt:
    a distilled style crib from a top-graded sample of this niche
    (exemplars/<niche>.md, written by `run.py exemplar <id>`) plus curated
    lessons from hand-grading (exemplars/LESSONS.md). Both optional; the
    system prompt is cached, so across a same-niche batch this text is
    read at ~10% of the input price."""
    parts = []
    crib = config.EXEMPLARS_DIR / f"{key}.md"
    if crib.is_file():
        parts.append(
            "STYLE EXEMPLAR — distilled from a previously TOP-GRADED sample for "
            "another business in this niche. Match this LEVEL of craft and these "
            "techniques; never copy its content, facts, or brand colors:\n"
            + crib.read_text(encoding="utf-8").strip())
    lessons = config.EXEMPLARS_DIR / "LESSONS.md"
    if lessons.is_file():
        parts.append(
            "LESSONS FROM HAND-GRADED SAMPLES — recurring flaws and wins from "
            "human review of past output. Apply every relevant one:\n"
            + lessons.read_text(encoding="utf-8").strip())
    return "\n\n".join(parts)


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

def _photo_rank(p: dict) -> int:
    """Lower = better HERO candidate. Landscape and wide wins; portrait and
    near-square (usually a logo or profile shot) sink to the bottom."""
    w, h = p.get("w") or 0, p.get("h") or 0
    if not w or not h:
        return 2  # legacy lead with no stored dims — keep, but after known-good
    if abs(w - h) <= min(w, h) * 0.12:
        return 4  # near-square: logo-shaped, never lead with it
    if h > w:
        return 3  # portrait: poor hero crop
    return 0 if w >= 1200 else 1


def rank_photos(profile: dict) -> list[dict]:
    """Normalize both profile shapes (legacy `photo_names` strings, new
    `photos` dicts with w/h) into hero-first order, dropping under-sized ones."""
    raw = profile.get("photos")
    if raw is None:
        raw = [{"ref": r} for r in profile.get("photo_names", [])]
    good = [p for p in raw if p.get("ref")
            and (not (p.get("w") or p.get("h"))
                 or max(p.get("w") or 0, p.get("h") or 0) >= 800)]
    return sorted(good, key=_photo_rank)


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


def stock_images(lead, brief: dict, limit: int = 8) -> list[str]:
    """Ladder step 3 — free niche stock (Pexels, then Unsplash). Both APIs are
    $0; a clean stock photo beats a typographic hero every time. Returns
    CANDIDATES (up to `limit`) — the generator picks the few that fit the
    palette/mood, so more choices = better odds of an on-brand hero. Returns []
    when no key is configured or nothing landscape/large comes back."""
    query = brief.get("stock_query") or "modern small business"
    if config.PEXELS_API_KEY:
        try:
            r = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": 12,
                        "orientation": "landscape", "size": "large"},
                headers={"Authorization": config.PEXELS_API_KEY}, timeout=15)
            if r.status_code == 200:
                urls = [p["src"]["large2x"] for p in r.json().get("photos", [])
                        if p.get("width", 0) >= 1200 and p.get("src", {}).get("large2x")]
                if urls:
                    return urls[:limit]
                log.warning("pexels: 0 usable results for %r", query)
            else:
                log.warning("pexels HTTP %s for %r: %s", r.status_code, query,
                            r.text[:120])
        except requests.RequestException:
            log.warning("pexels search failed for %r", query)
    if config.UNSPLASH_ACCESS_KEY:
        try:
            r = requests.get(
                "https://api.unsplash.com/search/photos",
                params={"query": query, "per_page": 8, "orientation": "landscape",
                        "client_id": config.UNSPLASH_ACCESS_KEY}, timeout=15)
            if r.status_code == 200:
                urls = [p["urls"]["regular"] for p in r.json().get("results", [])
                        if p.get("urls", {}).get("regular")]
                if urls:
                    return urls[:limit]
        except requests.RequestException:
            log.warning("unsplash search failed for %r", query)
    return []


def select_images(conn, lead, profile: dict, brief: dict) -> tuple[list[str], str]:
    """Image source ladder; returns (urls, image_source)."""
    ranked = rank_photos(profile)
    # never LEAD with a logo-shaped/portrait photo — if that's all they have,
    # prefer clean stock for the hero (doc: "clean stock beats a real ugly one")
    hero_worthy = [p for p in ranked if _photo_rank(p) <= 2]
    if hero_worthy:
        photos = places_photo_urls(conn, [p["ref"] for p in ranked])
        if photos:
            return photos, "places"
    if lead["qualify_status"] == "OUTDATED":
        imgs = site_images(lead["existing_website"])
        if imgs:
            return imgs, "their_site"
    stock = stock_images(lead, brief)
    if stock:
        return stock, "stock"
    if ranked:  # only non-hero-worthy real photos exist; still beat a gradient
        photos = places_photo_urls(conn, [p["ref"] for p in ranked])
        if photos:
            return photos, "places"
    return [], "typographic"


def _norm_hex(h: str) -> str:
    h = h.lower().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


def _is_neutral(h: str) -> bool:
    """Near-gray/black/white — not a brand accent color."""
    r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    return max(r, g, b) - min(r, g, b) < 24


def extract_brand(website: str) -> dict:
    """Pull the business's REAL brand from their existing site — logo, accent
    colors, fonts — so the sample looks like THEIRS, not a generic template.
    Returns {} when nothing usable (no site / parked domain / fetch fails)."""
    if not website:
        return {}
    try:
        r = requests.get(website, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException:
        return {}
    if r.status_code >= 400 or not r.text:
        return {}
    soup = BeautifulSoup(r.text, "lxml")
    base, brand = r.url, {}

    # logo: an <img> that looks like a logo, else apple-touch-icon / og:image
    for img in soup.find_all("img", src=True):
        cls = " ".join(img.get("class") or [])
        hay = f"{img.get('alt','')} {cls} {img.get('id','')} {img.get('src','')}".lower()
        if "logo" in hay:
            brand["logo"] = requests.compat.urljoin(base, img["src"])
            break
    if "logo" not in brand:
        icon = soup.find("link", rel=lambda v: v and "apple-touch-icon" in v)
        og = soup.find("meta", property="og:image")
        if icon and icon.get("href"):
            brand["logo"] = requests.compat.urljoin(base, icon["href"])
        elif og and og.get("content"):
            brand["logo"] = requests.compat.urljoin(base, og["content"])

    # colors: theme-color + hex codes across <style>, inline styles, up to 2 CSS files
    css = " ".join(s.get_text() for s in soup.find_all("style"))
    css += " " + " ".join(t.get("style", "") for t in soup.find_all(style=True))
    tc = soup.find("meta", attrs={"name": "theme-color"})
    theme = (tc.get("content", "") if tc else "").strip()
    for link in soup.find_all("link", rel=lambda v: v and "stylesheet" in v,
                              href=True)[:2]:
        try:
            cr = requests.get(requests.compat.urljoin(base, link["href"]),
                              timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if cr.status_code < 400:
                css += " " + cr.text[:200_000]
        except requests.RequestException:
            pass
    counts = Counter(_norm_hex(h) for h in
                     re.findall(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b",
                                theme + " " + css))
    colors = [h for h, _ in counts.most_common(30) if not _is_neutral(h)][:4]
    if theme.startswith("#"):
        t = _norm_hex(theme)
        colors = [t] + [c for c in colors if c != t]
    if colors:
        brand["colors"] = colors[:4]

    # fonts: Google Fonts families + font-family declarations
    fonts = []
    for link in soup.find_all("link", href=True):
        m = re.search(r"fonts\.googleapis\.com/css2?\?family=([^:&\"']+)",
                      link["href"])
        if m:
            fonts.append(m.group(1).replace("+", " "))
    for f in re.findall(r"font-family:\s*([^;}{\"']+)", css, re.I)[:10]:
        first = re.sub(r"\s*!important\s*$", "",
                       f.split(",")[0].strip().strip("'\""), flags=re.I).strip()
        low = first.lower()
        if (first and low not in (
                "inherit", "initial", "unset", "none", "sans-serif", "serif",
                "monospace", "system-ui", "-apple-system", "var")
                and not any(bad in low for bad in ("icon", "awesome", "glyph"))
                and first not in fonts):
            fonts.append(first)
    if fonts:
        brand["fonts"] = fonts[:3]

    return brand


# --- Principles 4-5: prompt + multi-pass -----------------------------------

def _claude():
    import anthropic
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _call_claude(conn, system: str, user, lead_id: int,
                 operation: str) -> str | None:
    """`user` is a string, or (base_prompt, feedback_str) — the base prompt gets a
    cache breakpoint so gate/critique retries re-read it at ~10% of input price
    (the retry always lands well inside the 5-min cache TTL)."""
    if costs.check(conn, "claude", EST_GEN_USD) == "block":
        log.warning("claude budget blocked; %s skipped", operation)
        return None
    if isinstance(user, tuple):
        base, feedback = user
        content = [{"type": "text", "text": base,
                    "cache_control": {"type": "ephemeral"}}]
        if feedback:
            content.append({"type": "text", "text": feedback})
    else:
        content = user
    client = _claude()
    resp = client.messages.create(
        model=config.MODEL_QUALITY,
        max_tokens=GEN_MAX_TOKENS,
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
    )
    usage = resp.usage
    costs.record(conn, "claude", operation,
                 costs.claude_cost(config.MODEL_QUALITY, usage.input_tokens,
                                   usage.output_tokens,
                                   cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                                   cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0),
                 lead_id=lead_id, tokens_in=usage.input_tokens,
                 tokens_out=usage.output_tokens)
    conn.commit()
    return resp.content[0].text


def build_prompt(lead, brief: dict, archetype: str, images: list[str],
                 profile: dict, brand: dict, image_source: str = "") -> str:
    if images and image_source == "stock":
        image_block = "\n".join(f"  {i+1}. {url}" for i, url in enumerate(images))
        image_block += (
            "\n  These are stock CANDIDATES — CHOOSE the 1-3 whose lighting, tones and "
            "subject genuinely fit the palette/mood above and use ONLY those (the single "
            "best one as hero). SKIP any that look cluttered, garish, cheap, or off-palette "
            "— a page with one perfect photo beats a page with three mediocre ones."
            "\n  NOTE: these are licensed stock photos matching their industry — NOT "
            "this business's own premises/staff/work. Use them as atmosphere; never "
            "caption or imply they depict this specific business.")
    else:
        image_block = "\n".join(
            f"  {i+1}. {url}  (role: {'hero' if i == 0 else 'supporting'})"
            for i, url in enumerate(images)
        ) or ("  none available — use a strong TYPOGRAPHIC hero on a solid or subtle "
              "gradient background. Never use a broken <img> or an invented URL.")
    if brand:
        parts = []
        if brand.get("logo"):
            parts.append(f"  Their real logo — place it in the header/nav: {brand['logo']}")
        if brand.get("colors"):
            parts.append("  Their brand colors — build the ENTIRE palette around these, "
                         f"do NOT override with generic niche defaults: {', '.join(brand['colors'])}")
        if brand.get("fonts"):
            parts.append("  Their fonts — use these (or the closest Google Font): "
                         f"{', '.join(brand['fonts'])}")
        brand_block = ("THEIR EXISTING BRAND (highest priority — the sample must read as "
                       "THIS business's own site, not a template):\n" + "\n".join(parts))
    else:
        brand_block = ("THEIR BRAND: no usable existing brand (no real site). INVENT a "
                       "distinctive identity for THIS specific business — a palette and type "
                       "system that feel chosen for them by name/personality, never a generic "
                       "niche default.")
    if lead["qualify_status"] == "OUTDATED":
        upgrade_mandate = (
            "WITHOUT-A-DOUBT UPGRADE: they ALREADY have a website, so this sample must be "
            "so obviously better that the owner sees an unmistakable leap — not a lateral "
            "move. Modern responsive layout, a commanding hero, confident type scale, real "
            "polish and speed. Keep their brand identity (logo/colors above) but execute it "
            "at a dramatically higher level. A result a reasonable person could call merely "
            "comparable or only slightly better than a dated small-business site is a FAILURE.")
    else:
        upgrade_mandate = (
            "ESTABLISH THEIR PRESENCE: they have no real website today. This is their first "
            "real web presence — make it genuinely impressive and credible, the kind of site "
            "that makes this business look established and trustworthy at a glance.")
    hours = profile.get("hours") or []
    open_days = sum(1 for h in hours if "closed" not in h.lower())
    # Only 4-5★ reviews may be quoted — never launder a positive fragment out of a
    # negative review (that misrepresents an unhappy customer and reads as dishonest
    # to an owner who knows their own reviews).
    quotable = [r for r in (profile.get("reviews") or [])
                if (r.get("rating") or 0) >= 4 and r.get("text")]
    business = {
        "name": lead["business_name"], "category": lead["category"],
        "phone": lead["phone"], "address": lead["address"],
        "rating": lead["rating"], "review_count": lead["review_count"],
        "hours": hours, "open_days_per_week": open_days,
        "summary": profile.get("summary"),
        "quotable_reviews_4_5_star_only": quotable,
    }
    return f"""You are an expert web designer creating a sample site to win this local business as a client.
Generate ONE complete, single-file, responsive HTML page (inline CSS, minimal/no JS), mobile-first.

DESIGN DIRECTION (niche category: {lead['category']}):
  Mood: {brief['mood']}
  Palette direction: {brief['palette']} — BUT if THEIR BRAND below gives colors, THOSE win
  Type pairing: {brief['type_pairing']} — BUT if THEIR BRAND below gives fonts, use those
  Lead with: {brief['leading_section']}
  Primary CTA: {brief['primary_cta']}
  Imagery style: {brief['imagery']}
LAYOUT ARCHETYPE (commit to this structure): {archetype}
IMAGES TO USE (real, already selected — place them well; do not invent others):
{image_block}
THEIR CURRENT SITE'S WEAKNESS: {lead['qualify_reason'] or 'no web presence at all'}
  -> This sample must visibly FIX that weakness.
{brand_block}
{upgrade_mandate}

HERO (get this right — it's the first impression): lead with a strong, specific
marketing HEADLINE — a benefit or hook written for THIS business — set over a large
hero image, with the primary CTA nearby. Do NOT open with an "app-store card": a
stacked business name + one-line description + a star-rating badge. That layout reads
generic and templated. Star ratings and review counts belong in the testimonials
section further down, never as a hero badge. The headline MUST scale responsively
(use CSS clamp() for its font-size) and wrap cleanly — never a fixed size that
overflows, clips, collides with the CTA, or "smooshes" on a phone-width screen.
TEXT OVER PHOTOS: any text sitting on a photo MUST have a dark scrim or gradient
overlay behind it (e.g. a rgba(0,0,0,.35–.55) layer or bottom-up gradient) so every
word reads clearly — light text straight onto a busy or bright photo is a failure.
HEADER AT PHONE WIDTH: at 390px the logo/site name, location line, and any header
CTA must never overlap, collide, or clip — shrink type, wrap, or drop the CTA to
its own row. A broken mobile header kills the whole pitch.
CRAFT (avoid "basic"): make it feel premium and designed, not a plain vertical stack of
centered text blocks. Use generous whitespace, a clear type scale (large confident
headings, comfortable body), real visual variety between sections (alternating layouts,
an image band, a stats or services grid), and thoughtful detail. Aim for "a designer
made this for me," not "a competent template."

Required sections: a hero (per HERO above), services, why-choose-us,
testimonials pulled ONLY from the provided 4-5★ reviews (lightly cleaned, attributed
"— Google review"), hours/location, and a clear contact CTA using their real phone/address.
Include <meta name="viewport">. Semantic HTML, alt text, sufficient contrast.
No lorem ipsum. No placeholder text. No fixed widths wider than the viewport.
A tiny tasteful footer line "Sample design" is acceptable; never plaster SAMPLE across the page.

STRICT HONESTY — this is what wins or loses the client. One invented fact the owner
spots destroys all trust in the pitch, so treat this as harder than any design rule:
- State ONLY facts present in the BUSINESS PROFILE below. If a section needs more
  words, use benefit/experience language that asserts NO new fact — never fill a gap
  with a plausible-sounding invented detail.
- NO superlatives or rankings you cannot source from the profile: no "best", "#1",
  "top-rated", "voted", "award-winning", "leading", "premier", "world-class".
- NO invented amenities or logistics: parking, refreshments, insurance, financing,
  board certifications, years in business, staff counts, service areas — unless they
  appear in the profile.
- Testimonials: quote ONLY from quotable_reviews_4_5_star_only below. Light cleanup
  only — never append, embellish, or invent words the reviewer did not write, and
  never stitch a quote from more than one review.
- Shortening a quote: use ONE contiguous excerpt, trimming only whole sentences from
  the start or end — never delete words or sentences from the middle. NEVER trim away
  context that changes who the reviewer appears to be (e.g. a fellow professional or
  trainee reading as a patient). If a review can't be excerpted honestly, quote it in
  full or use a different one.
- Days / hours: the business is open EXACTLY {open_days} day(s) per week. If you state
  a "days per week" figure, use that number verbatim; render the hours exactly as
  listed. Do NOT count or infer your own day total.

BUSINESS PROFILE:
{json.dumps(business, indent=2)}

Return only the HTML, nothing else."""


CRITIQUE_KEYS = ("bespoke", "hero", "craft", "real", "imagery", "beats", "mobile")


def critique(conn, lead, html: str, image_source: str = "") -> tuple[bool, str, bool]:
    """Pass 2 text-only self-critique against the rubric (Principle 6, Phase B).

    Returns (passed, fixes, weak). `weak` means it passed but some core factor
    scored a 4 — "fine, forgettable" territory — so one polish pass is worth it."""
    imagery_note = ""
    if image_source == "typographic":
        imagery_note = (
            "\nIMAGE CONSTRAINT: NO photographs exist for this business (no real "
            "photos, no usable stock) and the generator is FORBIDDEN to invent "
            "image URLs. The page is intentionally typographic. Do NOT demand "
            "real photos — score 'imagery' on the craft of the typographic/"
            "graphic treatment instead.")
    prompt = f"""Review this generated sample website HTML against the rubric. Be harsh.

RUBRIC (score each 1-5):
{RUBRIC}

THE BUSINESS: {lead['business_name']} ({lead['category']})
THE WEAKNESS THIS MUST FIX: {lead['qualify_reason'] or 'no web presence'}{imagery_note}

Return STRICT JSON only, scoring EXACTLY these keys:
{{"verdict": "PASS" or "FAIL", "scores": {{"bespoke": n, "hero": n, "craft": n, "real": n, "imagery": n, "beats": n, "mobile": n}}, "fixes": ["specific fix", ...]}}
FAIL if any of bespoke / hero / craft / beats / mobile / imagery scores below 4.
Even on PASS, list the fixes that would lift any 4 to a 5.

HTML:
{html[:60000]}"""
    raw = _call_claude(conn, "You are a strict design reviewer.", prompt,
                       lead["id"], "sample_critique")
    if raw is None:
        return True, "", False  # budget-blocked: don't fail the sample over the critique
    try:
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        scores = data.get("scores") or {}
        core = [int(scores[k]) for k in CRITIQUE_KEYS
                if isinstance(scores.get(k), (int, float))]
        passed = data.get("verdict") == "PASS"
        weak = passed and bool(core) and min(core) <= 4
        return passed, "; ".join(data.get("fixes", [])), weak
    except (AttributeError, ValueError, json.JSONDecodeError):
        log.warning("critique returned unparseable JSON; passing by default")
        return True, "", False


# Claim words that are almost never truthfully sourceable for a small business and
# were seen invented in calibration. Each is allowed ONLY if it also appears in the
# business's own source text (e.g. a real review that says "best place for your face").
BANNED_CLAIMS = (
    "best ", "#1", "number one", "top-rated", "top rated", "voted", "award-winning",
    "award winning", "world-class", "world class", "board-certified",
    "board certified", "free parking", "financing available", "guaranteed",
)


def fact_check(conn, lead, profile: dict, html: str) -> tuple[bool, str]:
    """Grounding pass: verify every factual claim in the page is supported by the
    business's REAL data. Returns (ok, "unsupported claim; unsupported claim").

    This is the check the design `critique()` structurally cannot do — the critique
    never sees the source facts, so invention (fake amenities, superlatives, wrong
    day-counts, laundered quotes) slips past it. Cheap Haiku-class, text-only.
    """
    if not config.ANTHROPIC_API_KEY:
        return True, ""
    if costs.check(conn, "claude", FACT_CHECK_USD) == "block":
        return True, ""  # budget-blocked: don't fail the sample over the fact-check
    hours = profile.get("hours") or []
    facts = {
        "name": lead["business_name"], "category": lead["category"],
        "phone": lead["phone"], "address": lead["address"],
        "rating": lead["rating"], "review_count": lead["review_count"],
        "hours": hours,
        "open_days_per_week": sum(1 for h in hours if "closed" not in h.lower()),
        "summary": profile.get("summary"),
        "reviews_with_ratings": profile.get("reviews"),
    }
    trimmed = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html,
                     flags=re.S | re.I)[:45000]
    prompt = f"""You are fact-checking a sales sample website against the ONLY verified
data about a real business. List every factual claim on the page that is NOT supported
by that data.

FLAG: services/treatments not evidenced in the data; amenities (parking, refreshments,
insurance, financing); credentials or certifications; superlatives/rankings ("best",
"#1", "award-winning", "top-rated") not in the data; a stated days-per-week other than
{facts['open_days_per_week']}; testimonial quotes that are not from a 4-5 star review or
that add/alter words the reviewer wrote.
Testimonial excerpting rule: a quote MAY be a shortened contiguous excerpt of a real
review with whole sentences trimmed from the start or end — do NOT flag that. FLAG a
quote only if it adds/changes words, deletes words or sentences from the MIDDLE,
stitches multiple reviews, or trims context that changes who the reviewer appears to
be (e.g. a fellow professional or trainee reading as a patient).
Do NOT flag generic benefit/marketing language that asserts no specific fact (e.g.
"results that look like you"). Be precise and conservative — only genuine unsupported
factual claims.

VERIFIED DATA:
{json.dumps(facts, indent=2)}

PAGE HTML:
{trimmed}

Return STRICT JSON only: {{"ok": true|false, "unsupported": ["<claim> - <why>", ...]}}"""
    try:
        client = _claude()
        resp = client.messages.create(
            model=config.MODEL_CLASSIFIER, max_tokens=600,
            system="You are a strict, conservative fact-checker.",
            messages=[{"role": "user", "content": prompt}],
        )
        costs.record(conn, "claude", "sample_factcheck",
                     costs.claude_cost(config.MODEL_CLASSIFIER,
                                       resp.usage.input_tokens,
                                       resp.usage.output_tokens),
                     lead_id=lead["id"], tokens_in=resp.usage.input_tokens,
                     tokens_out=resp.usage.output_tokens)
        conn.commit()
        data = json.loads(re.search(r"\{.*\}", resp.content[0].text, re.S).group(0))
        unsupported = [u for u in (data.get("unsupported") or []) if u]
        return (data.get("ok", True) and not unsupported), "; ".join(unsupported)
    except (AttributeError, json.JSONDecodeError):
        log.warning("fact_check unparseable response; passing by default")
        return True, ""
    except Exception:
        log.exception("fact_check failed")
        return True, ""


def structural_gate(html: str, lead, images: list[str],
                    source_text: str = "") -> str | None:
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
    # unsourced superlatives / invented claims (checks alt text + overlays too, since
    # this scans raw HTML — that's where "best in Plano" hid on lead 60)
    src = (source_text or "").lower()
    hits = sorted({b.strip() for b in BANNED_CLAIMS if b in lower and b not in src})
    if hits:
        return "unsourced claim(s): " + ", ".join(hits)
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
    brief = niche_brief(lead["category"], lead["business_name"])
    archetype = ARCHETYPES[lead["id"] % len(ARCHETYPES)]
    images, image_source = select_images(conn, lead, profile, brief)
    brand = extract_brand(lead["existing_website"]) if lead["existing_website"] else {}
    if brand:
        log.info("lead %s brand: colors=%s fonts=%s logo=%s", lead["id"],
                 brand.get("colors"), brand.get("fonts"), bool(brand.get("logo")))
    # the real logo is a legitimate image even if it isn't in the photo `images`
    allowed_images = images + ([brand["logo"]] if brand.get("logo") else [])
    # everything we can truthfully say about them — the claim gate allows a banned
    # word only if it appears here (e.g. a review literally saying "best place")
    source_text = " ".join([
        lead["business_name"] or "", lead["category"] or "",
        profile.get("summary") or "",
        *[r.get("text", "") for r in (profile.get("reviews") or [])],
    ])

    system = "You are an expert web designer."
    style_ctx = _style_context(niche_key(lead["category"], lead["business_name"]))
    if style_ctx:
        system += "\n\n" + style_ctx

    base_prompt = build_prompt(lead, brief, archetype, images, profile, brand,
                               image_source)
    feedback: list[str] = []
    html, failure = None, "no attempts made"
    critiqued = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = _call_claude(conn, system, (base_prompt, "\n\n".join(feedback)),
                           lead["id"], f"sample_generate_a{attempt}")
        if raw is None:
            failure = "claude budget blocked"
            break
        candidate = extract_html(raw)
        failure = structural_gate(candidate, lead, allowed_images, source_text)
        if failure:
            log.info("lead %s attempt %s failed gate: %s", lead["id"], attempt, failure)
            feedback.append(
                f"PREVIOUS ATTEMPT FAILED THE QUALITY GATE: {failure}. Fix that — and "
                "regenerate the COMPLETE page with ALL required sections (hero, "
                "services, why-choose-us, testimonials, hours/location, contact CTA) "
                "and the images; fix only what's named, never drop anything else.")
            continue
        # grounding fact-check — reject any invented claim before design polish
        fact_ok, fact_fixes = fact_check(conn, lead, profile, candidate)
        if not fact_ok and fact_fixes:
            log.info("lead %s attempt %s fact-check flagged: %s",
                     lead["id"], attempt, fact_fixes[:200])
            feedback.append(
                "FACT-CHECK — these claims are NOT supported by the business's "
                "real data. Remove or rewrite each so the page states only "
                "verified facts:\n" + fact_fixes +
                "\nRegenerate the COMPLETE page with ALL required sections and "
                "the images — change only the flagged claims, drop nothing else.")
            html = candidate  # fallback if later attempts regress
            continue
        # one critique-driven refine per lead: on FAIL it's mandatory, and a
        # PASS with any core factor at 4 ("fine, forgettable") earns the same
        # single polish pass — the cached base prompt makes the retry cheap
        if not critiqued and attempt < MAX_ATTEMPTS:
            critiqued = True
            ok, fixes, weak = critique(conn, lead, candidate, image_source)
            if fixes and (not ok or weak):
                log.info("lead %s critique (%s): %s", lead["id"],
                         "FAIL" if not ok else "PASS-but-weak", fixes[:200])
                feedback.append(
                    "A design review of your previous attempt required these "
                    "fixes — apply them all:\n" + fixes +
                    "\nRegenerate the COMPLETE page with ALL required sections and "
                    "the images — apply the fixes, drop nothing else.")
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
