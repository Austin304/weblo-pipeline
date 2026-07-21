"""Stage 2 — build a tailored sample site per QUALIFIED lead (doc 01).

Quality engine per sample-design-system.md: image ladder, per-niche art
direction, layout archetypes, gap-fixing, multi-pass generate -> critique.
Publishing = writing samples_cache/<slug>/index.html for serve_samples.py.
"""
import base64
import hashlib
import itertools
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

MAX_ATTEMPTS = 2          # total generation attempts. Facts are now assembled in
                          # code (assemble_content_blocks), so attempt 1 is honest by
                          # construction; attempt 2 is one targeted fix/polish pass.
                          # Full-page regeneration is expensive — cap the churn.
# Calibration: when both attempts leave a residual prose flag, ship the last
# structurally-sound candidate WITH a fact_flagged note (so factor-F grading catches
# it) instead of SAMPLE_FAILED — a dead lead teaches nothing and wastes two builds.
# The factual scaffolding is honest either way (it's code-assembled). Flip to False
# before go-live if you want strict fail-closed on any residual flag.
SHIP_WHEN_FACT_FLAGGED = True
# An owner's OWN photo must score at least this hero-fit (1-5, from the Haiku vision
# QC) to LEAD the page. Our qualifier selects businesses worst at visual self-
# presentation, so a merely-usable (3/5) owner photo as hero is the #1 recurring
# failure — below this bar, owner photos drop behind clean stock. See select_images.
OWNER_HERO_MIN = 4
GEN_MAX_TOKENS = 16000
EST_GEN_USD = 0.45        # worst-case pre-call estimate (doc 08: estimate from max_tokens)
FACT_CHECK_USD = 0.03     # cheap Haiku grounding pass; real cost ~0.005
PHOTO_VISION_USD = 0.02   # cheap Haiku-vision candidate ranking; real cost ~0.01
BON_JUDGE_USD = 0.08      # one best-of-N pairwise vision comparison (2 samples' shots)
# Best-of-N (Pillar 1+2): generate N diverse candidates and ship the pairwise-
# tournament winner. Set in config; 1 = legacy single-shot. See _try_best_of_n.
BEST_OF_N = config.BEST_OF_N

# where the main subject sits -> the CSS object-position that keeps it in frame
# after an object-fit:cover crop (the #1 cause of "photo cropped through a face")
FOCUS_CSS = {"top": "center top", "bottom": "center bottom",
             "left": "left center", "right": "right center", "center": "center"}


def _as_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _alnum(s: str) -> str:
    """Lowercased alphanumeric-only form — for whitespace/punctuation-insensitive
    substring matching (e.g. verifying a fact-check quote is really on the page)."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

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
    "dental": dict(
        match=("dent", "orthodont", "endodont", "periodont"),
        mood="Confident, clean, reassuring",
        palette="Calm blues/teals + white",
        type_pairing="Rounded, approachable sans",
        leading_section="Hero + trust markers (insurance, new-patient)",
        primary_cta="Request appointment",
        imagery="A genuine healthy smile; a professionally-dressed dentist/team; a "
                "warm dentist-with-patient moment; a bright welcoming reception",
        stock_query="happy healthy smile dental patient",
        # clinical service terms ("dental implants") return cold surgical stock, so
        # skip the service-led query and use the curated warm one above
        avoid_service_query=True,
        # their OWN photo only LEADS the page if it's really good (a 5); a merely-good
        # 4 still gets USED in a section (see select_images) — Austin's rule
        hero_lead_min=5,
        mix_sources=True,
        hero_guidance=(
            "This is a DENTAL practice. Best hero: a well-shot, genuine HEALTHY SMILE "
            "(a happy patient or a bright, confident smile). Also strong: a "
            "professionally-dressed, approachable dentist or team; a warm "
            "dentist-with-smiling-patient moment; a bright, welcoming reception. "
            "POOR hero (score hero=1, treat it like a storefront) — the 'surgery "
            "look': masked or gowned faces, gloved hands in a mouth, a dentist "
            "HOLDING A DRILL or using instruments, procedure/operatory close-ups, or "
            "bare clinical equipment. A dentist dressed up is good; a dentist holding "
            "a drill is not.")),
    "medical": dict(
        match=("medical", "chiro", "vet", "clinic",
               "physical_therap", "doctor", "wellness"),
        mood="Clean, reassuring, modern",
        palette="Calm blues/teals + white",
        type_pairing="Rounded, approachable sans",
        leading_section="Hero + trust markers (insurance, new-patient)",
        primary_cta="Request appointment",
        imagery="Warm and patient-facing: friendly staff, happy patients, a bright "
                "welcoming space",
        stock_query="friendly doctor with happy patient",
        avoid_service_query=True,
        hero_guidance=(
            "This is a patient-facing medical practice. Best heroes are WARM and "
            "human: a happy patient, a professionally-dressed, approachable clinician "
            "or team, or a bright welcoming reception. POOR hero (score hero=1) — the "
            "'surgery/procedure look': masked or gowned figures, gloved hands "
            "mid-procedure, instruments/needles, or bare clinical equipment. A "
            "clinician dressed up is good; one mid-procedure is not.")),
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
- Not basic — THE most common failure: "clean but plain" / "a tidy brochure" scores LOW.
  Premium demands dramatic type-scale contrast (oversized display + small uppercase kicker),
  a distinctive type pairing (NOT Inter/Roboto/Arial/system-ui, no script/squiggle fonts),
  editorial/asymmetric layout (not a centered stack), and real section-to-section variety
- Real & specific: real name, services, review quotes, contact; zero invented facts
- Imagery: a real, good photo present, well-cropped (object-fit: cover, subject in frame,
  not sliced or stretched) and well-placed — not a gradient default or an awkward crop
- Visual craft: consistent spacing & radius system, generous section padding, confident
  color use, considered detail (dividers, kickers, hover states) — not flat and templated
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


STOCK_USED_FILE = config.LOGS_DIR / "used_stock.json"


def _stock_id(url: str) -> str:
    """Stable per-photo key so the same Pexels/Unsplash photo dedupes across builds
    regardless of size/query-string variation (e.g. .../photos/3757952/... -> 3757952)."""
    m = re.search(r"/photos/(\d+)/", url) or re.search(r"photo-([\w-]+)", url)
    return m.group(1) if m else url


def _img_fingerprint(url: str) -> str:
    """A stable per-photo key that survives URL drift, so a photo a human rejected on
    a prior build can be excluded from the rebuild even if its resolved URL changes.
    Reuses the stock id for Pexels/Unsplash; for Google lh3 photo URLs the long token
    in the path is stable across re-resolutions of the same photo reference."""
    sid = _stock_id(url)
    if sid != url:
        return sid
    m = re.search(r"/(?:place-photos|places|photos|p|proxy)/([A-Za-z0-9_\-]{16,})", url)
    return m.group(1)[:80] if m else url


def _images_used_in_build(slug: str) -> set[str]:
    """Fingerprints of every image a prior build placed — used to exclude photos a
    human reviewer rejected from the next rebuild of the same lead."""
    if not slug:
        return set()
    d = config.SAMPLES_CACHE_DIR / slug
    # self-hosted builds rewrite srcs to local files, so recover the ORIGIN urls from
    # the sources sidecar (local file -> remote url) to fingerprint against.
    sj = d / "sources.json"
    if sj.is_file():
        try:
            origins = json.loads(sj.read_text(encoding="utf-8")).values()
            return {_img_fingerprint(u) for u in origins if u.startswith("http")}
        except Exception:
            pass
    # legacy build that hotlinked remote urls directly in the HTML
    p = d / "index.html"
    if not p.is_file():
        return set()
    html = p.read_text(encoding="utf-8", errors="ignore")
    return {_img_fingerprint(u)
            for u in re.findall(r'<img[^>]+src=["\']([^"\']+)', html, re.I)
            if u.startswith("http")}


def _used_stock() -> set[str]:
    try:
        return set(json.loads(STOCK_USED_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def remember_stock(urls: list[str]) -> None:
    """Persist the stock photos a shipped sample actually used, so later builds pick
    DIFFERENT ones — Austin flagged identical stock photos across samples (215/230)."""
    ids = {_stock_id(u) for u in urls if "pexels.com" in u or "unsplash.com" in u}
    if not ids:
        return
    try:
        STOCK_USED_FILE.parent.mkdir(exist_ok=True)
        STOCK_USED_FILE.write_text(json.dumps(sorted(_used_stock() | ids)), encoding="utf-8")
    except Exception:
        log.warning("could not persist used stock")


def _dedupe_pool(urls: list[str], lead, limit: int) -> list[str]:
    """Drop photos already used by other samples; rotate the remainder by lead id so
    two leads drawing the same pool still get different candidates. Falls back to the
    (rotated) full pool if dedupe would leave too few to choose from."""
    used = _used_stock()
    fresh = [u for u in urls if _stock_id(u) not in used]
    pool = fresh if len(fresh) >= min(3, len(urls)) else urls
    if pool:
        off = lead["id"] % len(pool)
        pool = pool[off:] + pool[:off]
    return pool[:limit]


def _stock_queries(brief: dict, services: list[str]) -> list[str]:
    """Ordered search queries, most-specific first. A business's REAL services
    (e.g. 'Botox, dermal filler') make a far better query than a generic niche
    word ('spa treatment room', which returns saunas for an injectables clinic).
    The generic niche query stays as a fallback so an over-narrow service term
    that returns nothing can't zero out the whole stock rung."""
    generic = brief.get("stock_query") or "modern small business"
    queries = []
    # some niches (dental/medical) have CLINICAL service terms ("dental implants")
    # that return cold surgical/lab stock, not warm patient-facing photos — for those
    # the brief sets avoid_service_query, so we lean on the curated warm generic query.
    if services and not brief.get("avoid_service_query"):
        # the 1-2 most specific real services, as a natural image query
        specific = " ".join(services[:2]).strip()
        if specific and specific.lower() not in generic.lower():
            queries.append(specific)
    queries.append(generic)
    return queries


def _pexels_search(query: str) -> list[str]:
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": 80,
                    "orientation": "landscape", "size": "large"},
            headers={"Authorization": config.PEXELS_API_KEY}, timeout=15)
        if r.status_code == 200:
            urls = [p["src"]["large2x"] for p in r.json().get("photos", [])
                    if p.get("width", 0) >= 1200 and p.get("src", {}).get("large2x")]
            if not urls:
                log.warning("pexels: 0 usable results for %r", query)
            return urls
        log.warning("pexels HTTP %s for %r: %s", r.status_code, query, r.text[:120])
    except requests.RequestException:
        log.warning("pexels search failed for %r", query)
    return []


def _unsplash_search(query: str) -> list[str]:
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "per_page": 30, "orientation": "landscape",
                    "client_id": config.UNSPLASH_ACCESS_KEY}, timeout=15)
        if r.status_code == 200:
            return [p["urls"]["regular"] for p in r.json().get("results", [])
                    if p.get("urls", {}).get("regular")]
    except requests.RequestException:
        log.warning("unsplash search failed for %r", query)
    return []


def stock_images(lead, brief: dict, limit: int = 8,
                 services: list[str] | None = None) -> list[str]:
    """Ladder step 3 — free niche stock (Pexels, then Unsplash). Both APIs are
    $0; a clean stock photo beats a typographic hero every time. Returns
    CANDIDATES (up to `limit`) — the generator picks the few that fit the
    palette/mood, so more choices = better odds of an on-brand hero. Returns []
    when no key is configured or nothing landscape/large comes back.

    Tries a service-specific query first (see _stock_queries), then the generic
    niche query. Fetches a LARGE pool and dedupes against photos other samples
    already used (see _dedupe_pool) so no two samples share stock photos."""
    for query in _stock_queries(brief, services or []):
        if config.PEXELS_API_KEY:
            urls = _pexels_search(query)
            if urls:
                return _dedupe_pool(urls, lead, limit)
        if config.UNSPLASH_ACCESS_KEY:
            urls = _unsplash_search(query)
            if urls:
                return _dedupe_pool(urls, lead, limit)
    return []


def select_images(conn, lead, profile: dict, brief: dict, service_desc: str = "",
                  services: list[str] | None = None,
                  exclude: set[str] | None = None) -> tuple[list[str], str, dict]:
    """Image source ladder; returns (urls, image_source, image_notes).

    Content-quality gates the SOURCE, not just aspect ratio. Each rung's
    candidates go through the Haiku vision QC (describe_and_rank_images), which
    drops low-quality and wrong-service photos; only if survivors remain does
    that rung win. So a business whose only real photos are a parking lot or a
    sauna falls through to clean stock instead of shipping the bad photo — the
    old ladder locked onto 'places' on aspect ratio alone and could never
    recover. Returns the survivor URLs (best-hero-first) plus their vision notes
    so build_one doesn't re-run the QC."""
    ban = exclude or set()

    hero_guidance = brief.get("hero_guidance", "")

    def qc(urls: list[str], source: str) -> tuple[list[str], dict]:
        # drop photos a human reviewer rejected on a prior build of this lead, so the
        # same hero physically can't return — the rung falls through to a fresh source
        urls = [u for u in urls if _img_fingerprint(u) not in ban]
        ordered, notes = describe_and_rank_images(
            conn, lead, urls, source, service_desc, hero_guidance)
        survivors = [u for u in ordered if notes.get(u, {}).get("use", True)]
        return survivors, notes

    # Two DIFFERENT bars: a photo is USABLE (belongs on the page) at OWNER_HERO_MIN,
    # but only LEADS the page as the hero at `lead_min` — for most niches these are the
    # same (4), but dental sets hero_lead_min=5 so a good-not-great owner photo still
    # gets used in a section while the hero comes from the best available smile.
    lead_min = brief.get("hero_lead_min", OWNER_HERO_MIN)

    def strong_hero(survivors: list[str], notes: dict, min_hero: int) -> bool:
        # is at least one survivor a GENUINELY good hero (not merely usable)?
        return bool(survivors) and max(
            (notes.get(u, {}).get("hero") or 0) for u in survivors) >= min_hero

    # Adverse selection: our qualifier ("bad/no website") systematically picks the
    # businesses WORST at visual self-presentation, so their own Google/site photos
    # make poor heroes far more often than not (Austin's "same bad hero keeps coming
    # back"). So owner photos only LEAD the page when one is a genuinely strong hero;
    # otherwise they drop to a last-resort behind clean stock, which beats a mediocre
    # owner selfie/close-up/parking-lot as the hero. Set aside any QC survivors as a
    # fallback so we still prefer a real (if weak) photo over a bare typographic hero.
    owner_backup: tuple[list[str], dict] | None = None

    # rung 1: their OWN photos (authentic beats stock) — but only if one is a strong hero.
    ranked = rank_photos(profile)
    if ranked:
        photos = places_photo_urls(conn, [p["ref"] for p in ranked])
        if photos:
            survivors, notes = qc(photos, "places")
            if strong_hero(survivors, notes, lead_min):
                return survivors, "places", notes
            if survivors:
                owner_backup = (survivors, notes)
    # rung 2: an OUTDATED site's own images (same owner-photo bar)
    if owner_backup is None and lead["qualify_status"] == "OUTDATED":
        imgs = site_images(lead["existing_website"])
        if imgs:
            survivors, notes = qc(imgs, "their_site")
            if strong_hero(survivors, notes, lead_min):
                return survivors, "their_site", notes
            if survivors:
                owner_backup = (survivors, notes)
    # rung 3: service-specific stock, also QC'd (a generic pool still returns
    # wrong-modality shots — the match check filters them). Preferred over a weak
    # owner photo for the hero.
    stock = stock_images(lead, brief, services=services)
    if stock:
        survivors, notes = qc(stock, "stock")
        if survivors:
            # mix_sources niches (dental): the owner's photos are usable but none is a
            # strong-enough HERO, so keep them for supporting sections and let the hero
            # come from the best smile available (owner or stock) instead of throwing
            # their real photos away or heroing a mediocre one.
            if brief.get("mix_sources") and owner_backup:
                return _merge_owner_stock(owner_backup, (survivors, notes))
            return survivors, "stock", notes
    # rung 3.5: no usable stock — fall back to the weak owner photos we set aside
    # (a real photo the generator can place small still beats a bare gradient hero)
    if owner_backup:
        return owner_backup[0], "places", owner_backup[1]
    # rung 4: nothing usable — the generator uses a typographic hero
    return [], "typographic", {}


def _merge_owner_stock(owner: tuple[list[str], dict], stock: tuple[list[str], dict],
                       limit: int = 6) -> tuple[list[str], str, dict]:
    """Combine a usable-but-not-hero owner set with stock, best-hero first, so the
    strongest photo (often a stock smile) leads and the owner's real photos fill the
    rest. Each note is tagged `own` so the generator can present owner photos as this
    business's, but never imply a stock photo depicts this specific business."""
    o_urls, o_notes = owner
    s_urls, s_notes = stock
    notes: dict = {}
    for u in o_urls:
        notes[u] = {**o_notes.get(u, {}), "own": True}
    for u in s_urls:
        notes[u] = {**s_notes.get(u, {}), "own": False}
    merged = sorted(o_urls + s_urls,
                    key=lambda u: -(notes[u].get("hero") or 0))[:limit]
    return merged, "mixed", {u: notes[u] for u in merged}


def describe_and_rank_images(conn, lead, urls: list[str], image_source: str,
                             service_desc: str = "",
                             hero_guidance: str = "") -> tuple[list[str], dict]:
    """Haiku-vision pre-pass. The generator picks photos from URLs it cannot SEE,
    so it once made a clinical gloved-hand close-up the hero. Show the candidates
    to a cheap vision model, get a one-line description + hero-suitability (1-5) +
    a SERVICE-MATCH verdict per image, reorder best-hero-first, and hand the
    descriptions to the generator so it chooses eyes-open (see build_prompt /
    _render_image_block).

    `service_desc` describes what this business actually does; an image that
    depicts the WRONG service/setting (a sauna on an injectables clinic) is
    marked use=false so `select_images` can fall through to a better source.

    Returns (reordered_urls, {url: {"desc","hero","use","focus"}}). Degrades to
    (urls, {}) on any failure or when there's nothing to rank — a failed vision
    call must never crash a build (it just skips the extra filtering)."""
    if not urls:
        return urls, {}
    if costs.check(conn, "claude", PHOTO_VISION_USD) == "block":
        log.warning("claude budget blocked; photo vision rank skipped")
        return urls, {}
    kind = ("licensed stock photos for this industry (NOT the business's own "
            "premises/staff)" if image_source == "stock"
            else "photos associated with this specific business")
    match_line = (
        f"\n\nThis business is: {service_desc}. For EACH image also judge SERVICE MATCH — "
        "does it plausibly depict THIS kind of business/service? An image showing a "
        "clearly WRONG service or setting (e.g. a sauna, massage table, or body-massage "
        "room for an injectables/Botox clinic; a gym for a law office; a kitchen for a "
        "dental office) is a MISMATCH — set 'match': false. When in doubt about a "
        "generic, on-tone scene (a clean neutral interior, an elegant detail), allow it "
        "(match=true). A mismatch embarrasses the owner, so be strict about obviously "
        "wrong modalities." if service_desc else "")
    # per-niche art direction for the HERO score — e.g. for dental, a healthy smile is
    # the best hero and a 'surgery look' (drill/mask/instruments) is a poor one. This
    # OVERRIDES the generic hero notes above where they conflict.
    hero_line = (f"\n\nNICHE HERO GUIDANCE (apply when scoring 'hero'; it OVERRIDES the "
                 f"generic hero notes above where they conflict): {hero_guidance}"
                 if hero_guidance else "")
    content = [{"type": "text", "text":
        f"These are candidate images for a {lead['category'] or 'local business'} "
        f"website ({kind}). For EACH image, judge how well it would work as the large "
        "HERO background of a PREMIUM website: high-quality, well-composed, appealing "
        "and uncluttered at full width. A clinical close-up, a logo, a screenshot, a "
        "dim/cluttered/garish snapshot, or an awkward crop is a POOR hero (it may still "
        "be usable small inside a section). These are POOR heroes (hero=1) and 'use': "
        "false unless nothing else exists: a photo with a VISIBLE WATERMARK or overlaid "
        "text; a STOREFRONT / BUILDING EXTERIOR / street view / parking lot / signage "
        "shot; a logo; or an obvious AMATEUR PHONE SNAPSHOT (harsh flash, tilted, messy "
        "background). Also note WHERE the main subject sits in the "
        "frame, so a CSS crop can be aimed to keep it (a wide hero crops off top and "
        "bottom; a tall column crops off the sides)." + match_line + hero_line}]
    for i, url in enumerate(urls):
        content.append({"type": "text", "text": f"Image {i+1}:"})
        content.append({"type": "image", "source": {"type": "url", "url": url}})
    content.append({"type": "text", "text":
        "Return STRICT JSON only:\n"
        '{"images": [{"n": 1, "desc": "<=12 words: subject + quality", '
        '"hero": <1-5 hero suitability>, "use": true|false (is it good enough quality '
        'to belong on the page at all?), "match": true|false (does it depict THIS '
        'business\'s service/setting — true if no service context given), '
        '"focus": "top|center|bottom|left|right (where the main '
        'subject/face sits, so a crop keeps it in frame)"}, ...]}'})
    try:
        client = _claude()
        resp = client.messages.create(
            # scale with the pool: each image needs ~130 tokens of JSON (desc +
            # hero + use + match + focus), so a fixed cap truncated 8-image stock
            # pools into unparseable JSON and the QC silently no-op'd.
            model=config.MODEL_CLASSIFIER, max_tokens=min(2000, 300 + 140 * len(urls)),
            system="You are a photo editor for premium web design. Be blunt about quality.",
            messages=[{"role": "user", "content": content}],
        )
        costs.record(conn, "claude", "photo_vision_rank",
                     costs.claude_cost(config.MODEL_CLASSIFIER,
                                       resp.usage.input_tokens, resp.usage.output_tokens),
                     lead_id=lead["id"], tokens_in=resp.usage.input_tokens,
                     tokens_out=resp.usage.output_tokens)
        conn.commit()
        data = json.loads(re.search(r"\{.*\}", resp.content[0].text, re.S).group(0))
    except (AttributeError, json.JSONDecodeError):
        trunc = " (response hit max_tokens — truncated)" if getattr(
            resp, "stop_reason", None) == "max_tokens" else ""
        log.warning("photo vision rank unparseable%s; keeping original order", trunc)
        return urls, {}
    except Exception:
        log.exception("photo vision rank failed; keeping original order")
        return urls, {}
    notes = {}
    for item in data.get("images") or []:
        n = _as_int(item.get("n"))
        if 1 <= n <= len(urls):
            foc = str(item.get("focus") or "center").lower()
            # a wrong-service photo is unusable regardless of how pretty it is
            usable = bool(item.get("use", True)) and bool(item.get("match", True))
            notes[urls[n - 1]] = {
                "desc": str(item.get("desc") or "")[:120],
                "hero": _as_int(item.get("hero")),
                "use": usable,
                "focus": foc if foc in FOCUS_CSS else "center",
            }
    if not notes:
        return urls, {}
    # best hero first; sorted() is stable so equal scores keep their prior order
    ordered = sorted(urls, key=lambda u: -(notes.get(u, {}).get("hero") or 0))
    log.info("lead %s photo rank: %s", lead["id"],
             [(notes[u]["hero"], notes[u]["desc"]) for u in ordered if u in notes])
    return ordered, notes


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


# --- Real facts from their own site (the copy-quality lever) ----------------
# The verified-fact base was Google Places only (name/rating/hours/5 reviews),
# so the generator had nothing SPECIFIC to say and fell back to generic mood-copy
# ("Comfort first", "Results you'll love") — which reads worse than the owner's
# real site. Their own website is a goldmine of true, specific facts (their actual
# service list, their tagline). We extract those, ANCHOR every item to text that
# really appears on their site (so an extraction slip can't leak an unsourced claim),
# and feed them into LOCKED CONTENT + the fact-check's verified data.

SITE_FACTS_USD = 0.02          # cheap Haiku extraction; real cost ~0.005
SITE_FACTS_CACHE = config.LOGS_DIR / "site_facts_cache.json"


def _url_key(url: str) -> str:
    p = urlparse((url or "").lower())
    return (p.netloc + p.path).rstrip("/") or (url or "").lower()


def _cached_site_facts(url: str):
    """Returns the cached facts dict, or None if this URL was never extracted.
    (A cached {} means 'extracted, nothing usable' and is returned as {} — not None —
    so we don't re-call Haiku for sites we already found barren.)"""
    try:
        data = json.loads(SITE_FACTS_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data.get(_url_key(url))


def _remember_site_facts(url: str, facts: dict) -> None:
    try:
        SITE_FACTS_CACHE.parent.mkdir(exist_ok=True)
        try:
            data = json.loads(SITE_FACTS_CACHE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        data[_url_key(url)] = facts
        SITE_FACTS_CACHE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        log.warning("could not persist site facts")


def _site_text_for_facts(website: str) -> str:
    """Homepage visible text (+ one services/menu page if linked) — the raw material
    the service list is extracted from and anchored against."""
    def page_text(url):
        try:
            r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        except requests.RequestException:
            return "", None
        if r.status_code >= 400 or not r.text:
            return "", None
        soup = BeautifulSoup(r.text, "lxml")
        for t in soup(["script", "style", "noscript", "svg", "template"]):
            t.decompose()
        return re.sub(r"\s+", " ", soup.get_text(" ")).strip(), (soup, r.url)

    text, meta = page_text(website)
    if not meta:
        return ""
    soup, base = meta
    for a in soup.find_all("a", href=True):
        label = (a.get_text(" ") + " " + a["href"]).lower()
        if any(k in label for k in ("service", "treatment", "menu", "what-we", "offerings")):
            href = requests.compat.urljoin(base, a["href"])
            if urlparse(href).netloc == urlparse(base).netloc and href != base:
                more, _ = page_text(href)
                if more:
                    text += " " + more
                break
    return text[:12000]


def extract_site_facts(conn, lead, website: str) -> dict:
    """Extract the business's REAL services + tagline from their OWN site so the
    sample's copy can be specific AND honest — the single biggest lever on "reads
    worse than their existing site". STRICTLY SOURCED: every returned item is anchored
    to text that actually appears on their page, so an extraction hallucination cannot
    leak an unsourced claim into LOCKED CONTENT. Cached per-URL (calibration rebuilds
    are then free). Returns {} when there's no usable site or nothing extractable —
    never fails a build (this is an enhancement, not a gate)."""
    if not website or not config.ANTHROPIC_API_KEY:
        return {}
    cached = _cached_site_facts(website)
    if cached is not None:
        return cached
    text = _site_text_for_facts(website)
    if len(text) < 120:
        _remember_site_facts(website, {})
        return {}
    if costs.check(conn, "claude", SITE_FACTS_USD) == "block":
        return {}
    prompt = f"""From this business's OWN website text, extract ONLY what the site itself states.
Do NOT infer, guess, or add anything typical-for-the-industry that isn't written here.

Return STRICT JSON only:
{{"services": ["<exact service / treatment names the site says they offer — short noun phrases, max 10>"],
  "tagline": "<their own headline or tagline, copied verbatim, or empty if none clearly is one>"}}

Rules:
- services: concrete NAMED offerings (e.g. "Botox", "Dermal Filler", "Microneedling",
  "Laser Hair Removal"), NOT vague categories ("wellness", "beauty", "self-care"). Copy the
  site's own wording. Omit prices and durations.
- Include a service ONLY if the text names it. If the site names none, return [].
- No superlatives, no invented specialties.

WEBSITE TEXT:
{text}"""
    try:
        client = _claude()
        resp = client.messages.create(
            model=config.MODEL_CLASSIFIER, max_tokens=500,
            system="You extract only facts explicitly present in the given text. Never infer.",
            messages=[{"role": "user", "content": prompt}],
        )
        costs.record(conn, "claude", "site_facts_extract",
                     costs.claude_cost(config.MODEL_CLASSIFIER,
                                       resp.usage.input_tokens, resp.usage.output_tokens),
                     lead_id=lead["id"], tokens_in=resp.usage.input_tokens,
                     tokens_out=resp.usage.output_tokens)
        conn.commit()
        data = json.loads(re.search(r"\{.*\}", resp.content[0].text, re.S).group(0))
    except (AttributeError, json.JSONDecodeError):
        log.warning("site_facts unparseable; skipping")
        _remember_site_facts(website, {})
        return {}
    except Exception:
        log.exception("site_facts extract failed")
        return {}  # transient (e.g. API) — don't cache, allow a later retry
    # ANCHOR every item to their real site text — this is the strictly-sourced guarantee
    site_norm = _alnum(text)
    services, seen = [], set()
    for s in (data.get("services") or []):
        s = re.sub(r"\s+", " ", str(s)).strip(" .-–—•")
        key = _alnum(s)
        if 2 <= len(key) <= 40 and len(s) <= 40 and key in site_norm and key not in seen:
            seen.add(key)
            services.append(s)
        if len(services) >= 10:
            break
    tagline = re.sub(r"\s+", " ", str(data.get("tagline") or "")).strip()
    if not (8 <= len(tagline) <= 120 and _alnum(tagline) in site_norm):
        tagline = ""
    facts = {}
    if services:
        facts["services"] = services
    if tagline:
        facts["tagline"] = tagline
    _remember_site_facts(website, facts)
    if facts:
        log.info("lead %s site facts: %d service(s)%s", lead["id"], len(services),
                 " + tagline" if tagline else "")
    return facts


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


# DESIGN-LANGUAGE MENU — distilled from reference med-spa sites Austin picked
# (1-3: marasmedspa / medspafm / navamedicalspa; 4: milan / laseraway / rejuve).
# Rotated one-per-lead so samples STOP converging on the same warm-blush-serif-centered
# template (his "they all feel template" note). Each is a distinct, committed aesthetic.
# Design DNA only — never their content/photos/brand. A real brand palette (from the
# lead's own existing site) still OVERRIDES the language palette — see _render_design_language.
DESIGN_LANGUAGES = (
    {   # 1. DARK LUXE (maras) — restrained, gold-on-charcoal, editorial
        "name": "Dark Luxe",
        "palette": "a deep charcoal / near-black base (#16181c–#1f2328) with warm "
                   "cream sections and ONE confident gold accent (#c8a96a); solid "
                   "black buttons — never timid gray-on-white",
        "type": "a high-contrast editorial serif display (Playfair/Didot-style) with "
                "small UPPERCASE letter-spaced kicker labels above headings, over a "
                "clean sans body (Montserrat/Inter-alt)",
        "layout": "a dramatic full-bleed hero image with a dark scrim and centered "
                  "serif headline; a horizontal row of 3–4 icon + short-label cards; "
                  "an even service grid with a subtle gold hairline/badge per card; "
                  "one confident full-bleed dark band. Generous 80–120px section padding",
        "motion": "scroll-reveal (fade + translateY) and gold-underline/lift on hover"},
    {   # 2. WARM EDITORIAL (medspafm) — mocha/rose, asymmetric splits
        "name": "Warm Editorial",
        "palette": "warm mocha/clay (#8f6e56) + soft rose-clay (#d1a394) + warm cream "
                   "whites; a brown/clay accent used decisively in one full-bleed band",
        "type": "a high-contrast serif display that MIXES an italic serif line with an "
                "UPPERCASE serif line (e.g. an italic phrase set above a bold UPPERCASE "
                "line), UPPERCASE kicker labels, clean sans body",
        "layout": "ASYMMETRIC and editorial — LEFT-aligned hero text over a full-bleed "
                  "image; a welcome section that SPLITS image on one side, text + a 2×2 "
                  "feature-card cluster on the other; services as a clean list separated "
                  "by hairline dividers with arrow affordances (not always boxed cards); "
                  "a warm full-bleed accent band near the end. No two adjacent sections share a layout",
        "motion": "IntersectionObserver scroll-reveal + card/button hover-lift (pop)"},
    {   # 3. AIRY MINIMAL (nava) — white, one bold accent, big whitespace
        "name": "Airy Minimal",
        "palette": "mostly white / off-white with near-black text and ONE bold saturated "
                   "accent (a deep plum-violet #6d4aa3 OR a deep emerald — pick what fits "
                   "the business), used sparingly in one divider band + links/buttons",
        "type": "large, LEFT-aligned classic serif headings with very roomy line-height, "
                "tiny UPPERCASE letter-spaced section labels, airy sans body",
        "layout": "minimal and gallery-like with LOTS of negative space; offset/asymmetric "
                  "image blocks that bleed off one edge; a single bold accent divider band; "
                  "very few boxed cards; oversized type carrying the hierarchy instead of borders",
        "motion": "subtle scroll-reveal fades; minimal, calm hover states"},
    {   # 4. CLINICAL MODERN (milan / laseraway / rejuve) — confident teal, big scale,
        # bold stat band. The premium modern-clinic look, NOT a soft blush spa.
        "name": "Clinical Modern",
        "palette": "confident and clean: a deep TEAL accent (#00698a / #0d7d8f) with warm "
                   "CREAM (#f3efe7) section backgrounds, crisp white, and deep navy/charcoal "
                   "text; rounded PILL buttons in the teal accent — reads like a modern "
                   "clinic, never a soft pastel spa",
        "type": "DRAMATIC scale contrast: a huge headline pairing an elegant ITALIC SERIF "
                "display accent word (Playfair/Cormorant-style) with a heavy geometric SANS "
                "(Jost/Poppins-style), above a tiny UPPERCASE letter-spaced kicker; clean "
                "sans body. The size jump from kicker to headline is the whole effect",
        "layout": "a full-bleed photo hero with a scrim and elegant overlaid headline + one "
                  "teal pill CTA; then a SPLIT welcome section (kicker + big headline + pill "
                  "on one side, full-height photo on the other); a BOLD STAT BAND of 2-4 big "
                  "numbers with small labels (REAL numbers only — e.g. Google rating + review "
                  "count from LOCKED CONTENT, never invented); a clean service grid or "
                  "hairline-divided list. No two adjacent sections share a layout",
        "motion": "IntersectionObserver scroll-reveal + hover-lift + COUNT-UP animation on "
                  "the stat-band numbers as it scrolls into view + gentle image hover-zoom"},
)


def _render_design_language(dl: dict, has_brand: bool) -> str:
    override = ("Their real brand colors/logo above OVERRIDE this palette; keep this "
                "language's TYPE energy, LAYOUT and MOTION."
                if has_brand else
                "Commit FULLY to this language — do NOT drift back to a generic "
                "warm-blush/sage centered spa template.")
    return (f"COMMITTED DESIGN LANGUAGE for this build — \"{dl['name']}\" (every build "
            f"gets a deliberately different one so no two samples look alike):\n"
            f"  Palette: {dl['palette']}\n"
            f"  Type: {dl['type']}\n"
            f"  Layout: {dl['layout']}\n"
            f"  Motion: {dl['motion']}\n"
            f"  {override}")


def _render_image_block(images: list[str], image_source: str,
                        image_notes: dict | None = None) -> str:
    """The IMAGES section of the gen prompt. When `image_notes` is present (from
    describe_and_rank_images), each URL carries the vision model's one-line
    description + hero-fit, so the generator picks eyes-open instead of guessing
    from a URL it can't see."""
    if not images:
        return ("  none available — use a strong TYPOGRAPHIC hero on a solid or subtle "
                "gradient background. Never use a broken <img> or an invented URL.")
    notes = image_notes or {}
    lines = []
    for i, url in enumerate(images):
        nt = notes.get(url, {})
        desc = f" — {nt['desc']}" if nt.get("desc") else ""
        avoid = " [LOW QUALITY — use small or omit]" if nt and not nt.get("use", True) else ""
        # aim the crop: the vision model saw where the subject is
        foc = nt.get("focus")
        pos = (f" — subject at {foc}; set object-position: {FOCUS_CSS[foc]}"
               if foc and foc != "center" and foc in FOCUS_CSS else "")
        if image_source == "stock":
            fit = f" [hero-fit {nt['hero']}/5]" if nt.get("hero") else ""
            lines.append(f"  {i+1}. {url}{fit}{avoid}{desc}{pos}")
        elif image_source == "mixed":
            # per-image provenance: owner photos may be shown as theirs; stock may not
            src = "their OWN photo" if nt.get("own") else ("stock — atmosphere only, "
                  "do NOT imply it depicts this business")
            role = "hero" if i == 0 else "supporting"
            fit = f", hero-fit {nt['hero']}/5" if nt.get("hero") else ""
            lines.append(f"  {i+1}. {url}  (role: {role}{fit}; {src}){avoid}{desc}{pos}")
        else:
            role = "hero" if i == 0 else "supporting"
            fit = f", hero-fit {nt['hero']}/5" if nt.get("hero") else ""
            lines.append(f"  {i+1}. {url}  (role: {role}{fit}){avoid}{desc}{pos}")
    block = "\n".join(lines)
    if image_source == "mixed":
        block += (
            "\n  MIXED SOURCES: photos marked 'their OWN photo' really are this "
            "business's — present them as such. Photos marked 'stock' are licensed "
            "industry stock — use them as atmosphere and NEVER caption or imply they "
            "depict this specific business, its staff, or its premises.")
    if image_source == "stock":
        block += (
            "\n  These are stock CANDIDATES — CHOOSE the 1-3 whose lighting, tones and "
            "subject genuinely fit the palette/mood above and use ONLY those (the single "
            "best one as hero). SKIP any that look cluttered, garish, cheap, or off-palette "
            "— a page with one perfect photo beats a page with three mediocre ones."
            "\n  NOTE: these are licensed stock photos matching their industry — NOT "
            "this business's own premises/staff/work. Use them as atmosphere; never "
            "caption or imply they depict this specific business.")
    if notes:
        block += (
            "\n  The descriptions and hero-fit scores above were written by a vision model "
            "that SAW these images — you cannot. TRUST them: lead with the highest hero-fit "
            "image, keep low hero-fit or LOW QUALITY images out of the hero (use them small "
            "in a supporting section, or omit them entirely).")
        if max((notes.get(u, {}).get("hero") or 0) for u in images) <= 2:
            block += (
                "\n  NONE of these is a strong hero (all hero-fit <=2). Use a confident "
                "TYPOGRAPHIC / brand-driven hero instead and place the best photo lower on "
                "the page — do NOT force a weak photo into the hero.")
    return block


MAX_TESTIMONIALS = 3          # how many verbatim quotes we lock into the page
_TESTIMONIAL_MAX_CHARS = 420  # skip rambling reviews so the model never wants to trim


def assemble_content_blocks(lead, profile: dict, site_facts: dict | None = None,
                            drop_testimonials: bool = False) -> dict:
    """Assemble EVERY factual element of the page IN CODE, verbatim, so the
    generator never has to (and never gets to) invent or restate a fact.

    This is the facts-in-code half of the split: the model receives these as
    locked strings it must place unmodified and writes only benefit-language
    copy + layout. Removes the fabrication surface that prompting could not
    close (stitched/renamed testimonials, miscounted open-days, invented
    amenities). `drop_testimonials` yields the honest aggregate-only fallback.

    `site_facts` (from extract_site_facts) carries the business's REAL, strictly-
    sourced service list + tagline from their own website — the substance that lets
    the copy be specific instead of generic mood-filler.
    """
    site_facts = site_facts or {}
    hours = [h for h in (profile.get("hours") or []) if h and h.strip()]
    open_days = sum(1 for h in hours if "closed" not in h.lower())
    rating = lead["rating"]
    review_count = lead["review_count"]
    rating_line = None
    if rating and review_count:
        # trim a trailing .0 so "5.0" reads as "5" only if it's whole
        r = f"{rating:g}" if isinstance(rating, (int, float)) else str(rating)
        rating_line = f"Rated {r} from {review_count} Google reviews"

    testimonials: list[str] = []
    if not drop_testimonials:
        # Only 4-5 star reviews, whole and verbatim. Prefer shorter ones so the
        # layout never tempts the model to trim (the reviews carry no author, so
        # attribution is always the honest generic "— Google review").
        quotable = [r["text"].strip() for r in (profile.get("reviews") or [])
                    if (r.get("rating") or 0) >= 4 and (r.get("text") or "").strip()]
        quotable = [t for t in quotable if 40 <= len(t) <= _TESTIMONIAL_MAX_CHARS]
        quotable.sort(key=len)
        testimonials = quotable[:MAX_TESTIMONIALS]

    return {
        "brand_name": _core_brand(lead["business_name"]) or lead["business_name"],
        "full_name": lead["business_name"],
        "rating_line": rating_line,
        "phone": (lead["phone"] or "").strip() or None,
        "address": (lead["address"] or "").strip() or None,
        "hours": hours,
        "open_days": open_days,
        "testimonials": testimonials,
        "attribution": "— Google review",
        "real_services": site_facts.get("services") or [],
        "tagline": site_facts.get("tagline") or None,
    }


def _render_content_contract(content: dict) -> str:
    """The LOCKED CONTENT section of the gen prompt — verbatim facts the model
    must reproduce exactly and may not add to."""
    lines = [
        "LOCKED CONTENT — this is the COMPLETE and ONLY set of facts about this business.",
        "Reproduce each item below on the page EXACTLY as written, word for word. You may",
        "wrap it in your own markup and style/position it, but you may NOT add to, remove",
        "from, reword, paraphrase, re-order, translate, or \"fix\" the text of any item, and",
        "you may state NO fact that is not here (see STRICT HONESTY).",
        "",
        f'  BUSINESS NAME (the brand, use throughout): {content["brand_name"]}',
    ]
    if content.get("real_services"):
        lines.append(
            '  REAL SERVICES — the ACTUAL services this business offers, taken from their '
            'OWN website. Build the services section around THESE real named services (name '
            'them, then sell each in warm benefit language). Do NOT replace them with vague '
            'generic descriptions, and do NOT invent any other treatment/service not listed:')
        lines += [f"    - {s}" for s in content["real_services"]]
    if content.get("tagline"):
        lines.append(
            f'  THEIR TAGLINE (their own words, from their site) — you MAY use or lightly '
            f'adapt it as headline/voice; it is not a checkable fact: "{content["tagline"]}"')
    if content["rating_line"]:
        lines.append(
            f'  RATING LINE (show ONCE, in/near testimonials — never as a hero badge): '
            f'"{content["rating_line"]}"')
    if content["phone"]:
        lines.append(f'  PHONE (use in the contact CTA): {content["phone"]}')
    if content["address"]:
        lines.append(f'  ADDRESS (use in hours/location): {content["address"]}')
    if content["hours"]:
        lines.append(
            f'  HOURS — render this list EXACTLY; the business is open '
            f'{content["open_days"]} day(s)/week, do NOT compute or state a different number:')
        lines += [f"    - {h}" for h in content["hours"]]
    if content["testimonials"]:
        lines.append(
            f'  TESTIMONIALS — place in a testimonials section; reproduce each quote '
            f'VERBATIM and WHOLE, attributed EXACTLY "{content["attribution"]}" '
            f'(never invent or add a reviewer name); show fewer if the layout needs, '
            f'but never alter one:')
        for i, t in enumerate(content["testimonials"], 1):
            lines.append(f'    {i}. "{t}"')
            lines.append(f'       {content["attribution"]}')
    else:
        lines.append(
            "  TESTIMONIALS — none available to quote. In the trust/testimonials area show "
            "ONLY the RATING LINE above (if any); add NO quoted text and invent no reviews.")
    return "\n".join(lines)


def build_prompt(lead, brief: dict, archetype: str, images: list[str],
                 content: dict, brand: dict, image_source: str = "",
                 image_notes: dict | None = None, design_language: dict | None = None) -> str:
    image_block = _render_image_block(images, image_source, image_notes)
    design_block = _render_design_language(design_language, bool(brand)) if design_language else ""
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
    open_days = content["open_days"]
    content_contract = _render_content_contract(content)
    return f"""You are an expert web designer creating a sample site to win this local business as a client.
Generate ONE complete, single-file, responsive HTML page (inline CSS; a SMALL inline <script> is allowed ONLY for the tasteful motion described under MOTION below), mobile-first.

{design_block}

DESIGN DIRECTION (niche category: {lead['category']}):
  Mood: {brief['mood']}
  Lead with: {brief['leading_section']}
  Primary CTA: {brief['primary_cta']}
  Imagery style: {brief['imagery']}
  (Palette/type are set by the COMMITTED DESIGN LANGUAGE above — the niche default
   "{brief['palette']}" is only a last resort if that is somehow absent.)
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
IMAGE CROPPING & PLACEMENT (this is where samples usually fail — the photos are
fine, the framing is not): give every content <img> a defined box (an
aspect-ratio or a height) and ALWAYS set object-fit: cover so it fills the box
without stretching or squishing — never let a photo distort or letterbox. Aim the
crop with object-position: when a photo lists a "subject at ..." hint above, use
the object-position it gives so the face/subject is never sliced off; otherwise
default object-position: center. Match photo shape to slot — a wide landscape
belongs in a full-bleed band or hero, a portrait belongs in a tall column or a
side-by-side split, not stretched across a wide hero. The hero image must be
LARGE and immersive (a tall band or full-bleed section, not a thin strip), and on
mobile it must keep a sensible height and its subject in frame. In grids, give
every image the SAME aspect-ratio so the row stays even. Use each photo ONCE — NEVER
repeat the hero image (or any photo) in a later section; if you run short of photos,
use fewer image slots rather than duplicating one.
CRAFT — this is the #1 thing separating "a designer made this" from "a competent
template." "Clean but plain" / "a tidy brochure" is a FAILURE, not a pass. Concrete
techniques to actually reach premium:
- DRAMATIC type scale: an oversized display headline (e.g. clamp(2.5rem, 6vw, 5rem))
  paired with a SMALL uppercase eyebrow/kicker label (letter-spaced ~0.1em) above section
  headings. Big contrast between display and body is the cheapest premium win.
- DISTINCTIVE type: a characterful display face for headings + a clean readable body face.
  For a serif, choose a clean MODERN/editorial serif — a high-contrast Didot/Bodoni or a
  crisp Playfair-style face — NOT an ornate, calligraphic, or handwritten serif with
  decorative swashes or a curly, looping lowercase "f"/"g". NEVER default to Inter, Roboto,
  Arial, or system-ui, and no decorative "squiggle"/script fonts — generic or gimmicky type
  is the #1 "AI slop" tell.
- EDITORIAL layout, not a centered stack: use asymmetry — offset or left-aligned section
  headings, split sections (text one side, image the other), a wide full-bleed color or
  image band. No two ADJACENT sections should share the same layout.
- EDITORIAL ≠ RANDOM: asymmetry must look DELIBERATE. Everything aligns to a clear grid
  with consistent, equal margins and a steady vertical rhythm; related items line up on
  shared edges/baselines. Never let elements look randomly placed, off-center, or
  haphazardly stacked — intentional alignment reads premium; scattered reads broken.
- CONSIDERED detail: generous section padding (~80-120px desktop), a consistent radius and
  spacing system, hairline dividers or numbered sections (01 / 02 / 03), tight heading
  letter-spacing with roomy body line-height (~1.6), and buttons with real padding + a
  hover state.
- CONFIDENT color: deploy the brand accent decisively (e.g. one full-bleed accent band or
  section), not timid gray-on-white throughout — but don't flood every section either.
- TASTEFUL MOTION — this is what makes a page feel alive instead of flat, and it is
  expected, not optional. Premium reference sites are RICH with subtle motion; a static
  page reads cheap. Do ALL of these, driven by vanilla JS/CSS only (the page is a single
  self-contained file — NO external libraries/CDNs):
  (a) SCROLL-REVEAL: sections and cards start slightly lowered and faded (opacity:0;
      transform: translateY(20px)) and ease to full as they enter the viewport, driven by
      ONE small IntersectionObserver near </body>; stagger cards in a row by ~80ms.
  (b) HOVER-LIFT: cards, service tiles, and buttons rise on hover (transform:
      translateY(-4px) + stronger shadow) over ~150-250ms.
  (c) COUNT-UP STATS: ONLY build a numeric stat band if you have REAL numbers from LOCKED
      CONTENT (e.g. the Google rating + review count). NEVER render a stat with 0 or a
      placeholder — if you don't have a real number, omit that stat (or the whole band).
      The real final value MUST be the element's static HTML text (e.g. <span>4.9</span>);
      the JS only animates it UPWARD to that value on scroll-in, and MUST restore/leave the
      real value if scripts don't run or prefers-reduced-motion is set. A stat frozen at 0
      reads as broken — the number a visitor sees with JS off must always be the real one.
  (d) IMAGE HOVER-ZOOM: photos in cards/galleries scale gently (transform: scale(1.04))
      inside an overflow:hidden frame on hover.
  Keep everything subtle and quick — never bouncy, spinning, parallax, auto-playing, or
  slow. MUST honor prefers-reduced-motion: reduce (no transforms/animation when set;
  counters jump straight to the final value).
Aim for something the owner would be proud to show off, not a page that reads as generic.

Required sections: a hero (per HERO above), services (if LOCKED CONTENT lists REAL
SERVICES, build this section around those actual named services in warm benefit
language — that specificity is what makes this beat a generic template; otherwise
describe the general {lead['category']} experience in benefit language — never invent
specific treatments, prices, or brands not in LOCKED CONTENT), why-choose-us, testimonials
(built ONLY from LOCKED CONTENT below), hours/location, and a clear contact CTA using
the locked phone/address.
Include <meta name="viewport">. Semantic HTML, alt text, sufficient contrast.
No lorem ipsum. No placeholder text. No fixed widths wider than the viewport.
A tiny tasteful footer line "Sample design" is acceptable; never plaster SAMPLE across the page.

{content_contract}

STRICT HONESTY — this is what wins or loses the client. One invented fact the owner
spots destroys all trust in the pitch, so treat this as harder than any design rule:
- The LOCKED CONTENT above is the COMPLETE set of facts about this business. Every
  concrete, checkable claim on the page MUST be one of those items, reproduced exactly.
  Anything NOT in LOCKED CONTENT is FORBIDDEN even if it's typical for the industry —
  no parking, refreshments, drinks, financing, insurance, certifications, awards, years
  in business, staff names/counts, specific prices, brand names, or named treatments.
- What YOU write is the marketing VOICE only: the hero headline and short benefit
  taglines / section intros. These must sell the EXPERIENCE with benefit language that
  asserts NO new fact (e.g. "results that look like you", "care that feels personal").
  If a sentence you write states something a customer could fact-check and it is not in
  LOCKED CONTENT, delete it. When unsure whether something is a fact, assume it is and
  leave it out.
- NO superlatives or rankings: no "best", "#1", "top-rated", "voted", "award-winning",
  "leading", "premier", "world-class" — none of these are in LOCKED CONTENT, so none
  may appear.
- Days / hours: state the hours EXACTLY as the LOCKED HOURS list; the business is open
  EXACTLY {open_days} day(s) per week — never count or infer a different number.
- COPY VOICE (judged hard — write like a real, warm, grounded person, NOT a brochure):
  * ENGLISH ONLY: write ALL visible copy in English — headline, taglines, section
    intros, labels, buttons, alt text, and the <html lang> attribute (lang="en").
    This holds even when the business NAME or its listed services are in another
    language (e.g. Spanish); keep the name itself verbatim, but never render the page
    bilingual or in that language. No Spanish (or other non-English) sentences anywhere.
  * NATURAL & CALM: short, confident sentences a native English speaker would actually
    say. Read each line aloud — if it sounds stiff, translated, ESL-ish, or like filler,
    rewrite it. Relaxed and warm, never salesy or breathless.
  * GOOD AT DESCRIPTION: be concrete and lightly sensory about the ACTUAL experience
    (the calm room, the unhurried visit, the natural-looking result) — plain vivid
    English, not vague abstract benefit-speak.
  * BANNED empty/abstract filler: "the kind of attention that feels personal", "a
    considered approach", "subtle results and ...", "elevate your journey", "your journey
    to ...", and similar hollow lines. Plain beats poetic every time.
  * PERSONAL, NOT INVASIVE: warmth is good, but know when personal matters and never
    overdo it — nothing that feels like someone wants to "crawl inside their skin" or is
    fixated on the customer's body. BAN "around your face", "for your face", "your problem
    areas", "work on your body". Speak to the person and the result, warmly and at a
    respectful distance.
  * Plain section labels ("Why us", "What we do", "Visit us") beat cutesy/flowery ones.
  * Match this register: "Still every bit you." / "Results that look like you, only more
    you." / "Come in, relax, and leave feeling like yourself."

Return only the HTML, nothing else."""


CRITIQUE_KEYS = ("bespoke", "hero", "craft", "real", "voice", "imagery", "beats", "mobile")


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

COPY VOICE CHECK (score "voice") — read EVERY headline and tagline aloud and ask:
would a real, grounded business owner actually SAY this to a customer? Score voice LOW
(1-2) and add a specific fix for ANY of these:
  - a "Real ___, real ___" template OR its fragments ("Real hands", "Real legs",
    "Real results") — unsettling and formulaic;
  - a body-part pun or cutesy metaphor as a headline ("Lighter legs", "around your face");
  - slogan/riddle lines that sound like ad-copy, not a person ("elevate your journey",
    "your journey to ...", "the kind of attention that feels personal");
  - anything stiff, translated, ESL-ish, or hollow.
Good voice sounds like a warm person: "Come in, relax, and leave feeling like yourself."

Return STRICT JSON only, scoring EXACTLY these keys:
{{"verdict": "PASS" or "FAIL", "scores": {{"bespoke": n, "hero": n, "craft": n, "real": n, "voice": n, "imagery": n, "beats": n, "mobile": n}}, "fixes": ["specific fix", ...]}}
FAIL if any of bespoke / hero / craft / voice / beats / mobile / imagery scores below 4.
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

# Ranking/superlative overclaims the model reflexively writes into headlines despite
# the ban ("prohibitions don't work — the generative prior wins"). These are pure
# adjectives/phrases that degrade gracefully when simply removed, so we SCRUB them in
# code before the gate rather than failing→retrying→killing the lead (leads 214 "best"
# and 234 "#1" both died on exactly this). Amenity/credential claims (parking, board-
# certified) are NOT scrubbed — removing them leaves broken prose; the fact-check +
# degrade-not-die path handles those instead.
SCRUB_SUPERLATIVES = (
    "#1", "number one", "number-one", "top-rated", "top rated", "award-winning",
    "award winning", "world-class", "world class", "voted", "best", "premier",
    "leading", "most trusted", "unrivaled", "unmatched", "second to none",
)


def _scrub_superlatives(html: str, source_text: str = "") -> str:
    """Remove unsourced ranking superlatives from visible text + alt/title/aria/meta,
    leaving tags, URLs, and source-attested wording (e.g. a review that really says
    "best place") untouched. Deterministic — no model, no retry.

    Operates by regex on the raw string (text between > and <, plus specific attribute
    values) — NEVER reparses/reserializes the page. An earlier BeautifulSoup version
    reordered <meta> attributes on str(soup) and broke the viewport gate check."""
    src = (source_text or "").lower()
    terms = [t for t in SCRUB_SUPERLATIVES if t not in src]
    if not terms:
        return html
    pat = re.compile(
        r"(?<![\w#])(?:the\s+|a\s+|our\s+)?(?:" +
        "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) +
        r")(?![\w-])",
        re.I)
    removed: list[str] = []

    def _tidy(s: str) -> str:
        s = re.sub(r"[ \t]{2,}", " ", s)
        s = re.sub(r"\s+([,.!?;:])", r"\1", s)
        return s

    def _do(text: str) -> str:
        if not pat.search(text):
            return text
        removed.extend(m.group(0).strip() for m in pat.finditer(text))
        return _tidy(pat.sub("", text))

    # visible text nodes only (content between a '>' and the next '<' — never inside a tag)
    out = re.sub(r"(>)([^<]*)(<)", lambda m: m.group(1) + _do(m.group(2)) + m.group(3), html)
    # specific attribute values where overclaims sometimes hide (alt/overlay/meta desc)
    for q in ('"', "'"):
        out = re.sub(
            r"((?:alt|title|aria-label|content)\s*=\s*" + q + r")([^" + q + r"]*)(" + q + r")",
            lambda m: m.group(1) + _do(m.group(2)) + m.group(3), out, flags=re.I)
    if removed:
        log.info("scrubbed %d superlative(s): %s", len(removed),
                 ", ".join(sorted(set(removed)))[:150])
    return out


def fact_check(conn, lead, profile: dict, html: str,
               site_facts: dict | None = None) -> tuple[bool, str]:
    """Grounding pass: verify every factual claim in the page is supported by the
    business's REAL data. Returns (ok, "unsupported claim; unsupported claim").

    This is the check the design `critique()` structurally cannot do — the critique
    never sees the source facts, so invention (fake amenities, superlatives, wrong
    day-counts, laundered quotes) slips past it. Cheap Haiku-class, text-only.

    `site_facts` (their real, strictly-sourced services from their own website) is
    folded into VERIFIED DATA so the checker treats those named services as supported
    — otherwise it would flag the very specificity we just added as "unsupported".
    """
    if not config.ANTHROPIC_API_KEY:
        return True, ""
    if costs.check(conn, "claude", FACT_CHECK_USD) == "block":
        return True, ""  # budget-blocked: don't fail the sample over the fact-check
    site_facts = site_facts or {}
    hours = profile.get("hours") or []
    facts = {
        "name": lead["business_name"], "category": lead["category"],
        "phone": lead["phone"], "address": lead["address"],
        "rating": lead["rating"], "review_count": lead["review_count"],
        "hours": hours,
        "open_days_per_week": sum(1 for h in hours if "closed" not in h.lower()),
        "summary": profile.get("summary"),
        "services_listed_on_their_own_website": site_facts.get("services") or None,
        "reviews_with_ratings": profile.get("reviews"),
    }
    trimmed = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html,
                     flags=re.S | re.I)[:45000]
    prompt = f"""You are fact-checking a sales sample website against the ONLY verified
data about a real business. List every factual claim THE PAGE MAKES that is NOT supported
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

CRITICAL — you are checking the PAGE, not the data:
- Flag a claim ONLY if the PAGE HTML itself states it. For every flag you MUST copy the
  exact words FROM THE PAGE into "page_quote" (verbatim, at least a few words). If you
  cannot copy the claim from the PAGE HTML, it is not on the page — do NOT flag it.
- The customer reviews inside VERIFIED DATA are customer-attested facts, NOT page claims.
  NEVER flag something merely because it appears in a review; only flag it if the PAGE
  presents it as the business's own claim (with a page_quote to prove it).

VERIFIED DATA:
{json.dumps(facts, indent=2)}

PAGE HTML:
{trimmed}

Return STRICT JSON only:
{{"ok": true|false, "unsupported": [{{"claim": "<what>", "page_quote": "<exact words copied from the PAGE HTML>", "why": "<why unsupported>"}}]}}"""
    try:
        client = _claude()
        resp = client.messages.create(
            model=config.MODEL_CLASSIFIER, max_tokens=800,
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
        # Anchor every flag to text actually on the page: the fact-checker confabulates
        # amenities it sees in the source REVIEWS (parking, beverage station, candy bowl)
        # and misattributes them to the page. Drop any flag whose page_quote isn't really
        # in the page — that filters the confabulations while keeping real on-page claims.
        page_norm = _alnum(BeautifulSoup(html, "lxml").get_text(" ") + " " +
                           " ".join(t.get("alt", "") + " " + t.get("aria-label", "")
                                    for t in BeautifulSoup(html, "lxml").find_all()))
        kept, dropped = [], []
        for u in (data.get("unsupported") or []):
            if isinstance(u, str):
                # legacy string form — keep (no quote to verify against)
                if u.strip():
                    kept.append(u.strip())
                continue
            if not isinstance(u, dict):
                continue
            claim = (u.get("claim") or "").strip()
            quote = (u.get("page_quote") or "").strip()
            why = (u.get("why") or "").strip()
            q_norm = _alnum(quote)
            if q_norm and len(q_norm) >= 6 and q_norm in page_norm:
                kept.append(f"{claim} - {why}" if why else claim)
            else:
                dropped.append(f"{claim} (quote not on page: {quote!r})")
        if dropped:
            log.info("fact_check dropped %d confabulated flag(s): %s",
                     len(dropped), "; ".join(dropped)[:300])
        return (not kept), "; ".join(kept)
    except (AttributeError, json.JSONDecodeError):
        log.warning("fact_check unparseable response; passing by default")
        return True, ""
    except Exception:
        log.exception("fact_check failed")
        return True, ""


def _core_brand(name: str) -> str:
    """Brand portion of a listing name, dropping a parenthetical location and any
    provider/credential suffix ("Syringe Aesthetics - Manuel Guillen, FNP" ->
    "Syringe Aesthetics"; "I Love My Look Aesthetics (Viridian, Arlington Location)"
    -> "I Love My Look Aesthetics"). The generator correctly brands to this, so the
    gate must too."""
    name = re.sub(r"\s*\([^)]*\)", "", name).strip()  # drop "(Viridian, ...)"
    return re.split(r"\s+[-–—|]\s+|,\s*", name, maxsplit=1)[0].strip()


_GENERIC_BRAND_WORDS = {
    "med", "medspa", "spa", "spas", "aesthetic", "aesthetics", "salon", "clinic",
    "wellness", "school", "llc", "inc", "co", "the", "and", "of", "skin", "beauty",
    "studio", "center", "centre", "institute", "bar", "lounge", "medical", "cosmetic",
    "cosmetics", "laser", "day", "group", "care", "health",
}


def _brand_present(name: str, page_lower: str) -> bool:
    """True if the page carries the business's brand. Matches on the DISTINCTIVE lead
    words (ignoring generic 'med spa / aesthetics / ...' descriptors), with &<->and
    normalized, so a page that brands itself "Fit & Fancy" satisfies the listing
    "Fit & Fancy Med Spa & School" instead of triggering a false 'name missing' retry."""
    def toks(s: str) -> list[str]:
        return [t for t in re.split(r"[^a-z0-9]+", s.lower().replace("&", " and ")) if t]
    page = set(toks(page_lower))
    distinctive = [t for t in toks(_core_brand(name))
                   if t not in _GENERIC_BRAND_WORDS and len(t) > 1]
    if distinctive:
        return all(t in page for t in distinctive[:2])  # first two lead words suffice
    # entirely-generic brand (e.g. "MedSpa") — fall back to the joined core string
    return "".join(toks(_core_brand(name))) in re.sub(r"[^a-z0-9]", "", page_lower)


def structural_gate(html: str, lead, images: list[str],
                    source_text: str = "") -> str | None:
    """Doc 01 STEP 3 hard-defect gate. Returns None if OK, else the failure."""
    lower = html.lower()
    if not _brand_present(lead["business_name"], lower):
        return "business name missing"
    if "lorem ipsum" in lower or "placeholder" in lower:
        return "placeholder text present"
    if not re.search(r"<meta\b[^>]*\bname\s*=\s*[\"']viewport[\"']", lower):
        return "no viewport meta"
    # Check for real signals, not magic words — a good page that titles its sections
    # "Get in Touch" or "Our Treatments" must NOT be falsely rejected into a slow retry.
    if "<h1" not in lower:
        return "no <h1> headline (hero)"
    if not any(s in lower for s in
               ("service", "treatment", "what we offer", "our menu", "procedure", "offering")):
        return "missing services section"
    phone_digits = re.sub(r"\D", "", lead["phone"] or "")
    page_digits = re.sub(r"\D", "", lower)
    has_phone = len(phone_digits) >= 7 and phone_digits[-7:] in page_digits
    if not has_phone and not any(s in lower for s in
            ("contact", "get in touch", "visit us", "book", "appointment", "call us",
             "find us", "location", "reach us")):
        return "missing contact section"
    srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)', html, re.I)
    for src in srcs:
        if src.startswith("data:"):
            continue
        if not any(src.startswith(u[:40]) or src == u for u in images):
            return f"invented image URL: {src[:80]}"
    if images and not srcs:
        return "good real photo available but page fell back to no images"
    # NOTE: unsourced superlatives are no longer a hard gate failure — they are scrubbed
    # deterministically in build_one (_scrub_superlatives) BEFORE this gate, so they can
    # never kill a lead (leads 214/234 died here on "best"/"#1"). Invented amenities /
    # credentials are caught by fact_check + degrade-not-die, not by a fatal gate.
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


# content-type -> extension for images we self-host (see _localize_images)
_IMG_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
            "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif",
            "image/svg+xml": ".svg"}


def _localize_images(html: str, out_dir) -> tuple[str, list[str]]:
    """Download every external image the FINAL page references into `out_dir` and
    rewrite the HTML to point at the local copies (relative filenames). This makes
    each sample self-contained: immune to third-party CDN URL-rot in a sent email
    (a prospect opening the link days later never sees a broken hero), and compliant
    with sources that forbid hotlinking (e.g. Pixabay — download-and-host required).

    Also writes `out_dir/sources.json` (local file -> origin URL) so the per-lead
    learning loop can still exclude a reviewer-rejected photo on a rebuild even though
    the shipped HTML now shows local filenames (see _images_used_in_build).

    Any download that fails leaves the original URL in place — never breaks an image.
    Returns (rewritten_html, origin_urls_downloaded)."""
    urls: list[str] = []
    seen = set()
    for u in re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I):
        if u not in seen:
            seen.add(u); urls.append(u)
    for ss in re.findall(r'srcset=["\']([^"\']+)["\']', html, re.I):
        for part in ss.split(","):
            u = part.strip().split(" ")[0].strip()
            if u and u not in seen:
                seen.add(u); urls.append(u)
    for u in re.findall(r'url\(\s*["\']?([^"\')]+?)["\']?\s*\)', html, re.I):
        if u not in seen:
            seen.add(u); urls.append(u)
    external = [u for u in urls if u.startswith("http")]
    if not external:
        return html, []
    sources: dict[str, str] = {}
    downloaded: list[str] = []
    for i, url in enumerate(external):
        fetch_url = url.replace("&amp;", "&")  # attributes may HTML-escape query &s
        try:
            r = requests.get(fetch_url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        except requests.RequestException:
            continue
        if r.status_code != 200 or not r.content:
            log.warning("localize: %s for %s", r.status_code, fetch_url[:80])
            continue
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        ext = _IMG_EXT.get(ctype)
        if not ext:
            m = re.search(r"\.(jpe?g|png|webp|gif|avif|svg)(?:\?|$)", url, re.I)
            ext = ("." + m.group(1).lower().replace("jpeg", "jpg")) if m else ".jpg"
        fname = f"img{i}{ext}"
        try:
            (out_dir / fname).write_bytes(r.content)
        except OSError:
            log.warning("localize: could not write %s", fname)
            continue
        html = html.replace(url, fname)
        sources[fname] = url
        downloaded.append(url)
    if sources:
        try:
            (out_dir / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
        except OSError:
            log.warning("localize: could not write sources.json")
    return html, downloaded


def _ship_sample(conn, lead, html: str, image_source: str,
                 fact_flag_note: str | None = None) -> bool:
    """Persist a finished sample and transition the lead to SAMPLE_BUILT — the
    shared tail for both the single-shot and best-of-N build paths."""
    slug = unique_slug(conn, lead)
    out_dir = config.SAMPLES_CACHE_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    # remember which stock photos this sample placed (by their REMOTE url) BEFORE
    # self-hosting rewrites the srcs to local files — so the next build picks
    # different stock (no repeated stock across samples).
    if image_source in ("stock", "mixed"):
        remember_stock(re.findall(r'<img[^>]+src=["\']([^"\']+)', html, re.I))
    # self-host every external image (URL-rot immunity + no-hotlink compliance)
    html, localized = _localize_images(html, out_dir)
    if localized:
        log.info("lead %s: self-hosted %d image(s)", lead["id"], len(localized))
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    url = f"{config.SAMPLE_BASE_URL}/{slug}"
    notes = fact_flag_note
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            notes = f"{notes + '; ' if notes else ''}url_unverified ({r.status_code})"
    except requests.RequestException:
        notes = f"{notes + '; ' if notes else ''}url_unverified (tunnel unreachable at build time)"
    if notes:
        log.warning("lead %s: %s", lead["id"], notes)
    db.transition(conn, lead["id"], "SAMPLE_BUILT", f"images={image_source}",
                  sample_url=url, sample_slug=slug, sample_built_at=db.now(),
                  image_source=image_source,
                  notes=notes if notes else lead["notes"])
    conn.commit()
    return True


# --- Best-of-N (Pillar 1+2): generate a diverse batch, ship the tournament winner --
# The generator is high-variance: the SAME lead yields a great build and a broken one
# from identical inputs (headline slapped across a face vs. a clean split hero). The
# single-shot path shipped whatever one draw produced. Best-of-N instead draws N
# candidates (one per design language), renders each, and ships the one a pairwise
# vision judge picks — turning that variance from a liability into a selection asset.
# Validated in calibration: a pairwise judge picked the human-preferred build 4/4 on
# real dental pairs, order-invariant. Absolute 1-5 scoring did NOT (it called human
# 1s a 6); pairwise "which is better?" is the reliable form, so the tournament asks that.

BON_JUDGE_PROMPT = (
    "Two AI-generated SAMPLE homepages for the SAME local business ({biz}), each built "
    "to win them as a cold-outreach client. Sample A first (desktop fold, then full "
    "page), then sample B (fold, then full page).\n\n"
    "Pick the one that would best make the OWNER stop, think \"that's my business, but "
    "better,\" and reply to the email. Judge, in priority order:\n"
    "- HERO in the first 3 seconds, and whether the hero PHOTO is an asset or a "
    "liability. Asset: a warm, appealing, on-brand image that fits THIS business. "
    "Liability: a wrong-modality photo, a dim/dated/cluttered shot, a bare clinical or "
    "storefront/parking shot, a posed stock model that reads fake, or headline text "
    "laid across a person's face.\n"
    "- DESIGN CRAFT: premium and designed vs. a flat template ('clean but plain' loses).\n"
    "- COPY that sounds written for THIS business, not filler.\n"
    "- CREDIBILITY through the owner's eyes: nothing embarrassing or overclaimed.\n\n"
    "Return STRICT JSON only: {{\"winner\":\"A\" or \"B\",\"reason\":\"<the single deciding "
    "factor, <=18 words>\"}}")


def _render_two(browser, uri: str) -> dict:
    """Desktop fold + full-page JPEG bytes for one candidate (reduced-motion so
    scroll-reveal sections are settled, not captured mid-fade as blank blocks)."""
    shots = {}
    for label, full in (("fold", False), ("full", True)):
        page = browser.new_page(viewport={"width": 1280, "height": 860},
                                reduced_motion="reduce")
        try:
            page.goto(uri, wait_until="networkidle", timeout=45_000)
            page.wait_for_timeout(900)
            if full:
                h = min(page.evaluate("document.body.scrollHeight"), 4200)
                shots[label] = page.screenshot(type="jpeg", quality=58, full_page=True,
                                               clip={"x": 0, "y": 0, "width": 1280, "height": h})
            else:
                shots[label] = page.screenshot(type="jpeg", quality=62, full_page=False)
        finally:
            page.close()
    return shots


def _pairwise_pick(conn, lead, shots_a: dict, shots_b: dict) -> str:
    """Ask the vision judge which of two rendered candidates is better. Returns
    'a' or 'b' (defaults to 'a' on any parse/API failure — a stable, harmless tie-break)."""
    biz = f"{lead['business_name']} ({lead['category'] or 'local business'})"

    def _imgs(shots):
        return [{"type": "image", "source": {"type": "base64",
                 "media_type": "image/jpeg", "data": base64.b64encode(b).decode()}}
                for b in (shots.get("fold"), shots.get("full")) if b]

    content = [{"type": "text", "text": BON_JUDGE_PROMPT.format(biz=biz)},
               {"type": "text", "text": "=== SAMPLE A — fold ==="}, *_imgs({"fold": shots_a.get("fold")}),
               {"type": "text", "text": "=== SAMPLE A — full page ==="}, *_imgs({"full": shots_a.get("full")}),
               {"type": "text", "text": "=== SAMPLE B — fold ==="}, *_imgs({"fold": shots_b.get("fold")}),
               {"type": "text", "text": "=== SAMPLE B — full page ==="}, *_imgs({"full": shots_b.get("full")})]
    try:
        client = _claude()
        resp = client.messages.create(
            model=config.MODEL_QUALITY, max_tokens=200,
            system="You are a decisive, brutally honest design director grading sample "
                   "websites for a cold-outreach pipeline. Judge like a picky business owner.",
            messages=[{"role": "user", "content": content}])
        costs.record(conn, "claude", "sample_bon_judge",
                     costs.claude_cost(config.MODEL_QUALITY, resp.usage.input_tokens,
                                       resp.usage.output_tokens),
                     lead_id=lead["id"], tokens_in=resp.usage.input_tokens,
                     tokens_out=resp.usage.output_tokens)
        conn.commit()
        data = json.loads(re.search(r"\{.*\}", resp.content[0].text, re.S).group(0))
        winner = "b" if str(data.get("winner", "A")).strip().upper().startswith("B") else "a"
        log.info("lead %s judge: %s (%s)", lead["id"], winner.upper(),
                 str(data.get("reason", ""))[:120])
        return winner
    except Exception:
        log.exception("lead %s pairwise judge failed; defaulting to A", lead["id"])
        return "a"


def _tournament(conn, lead, htmls: list[str]) -> int:
    """Render every candidate and run a round-robin pairwise tournament; return the
    index of the candidate with the most wins (ties -> lowest index). Raises on a
    Playwright/render failure so the caller can fall back."""
    from playwright.sync_api import sync_playwright  # laptop-only
    tmp = config.LOGS_DIR / "bon"
    tmp.mkdir(parents=True, exist_ok=True)
    shots: dict[int, dict] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for i, html in enumerate(htmls):
                f = tmp / f"{lead['id']}_{i}.html"
                f.write_text(html, encoding="utf-8")
                shots[i] = _render_two(browser, f.as_uri())
        finally:
            browser.close()
    wins = {i: 0 for i in range(len(htmls))}
    for a, b in itertools.combinations(range(len(htmls)), 2):
        if costs.check(conn, "claude", BON_JUDGE_USD) == "block":
            log.warning("lead %s: budget blocked mid-tournament", lead["id"])
            break
        winner = _pairwise_pick(conn, lead, shots[a], shots[b])
        wins[a if winner == "a" else b] += 1
    log.info("lead %s tournament wins: %s", lead["id"], wins)
    return max(range(len(htmls)), key=lambda i: (wins[i], -i))


def _try_best_of_n(conn, lead, *, brief, brand, site_facts, services, profile,
                   source_text, content, images, image_source, image_notes,
                   allowed_images, system, feedback_seed) -> bool | None:
    """Generate BEST_OF_N diverse candidates and ship the tournament winner.

    Returns True/False when it produces a definitive build outcome, or None to tell
    build_one to fall back to the single-shot loop — used when Playwright is missing
    (the VM) or when too few candidates clear the structural gate to select among."""
    try:
        import playwright.sync_api  # noqa: F401 — VM lacks it; degrade to single-shot
    except Exception:
        return None
    start = int(hashlib.sha1(str(lead["id"]).encode()).hexdigest(), 16) % len(DESIGN_LANGUAGES)
    n = min(BEST_OF_N, len(DESIGN_LANGUAGES))
    seed = "\n\n".join(feedback_seed)
    candidates: list[tuple[str, dict, str]] = []   # (html, design_language, archetype)
    for i in range(n):
        dl = DESIGN_LANGUAGES[(start + i) % len(DESIGN_LANGUAGES)]
        arch = ARCHETYPES[(lead["id"] + i) % len(ARCHETYPES)]
        base_prompt = build_prompt(lead, brief, arch, images, content, brand,
                                   image_source, image_notes, dl)
        raw = _call_claude(conn, system, (base_prompt, seed),
                           lead["id"], f"sample_generate_bon{i + 1}")
        if raw is None:
            break  # budget blocked — stop drawing candidates
        cand = _scrub_superlatives(extract_html(raw), source_text)
        if structural_gate(cand, lead, allowed_images, source_text) is None:
            candidates.append((cand, dl, arch))
            log.info("lead %s bon cand %d (%s): gate PASS", lead["id"], i + 1, dl["name"])
        else:
            log.info("lead %s bon cand %d (%s): gate FAIL", lead["id"], i + 1, dl["name"])
    if not candidates:
        return None  # nothing to select — single-shot has retry-with-feedback
    if len(candidates) == 1:
        best = 0
    else:
        try:
            best = _tournament(conn, lead, [c[0] for c in candidates])
        except Exception:
            log.exception("lead %s: tournament render/judge failed; using first passer",
                          lead["id"])
            best = 0
    winner_html, win_dl, win_arch = candidates[best]
    log.info("lead %s: best-of-%d winner = cand %d (%s)", lead["id"],
             len(candidates), best + 1, win_dl["name"])

    # honesty pass on the WINNER only (the tournament already selected for design/hero).
    # Facts are code-assembled, so a flag here is a rare prose slip — try one targeted
    # fix, then degrade-not-die (ship flagged for factor-F grading) rather than kill it.
    fact_ok, fact_fixes = fact_check(conn, lead, profile, winner_html, site_facts)
    if fact_ok or not fact_fixes:
        return _ship_sample(conn, lead, winner_html, image_source, None)
    log.info("lead %s: winner fact-flagged, one fix pass: %s", lead["id"], fact_fixes[:160])
    fix_fb = list(feedback_seed) + [
        "FACT-CHECK — your marketing copy stated these claims, which are NOT in LOCKED "
        "CONTENT and are therefore forbidden. DELETE each one outright (do not soften or "
        "reword — remove the phrase/sentence):\n" + fact_fixes +
        "\nRegenerate the COMPLETE page with ALL required sections and the images — remove "
        "only the flagged claims, keep every LOCKED CONTENT item exactly."]
    fix_prompt = build_prompt(lead, brief, win_arch, images, content, brand,
                              image_source, image_notes, win_dl)
    raw = _call_claude(conn, system, (fix_prompt, "\n\n".join(fix_fb)),
                       lead["id"], "sample_generate_bon_fix")
    if raw is not None:
        fixed = _scrub_superlatives(extract_html(raw), source_text)
        if structural_gate(fixed, lead, allowed_images, source_text) is None:
            ok2, fixes2 = fact_check(conn, lead, profile, fixed, site_facts)
            note = None if ok2 else "fact_flagged: " + fixes2[:180]
            return _ship_sample(conn, lead, fixed, image_source, note)
    # fix regressed or was blocked — ship the winner flagged (SHIP_WHEN_FACT_FLAGGED)
    if SHIP_WHEN_FACT_FLAGGED:
        return _ship_sample(conn, lead, winner_html, image_source,
                            "fact_flagged: " + fact_fixes[:180])
    db.transition(conn, lead["id"], "SAMPLE_FAILED", "fact-check unresolved: " + fact_fixes[:140])
    conn.commit()
    return False


def build_one(conn, lead) -> bool:
    profile = json.loads(lead["source_profile"] or "{}")
    brief = niche_brief(lead["category"], lead["business_name"])
    archetype = ARCHETYPES[lead["id"] % len(ARCHETYPES)]
    brand = extract_brand(lead["existing_website"]) if lead["existing_website"] else {}
    if brand:
        log.info("lead %s brand: colors=%s fonts=%s logo=%s", lead["id"],
                 brand.get("colors"), brand.get("fonts"), bool(brand.get("logo")))
    # their REAL services + tagline from their own site — the substance that lets the
    # copy be specific instead of generic mood-filler (strictly sourced; cached per-URL).
    # Extracted BEFORE image selection so the real services drive the stock query and
    # the vision service-match check (a sauna won't slip onto an injectables clinic).
    site_facts = (extract_site_facts(conn, lead, lead["existing_website"])
                  if lead["existing_website"] else {})
    services = site_facts.get("services") or []
    # a plain "what this business does" line for the vision QC's service-match verdict;
    # the name often encodes the modality ("Miss Botox") when there are no listed services
    service_desc = lead["business_name"] or lead["category"] or "local business"
    desc_extra = [b for b in (lead["category"],
                              ("offering " + ", ".join(services[:8])) if services else "")
                  if b]
    if desc_extra:
        service_desc += " (" + "; ".join(desc_extra) + ")"
    # PER-LEAD LEARNING: read this lead's most recent HUMAN grade and feed it back into
    # this rebuild. Without this, a hand-grade that says "replace the hero" changes
    # nothing — build_one starts fresh and re-picks the same photo (Austin's "they aren't
    # actually learning"). Here we (1) exclude the exact image(s) a low imagery/hero
    # grade rejected, and (2) surface the reviewer's CHANGE note to the generator below.
    prior = conn.execute(
        "SELECT imagery, hero, notes, sample_slug FROM grades WHERE lead_id=? "
        "ORDER BY id DESC LIMIT 1", (lead["id"],)).fetchone()
    exclude_imgs: set[str] = set()
    reviewer_note = (prior["notes"] or "").strip() if prior else ""
    if prior and ((prior["imagery"] or 5) <= 3 or (prior["hero"] or 5) <= 3):
        exclude_imgs = _images_used_in_build(prior["sample_slug"])
        if exclude_imgs:
            log.info("lead %s: excluding %d reviewer-rejected image(s) from rebuild",
                     lead["id"], len(exclude_imgs))
    # image source ladder — content-quality (not just aspect ratio) picks the source;
    # returns survivor URLs best-hero-first plus the vision notes the generator needs
    images, image_source, image_notes = select_images(
        conn, lead, profile, brief, service_desc, services, exclude=exclude_imgs)
    # the real logo is a legitimate image even if it isn't in the photo `images`
    allowed_images = images + ([brand["logo"]] if brand.get("logo") else [])
    # everything we can truthfully say about them — the claim gate allows a banned
    # word only if it appears here (e.g. a review literally saying "best place");
    # their real services/tagline are sourced facts, so include them here too.
    source_text = " ".join([
        lead["business_name"] or "", lead["category"] or "",
        profile.get("summary") or "",
        *(site_facts.get("services") or []),
        site_facts.get("tagline") or "",
        *[r.get("text", "") for r in (profile.get("reviews") or [])],
    ])

    system = "You are an expert web designer."
    style_ctx = _style_context(niche_key(lead["category"], lead["business_name"]))
    if style_ctx:
        system += "\n\n" + style_ctx

    # FACTS IN CODE: assemble every verifiable fact (name, rating line, hours,
    # phone, address, verbatim 4-5★ testimonials) here so the generator places them
    # unmodified and can only invent inside its own marketing prose — which the
    # fact-check + banned-claim gate then police. This is what stops the churn:
    # attempt 1 is honest by construction, so retries become the exception.
    content = assemble_content_blocks(lead, profile, site_facts)
    # rotate a distinct design language per lead so samples stop looking templated.
    # hash the id (not id % N) so consecutive/clustered ids still spread evenly.
    dl_idx = int(hashlib.sha1(str(lead["id"]).encode()).hexdigest(), 16) % len(DESIGN_LANGUAGES)
    design_language = DESIGN_LANGUAGES[dl_idx]
    log.info("lead %s design language: %s", lead["id"], design_language["name"])
    base_prompt = build_prompt(lead, brief, archetype, images, content, brand,
                               image_source, image_notes, design_language)
    feedback: list[str] = []
    # seed the FIRST attempt with the human reviewer's note on the previous version of
    # THIS page — the highest-signal guidance there is. Any rejected image is already
    # gone from `images`; this carries the copy/hero/design feedback into generation.
    if reviewer_note:
        feedback.append(
            "A HUMAN REVIEWER graded the PREVIOUS version of THIS EXACT page and gave "
            "the single most important change to make. Treat it as top priority and do "
            "NOT repeat the mistake (if it named the hero photo, the rejected image has "
            "already been removed from your options — pick a different one):\n"
            + reviewer_note)

    # BEST-OF-N (Pillar 1+2): draw N diverse candidates and ship the pairwise-
    # tournament winner. Returns None to fall through to the single-shot loop below
    # (Playwright missing on the VM, or too few candidates cleared the gate to select).
    if BEST_OF_N > 1:
        outcome = _try_best_of_n(
            conn, lead, brief=brief, brand=brand, site_facts=site_facts,
            services=services, profile=profile, source_text=source_text,
            content=content, images=images, image_source=image_source,
            image_notes=image_notes, allowed_images=allowed_images,
            system=system, feedback_seed=feedback)
        if outcome is not None:
            return outcome
        log.info("lead %s: best-of-N unavailable — single-shot fallback", lead["id"])

    html, failure = None, "no attempts made"
    last_valid, last_flags = None, ""   # best gate-passed candidate + its residual flags
    critiqued = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        final_attempt = attempt == MAX_ATTEMPTS
        raw = _call_claude(conn, system, (base_prompt, "\n\n".join(feedback)),
                           lead["id"], f"sample_generate_a{attempt}")
        if raw is None:
            failure = "claude budget blocked"
            break
        candidate = extract_html(raw)
        # deterministically remove unsourced ranking superlatives BEFORE the gate — the
        # model keeps writing "best"/"#1" no matter the prompt ban, and failing the gate
        # on them just burns a retry (or kills the lead). Scrubbing keeps the page honest.
        candidate = _scrub_superlatives(candidate, source_text)
        failure = structural_gate(candidate, lead, allowed_images, source_text)
        if failure:
            log.info("lead %s attempt %s failed gate: %s", lead["id"], attempt, failure)
            feedback.append(
                f"PREVIOUS ATTEMPT FAILED THE QUALITY GATE: {failure}. Fix that — and "
                "regenerate the COMPLETE page with ALL required sections (hero, "
                "services, why-choose-us, testimonials, hours/location, contact CTA) "
                "and the images; fix only what's named, never drop anything else.")
            continue
        # grounding fact-check — the facts are code-locked, so anything flagged here
        # is invention inside the model's own prose (an amenity, a superlative).
        fact_ok, fact_fixes = fact_check(conn, lead, profile, candidate, site_facts)
        if not fact_ok and fact_fixes:
            log.info("lead %s attempt %s fact-check flagged: %s",
                     lead["id"], attempt, fact_fixes[:200])
            last_valid, last_flags = candidate, fact_fixes  # honest scaffold, prose slip
            feedback.append(
                "FACT-CHECK — your marketing copy stated these claims, which are NOT in "
                "LOCKED CONTENT and are therefore forbidden. DELETE each one outright "
                "(do not soften or reword — remove the phrase/sentence):\n" + fact_fixes +
                "\nRegenerate the COMPLETE page with ALL required sections and the images "
                "— remove only the flagged claims, keep every LOCKED CONTENT item exactly.")
            failure = "fact-check unresolved: " + fact_fixes[:140]
            continue
        # one critique-driven refine per lead (skipped on the final attempt, which has
        # no budget left to act on it): FAIL is mandatory, a PASS with any core factor
        # at 4 ("fine, forgettable") earns the same single polish pass.
        if not critiqued and not final_attempt:
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

    # degrade-not-die: if no attempt came back fact-clean but we have a structurally
    # sound candidate, ship it flagged (its facts are code-assembled and honest; the
    # residual is a prose slip for factor-F grading to catch) rather than killing the
    # lead. Two builds already spent — a dead lead is the worst outcome.
    fact_flag_note = None
    if html is None and SHIP_WHEN_FACT_FLAGGED and last_valid is not None:
        html = last_valid
        fact_flag_note = "fact_flagged: " + last_flags[:180]
        log.warning("lead %s: shipping fact-flagged sample for review — %s",
                    lead["id"], last_flags[:200])

    if html is None:
        db.transition(conn, lead["id"], "SAMPLE_FAILED", failure or "unknown")
        conn.commit()
        return False

    return _ship_sample(conn, lead, html, image_source, fact_flag_note)


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
