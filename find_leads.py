"""Stage 1 — find + qualify leads, discover emails, write to DB.

Implements find-leads-brief.md against the Places API (New, v1).
Every paid call goes through the costs.check gate first.
"""
import json
import logging
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import config
import costs
import db

log = logging.getLogger(__name__)

# Legacy Places API — the project has this enabled (Places API New is not).
# If Google ever sunsets these endpoints, enable "Places API (New)" in the
# GCP console and swap back to places.googleapis.com/v1.
PLACES_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

DETAIL_FIELDS = ",".join([
    "formatted_phone_number", "website", "review", "photo",
    "opening_hours", "editorial_summary",
])

SOCIAL_HOSTS = ("facebook.com", "instagram.com", "linktr.ee", "m.facebook.com")
FREE_SITE_HOSTS = ("wixsite.com", "weebly.com", "godaddysites.com",
                   "business.site", "square.site", "wordpress.com")
FRANCHISE_HINTS = ("franchise", "corporate")

# Domain-parking / for-sale hosts + on-page markers. A parked domain means the
# business has NO real site (the listing's "website" is just a placeholder), so
# it must qualify as NO_SITE, never OUTDATED — otherwise the empty parking stub
# (no viewport meta) trips the "not responsive" check and we pitch "your site is
# outdated" to someone who knows they have no site at all.
PARKING_HOSTS = ("sedoparking", "parkingcrew", "bodis", "afternic", "hugedomains",
                 "dan.com", "uniregistry", "above.com", "sav.com", "domainmarket",
                 "cashparking", "godaddy")
PARKED_MARKERS = ("this domain is parked", "is parked free", "parked free, courtesy",
                  "buy this domain", "the domain is for sale", "domain for sale",
                  "domain is for sale", "get this domain", "courtesy of godaddy",
                  "domain may be for sale", "/cgi-bin/parked", "parkingcrew",
                  "sedoparking",
                  # technical markers in raw HTML when the visible "parked" text
                  # is JS-rendered (e.g. GoDaddy's /lander parking page)
                  "parking-lander", "lander_system", 'ap:"parking"',
                  "wsimg.com/parking")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BAD_EMAIL_BITS = ("example.", "sentry", "wixpress", "@2x", ".png", ".jpg",
                  ".gif", "godaddy", "no-reply", "noreply", "yourdomain")

STATE_TZ = {
    "CT": "America/New_York", "DE": "America/New_York", "FL": "America/New_York",
    "GA": "America/New_York", "IN": "America/Indiana/Indianapolis",
    "KY": "America/New_York", "ME": "America/New_York", "MD": "America/New_York",
    "MA": "America/New_York", "MI": "America/Detroit", "NH": "America/New_York",
    "NJ": "America/New_York", "NY": "America/New_York", "NC": "America/New_York",
    "OH": "America/New_York", "PA": "America/New_York", "RI": "America/New_York",
    "SC": "America/New_York", "VT": "America/New_York", "VA": "America/New_York",
    "WV": "America/New_York", "DC": "America/New_York",
    "AL": "America/Chicago", "AR": "America/Chicago", "IL": "America/Chicago",
    "IA": "America/Chicago", "KS": "America/Chicago", "LA": "America/Chicago",
    "MN": "America/Chicago", "MS": "America/Chicago", "MO": "America/Chicago",
    "NE": "America/Chicago", "ND": "America/Chicago", "OK": "America/Chicago",
    "SD": "America/Chicago", "TN": "America/Chicago", "TX": "America/Chicago",
    "WI": "America/Chicago",
    "AZ": "America/Phoenix", "CO": "America/Denver", "ID": "America/Boise",
    "MT": "America/Denver", "NM": "America/Denver", "UT": "America/Denver",
    "WY": "America/Denver",
    "CA": "America/Los_Angeles", "NV": "America/Los_Angeles",
    "OR": "America/Los_Angeles", "WA": "America/Los_Angeles",
    "AK": "America/Anchorage", "HI": "Pacific/Honolulu",
}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}

# responses that usually mean "the site blocked our client", not "dead site"
BOT_BLOCK_STATUSES = {401, 403, 406, 429, 503}


# ---------------------------------------------------------------- Places

def search_places(conn, query: str, max_results: int = 60) -> list[dict]:
    """Text Search. Returns place dicts in a normalized shape.

    NOTE: legacy next_page_token pagination is BROKEN for this project — the
    token persistently returns INVALID_REQUEST regardless of wait or encoding
    (Google has degraded the legacy API for newly-created projects; verified
    2026-07-07). So one call yields ~20 results and breadth must come from
    multiple query variants / areas at the top_up level, not pagination. We still
    attempt one page-2 fetch cheaply in case Google ever restores it, then bail
    fast rather than burning time on a token that won't work."""
    out, token, page = [], None, 0
    while len(out) < max_results:
        if costs.check(conn, "places", costs.PLACES_TEXT_SEARCH_USD) == "block":
            log.warning("places budget blocked; stopping search early")
            break
        params = {"key": config.GOOGLE_PLACES_API_KEY}
        params["pagetoken" if token else "query"] = token or query
        if token:
            time.sleep(2)  # give the token a beat; bail below if still invalid
        r = requests.get(PLACES_SEARCH_URL, params=params, timeout=30)
        r.raise_for_status()
        costs.record(conn, "places", "text_search", costs.PLACES_TEXT_SEARCH_USD)
        data = r.json()
        status = data.get("status")
        if status == "INVALID_REQUEST" and token:
            log.info("places pagination unavailable (next_page_token rejected); "
                     "keeping %d results from page 1", len(out))
            break
        if status not in ("OK", "ZERO_RESULTS"):
            log.warning("places search status %s: %s", status,
                        (data.get("error_message") or "")[:200])
            break
        for res in data.get("results", []):
            out.append({
                "id": res.get("place_id"),
                "displayName": {"text": res.get("name", "")},
                "formattedAddress": res.get("formatted_address", ""),
                "rating": res.get("rating"),
                "userRatingCount": res.get("user_ratings_total"),
                "businessStatus": res.get("business_status"),
                "primaryType": (res.get("types") or [None])[0],
            })
        page += 1
        token = data.get("next_page_token")
        log.info("places page %s: +%d results (total %d); more=%s",
                 page, len(data.get("results", [])), len(out), bool(token))
        if not token:
            break
    return out[:max_results]


# Query-term variants per niche. Since pagination is dead, breadth comes from
# running several phrasings (each ~20 results) and unioning them — this also
# surfaces DIFFERENT businesses than a single ranked list would. Extend as new
# niches are campaigned; the fallback is just the configured niche verbatim.
NICHE_QUERY_VARIANTS = {
    "med spa": ["med spa", "medical spa", "medical aesthetics", "botox clinic",
                "aesthetic clinic", "injectables"],
    "dentist": ["dentist", "dental office", "family dentistry",
                "cosmetic dentist", "dental clinic"],
    "chiropractor": ["chiropractor", "chiropractic clinic", "spine clinic"],
    "law": ["law firm", "attorney", "lawyer", "legal office"],
    "accountant": ["accountant", "cpa firm", "tax preparation", "bookkeeping"],
}


def niche_queries(niche: str) -> list[str]:
    key = (niche or "").strip().lower()
    for k, variants in NICHE_QUERY_VARIANTS.items():
        if k in key or key in k:
            return variants
    return [niche] if niche else []


def search_area(conn, niche: str, location: str) -> list[dict]:
    """All distinct places for a niche in one area, across term variants.

    max_results=20 so we take just page 1 per query (pagination is dead) and
    don't waste a call attempting page 2."""
    seen, out = set(), []
    for term in niche_queries(niche):
        for p in search_places(conn, f"{term} in {location}", max_results=20):
            pid = p.get("id")
            if pid and pid not in seen:
                seen.add(pid)
                out.append(p)
        time.sleep(0.3)  # small courtesy gap between queries
    log.info("search_area %s / %s: %d distinct places across %d queries",
             niche, location, len(out), len(niche_queries(niche)))
    return out


def place_details(conn, place_id: str) -> dict:
    """Details call — the expensive one; provides phone/website/reviews/photos."""
    if costs.check(conn, "places", costs.PLACES_DETAILS_USD) == "block":
        return {}
    r = requests.get(PLACES_DETAILS_URL, params={
        "key": config.GOOGLE_PLACES_API_KEY, "place_id": place_id,
        "fields": DETAIL_FIELDS,
    }, timeout=30)
    if r.status_code != 200:
        log.warning("place details %s -> %s", place_id, r.status_code)
        return {}
    costs.record(conn, "places", "place_details", costs.PLACES_DETAILS_USD)
    data = r.json()
    if data.get("status") != "OK":
        log.warning("place details status %s", data.get("status"))
        return {}
    return data.get("result", {})


# ---------------------------------------------------------------- qualify

def fetch_site(url: str) -> tuple[requests.Response | None, str]:
    try:
        r = requests.get(url, headers=UA, timeout=12, allow_redirects=True)
        return r, ""
    except requests.RequestException as e:
        return None, type(e).__name__


def looks_parked(resp, _followed: bool = False) -> bool:
    """True if the fetched page is a parked / for-sale domain, not a real site.

    Catches the common case of a real business whose listed "website" is just a
    registrar placeholder. Many parkers (e.g. GoDaddy) serve a near-empty
    JS-redirect shell to a `/lander`; since our fetch runs no JS we follow ONE
    same-site hop to see the actual parking page before judging."""
    host = urlparse(resp.url).netloc.lower()
    if any(h in host for h in PARKING_HOSTS):
        return True
    body = (resp.text or "")[:8000]
    low = body.lower()
    if any(m in low for m in PARKED_MARKERS):
        return True
    if not _followed:
        text_only = re.sub(r"<[^>]+>", "",
                           re.sub(r"<script.*?</script>", " ", body,
                                  flags=re.S | re.I)).strip()
        if len(text_only) < 40:  # JS/meta-refresh shell hiding the real page
            m = (re.search(r'location\.href\s*=\s*["\']([^"\']+)', body, re.I)
                 or re.search(r'http-equiv=["\']refresh["\'][^>]*url=([^"\'>]+)',
                              body, re.I))
            if m:
                nxt = requests.compat.urljoin(resp.url, m.group(1).strip())
                sub, _ = fetch_site(nxt)
                if sub is not None:
                    return looks_parked(sub, _followed=True)
    return False


EST_AI_QUALIFY_USD = 0.02  # worst-case pre-call gate; real cost ~0.005 (Haiku)


def _trim_html_for_ai(html: str, limit: int = 18000) -> str:
    """Strip the noisy/huge parts (scripts, svg, inline data) so the model sees
    structure + styling, and cap length to keep the call cheap."""
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", " ", html,
                     flags=re.S | re.I)
    cleaned = re.sub(r"<svg\b[^>]*>.*?</svg>", " ", cleaned, flags=re.S | re.I)
    cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.S)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:limit]


def ai_looks_dated(conn, html: str, place: dict) -> str | None:
    """Read the raw HTML (no rendering) and CONSERVATIVELY judge whether the
    site looks dated/low-quality enough that a redesign clearly helps. Returns a
    short reason if dated, else None. Catches visually-dated sites that pass the
    technical checks — the qualifier's blind spot without a browser."""
    if not config.ANTHROPIC_API_KEY:
        return None
    if costs.check(conn, "claude", EST_AI_QUALIFY_USD) == "block":
        return None
    snippet = _trim_html_for_ai(html)
    if len(snippet) < 200:  # JS shell / near-empty — can't judge, don't guess
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=config.MODEL_CLASSIFIER, max_tokens=200,
            system="You judge whether a local business's existing website looks "
                   "OUTDATED or low-quality from its raw HTML (no rendering). Be "
                   "CONSERVATIVE: only say dated on CLEAR signals — table-based "
                   "layout, <font>/inline formatting tags, no responsive CSS "
                   "(flex/grid/media queries), ancient frameworks, pre-2015 design "
                   "patterns, broken/sparse structure. If it looks like a competent "
                   "modern site, say NOT dated. A false 'dated' causes a bad pitch, "
                   "which is worse than skipping.",
            messages=[{"role": "user", "content":
                       f"Business: {(place.get('displayName') or {}).get('text','')}\n"
                       f"Judge this site's HTML. Return STRICT JSON only: "
                       f'{{"dated": true|false, "reason": "<=8 words"}}\n\nHTML:\n{snippet}'}],
        )
        costs.record(conn, "claude", "ai_qualify",
                     costs.claude_cost(config.MODEL_CLASSIFIER,
                                       resp.usage.input_tokens,
                                       resp.usage.output_tokens))
        conn.commit()
        data = json.loads(re.search(r"\{.*\}", resp.content[0].text, re.S).group(0))
        if data.get("dated"):
            return (data.get("reason") or "dated design").strip()[:60]
    except (AttributeError, json.JSONDecodeError):
        log.warning("ai_qualify unparseable response")
    except Exception:
        log.exception("ai_qualify failed")
    return None


def qualify(conn, place: dict) -> tuple[str, str]:
    """Returns (NO_SITE | OUTDATED | SKIP, reason)."""
    if place.get("businessStatus") not in (None, "OPERATIONAL"):
        return "SKIP", f"business_status={place.get('businessStatus')}"
    if (place.get("userRatingCount") or 0) < 5:
        return "SKIP", "fewer than 5 reviews"

    website = (place.get("websiteUri") or "").strip()
    if not website:
        return "NO_SITE", "no website on listing"
    host = urlparse(website).netloc.lower()
    if any(s in host for s in SOCIAL_HOSTS):
        return "NO_SITE", f"website field is a social page ({host})"

    resp, err = fetch_site(website)
    if resp is None:
        return "NO_SITE", f"site dead ({err})"
    if resp.status_code in BOT_BLOCK_STATUSES:
        # can't inspect it — never claim "no website" to someone who has one
        return "SKIP", f"site blocked automated check (HTTP {resp.status_code})"
    if resp.status_code >= 400:
        return "NO_SITE", f"site dead (HTTP {resp.status_code})"
    if looks_parked(resp):
        # real business, no real site — the listing points at a parked domain
        return "NO_SITE", "domain parked / for sale (no real website)"

    reasons = []
    final_url = resp.url or website
    if urlparse(final_url).scheme != "https":
        reasons.append("no HTTPS")
    if any(s in urlparse(final_url).netloc.lower() for s in FREE_SITE_HOSTS):
        reasons.append("free template subdomain")

    html = resp.text[:400_000]
    lower = html.lower()
    if "lorem ipsum" in lower:
        reasons.append("lorem ipsum placeholder text")

    # STRONG, objective neglect signals only (no HTTPS, free-builder subdomain,
    # placeholder text) are decisive — each justifies a "we can do better" pitch
    # on its own without a style judgment.
    if reasons:
        return "OUTDATED", " + ".join(reasons[:3])

    # For every other technically-clean site, the conservative AI visual judge is
    # the ONLY path to OUTDATED. It reads the raw HTML (including whether the site
    # is mobile-responsive) and only says "dated" on clear signals, so a good,
    # modern site is never mislabeled.
    #
    # We deliberately do NOT treat a stale copyright-footer year or a missing
    # viewport meta as decisive anymore. A "© 2022" footer is not evidence of a
    # dated design — plenty of modern sites never bump the year — and that rule
    # was producing false OUTDATED pitches to businesses whose live sites are
    # actually strong (e.g. Euro Image, Injexed). The AI judge flags genuine
    # non-responsiveness / dating from the HTML itself.
    ai_reason = ai_looks_dated(conn, html, place)
    if ai_reason:
        return "OUTDATED", f"AI: {ai_reason}"
    return "SKIP", "site looks modern"


# ---------------------------------------------------------------- email

def _clean_emails(candidates: list[str], business_domain: str = "") -> list[str]:
    seen, out = set(), []
    for e in candidates:
        e = e.strip().strip(".").lower()
        if e in seen or any(b in e for b in BAD_EMAIL_BITS):
            continue
        seen.add(e)
        out.append(e)
    # prefer addresses on the business's own domain
    if business_domain:
        out.sort(key=lambda e: 0 if e.endswith("@" + business_domain) else 1)
    return out


def scrape_emails(website: str) -> list[str]:
    """Check the site's home + contact/about pages for addresses."""
    if not website:
        return []
    found: list[str] = []
    resp, _ = fetch_site(website)
    if resp is None:
        return []
    pages = [resp]
    try:
        soup = BeautifulSoup(resp.text, "lxml")
        links = {
            requests.compat.urljoin(resp.url, a["href"])
            for a in soup.find_all("a", href=True)
            if any(k in a["href"].lower() for k in ("contact", "about"))
            or a["href"].lower().startswith("mailto:")
        }
        for href in list(links)[:4]:
            if href.startswith("mailto:"):
                found.append(href[7:].split("?")[0])
                continue
            sub, _ = fetch_site(href)
            if sub is not None:
                pages.append(sub)
    except Exception:
        log.exception("email scrape parse failure on %s", website)
    for page in pages:
        found.extend(EMAIL_RE.findall(page.text[:400_000]))
    domain = urlparse(resp.url).netloc.lower().removeprefix("www.")
    return _clean_emails(found, domain)


def verify_email(conn, email: str, scraped_from_their_site: bool) -> bool:
    """ZeroBounce when configured; otherwise MX check.

    Note: SMTP RCPT probing is deliberately not attempted — GCP blocks
    outbound port 25, so on the VM only MX resolution is possible. Without
    ZeroBounce we therefore only trust addresses actually published on the
    business's own site (not pattern guesses).
    """
    if config.ZEROBOUNCE_API_KEY:
        for attempt in (1, 2):
            if costs.check(conn, "zerobounce", costs.ZEROBOUNCE_CHECK_USD) == "block":
                break
            try:
                r = requests.get(
                    "https://api.zerobounce.net/v2/validate",
                    params={"api_key": config.ZEROBOUNCE_API_KEY, "email": email},
                    timeout=20,
                )
                r.raise_for_status()
                costs.record(conn, "zerobounce", "validate",
                             costs.ZEROBOUNCE_CHECK_USD)
                status = r.json().get("status")
                if status == "valid":
                    return True
                if status in ("invalid", "spamtrap", "abuse", "do_not_mail",
                              "catch-all"):
                    return False
                # 'unknown' → transient/ambiguous: retry once
            except requests.RequestException:
                log.warning("zerobounce attempt %s failed for %s", attempt, email)
            time.sleep(2)
        return False
    # MX fallback
    if not scraped_from_their_site:
        return False
    try:
        import dns.resolver
        domain = email.rsplit("@", 1)[-1]
        for attempt in (1, 2):
            try:
                answers = dns.resolver.resolve(domain, "MX", lifetime=10)
                return len(answers) > 0
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                return False
            except Exception:
                time.sleep(2)
        return False
    except ImportError:
        log.warning("dnspython missing; accepting scraped email without MX check")
        return True


def find_email(conn, place: dict, website: str) -> tuple[str | None, str]:
    """Returns (email or None, email_status)."""
    candidates = scrape_emails(website) if website else []
    for email in candidates[:5]:
        if verify_email(conn, email, scraped_from_their_site=True):
            return email, "found"
    # pattern guessing only helps when a verifier exists and a real domain is known
    if config.ZEROBOUNCE_API_KEY and website:
        domain = urlparse(website).netloc.lower().removeprefix("www.")
        if domain and not any(s in domain for s in SOCIAL_HOSTS + FREE_SITE_HOSTS):
            for local in ("info", "contact", "hello", "office"):
                guess = f"{local}@{domain}"
                if verify_email(conn, guess, scraped_from_their_site=False):
                    return guess, "found"
    return None, "not_found"


# ---------------------------------------------------------------- assemble

def tz_from_address(address: str) -> str:
    m = re.search(r",\s*([A-Z]{2})\s+\d{5}", address or "")
    return STATE_TZ.get(m.group(1), "America/Chicago") if m else "America/Chicago"


def top_up(location: str | None = None, niche: str | None = None,
           target_count: int | None = None) -> dict:
    """The find_leads job: fill the funnel to target_count QUALIFIED leads."""
    location = location or config.CAMPAIGN_LOCATION
    niche = niche or config.CAMPAIGN_NICHE
    target_count = target_count or config.CAMPAIGN_TARGET_COUNT
    if not (location and niche):
        raise SystemExit("CAMPAIGN_LOCATION / CAMPAIGN_NICHE not set")

    stats = {"searched": 0, "inserted": 0, "qualified": 0, "skipped": 0,
             "deduped": 0, "phone_only": 0, "dropped": 0}
    with db.connect() as conn:
        have = len(db.get_leads_by_status(conn, "QUALIFIED"))
        need = target_count - have
        if need <= 0:
            log.info("funnel already at target (%s QUALIFIED)", have)
            return stats

        areas = config.CAMPAIGN_AREAS or [location]
        seen_ids, places = set(), []
        for area in areas:
            for p in search_area(conn, niche, area):
                pid = p.get("id")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    places.append(p)
        stats["searched"] = len(places)
        log.info("find: %d distinct places across %d area(s) for '%s'",
                 len(places), len(areas), niche)
        for place in places:
            if stats["qualified"] >= need:
                break
            name = (place.get("displayName") or {}).get("text", "").strip()
            address = (place.get("formattedAddress") or "").strip()
            if not name:
                continue

            # legacy API: phone/website/reviews/photos only exist on Details
            details = place_details(conn, place["id"]) if place.get("id") else {}
            phone = (details.get("formatted_phone_number") or "").strip()
            website = (details.get("website") or "").strip()
            place["websiteUri"] = website
            if db.is_duplicate(conn, phone, None):
                stats["deduped"] += 1
                continue

            q_status, q_reason = qualify(conn, place)
            if q_status == "SKIP":
                stats["skipped"] += 1
                # store skips too, so re-runs don't re-fetch their site
                db.add_lead(conn, business_name=name, category=place.get("primaryType"),
                            phone=phone or None, address=address,
                            existing_website=website or None,
                            rating=place.get("rating"),
                            review_count=place.get("userRatingCount"),
                            qualify_status="SKIP", qualify_reason=q_reason,
                            status="SKIP", email_status="not_found",
                            timezone=tz_from_address(address))
                conn.commit()
                continue

            email, email_status = find_email(conn, place, website)
            if email and db.is_duplicate(conn, None, email):
                stats["deduped"] += 1
                continue

            reviews = [
                {"text": rv.get("text", ""), "rating": rv.get("rating")}
                for rv in details.get("reviews", [])[:5]
                if rv.get("text")
            ]
            # keep dimensions so build_sample can rank hero candidates
            # (landscape ≥1200 wide first) and reject logo-shaped images
            photos = [{"ref": p["photo_reference"], "w": p.get("width", 0),
                       "h": p.get("height", 0)}
                      for p in details.get("photos", [])[:10]
                      if p.get("photo_reference")
                      and max(p.get("width", 0), p.get("height", 0)) >= 800]

            profile = {
                "place_id": place.get("id"),
                "hours": (details.get("opening_hours") or {}).get(
                    "weekday_text", []),
                "summary": (details.get("editorial_summary") or {}).get(
                    "overview", ""),
                "reviews": reviews,
                "photos": photos,
            }

            status = "FOUND"
            if email_status == "not_found" and not address:
                if phone:
                    status = "PHONE_ONLY"
                    stats["phone_only"] += 1
                else:
                    stats["dropped"] += 1
                    log.info("dropped %s: no email, no address, no phone", name)
                    continue

            lead_id = db.add_lead(
                conn, business_name=name, category=place.get("primaryType"),
                phone=phone or None, address=address,
                timezone=tz_from_address(address),
                existing_website=website or None, rating=place.get("rating"),
                review_count=place.get("userRatingCount"),
                email=email, email_status=email_status,
                email_verified=1 if email_status == "found" else 0,
                qualify_status=q_status, qualify_reason=q_reason,
                source_profile=profile, status=status,
            )
            if lead_id is None:
                stats["deduped"] += 1
                continue
            stats["inserted"] += 1
            if status == "FOUND":
                db.transition(conn, lead_id, "QUALIFIED",
                              f"{q_status}: {q_reason}")
                stats["qualified"] += 1
            conn.commit()
            time.sleep(0.5)  # be polite to scraped sites

    log.info("find_leads done: %s", stats)
    return stats


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    loc = sys.argv[1] if len(sys.argv) > 1 else None
    nic = sys.argv[2] if len(sys.argv) > 2 else None
    cnt = int(sys.argv[3]) if len(sys.argv) > 3 else None
    print(top_up(loc, nic, cnt))
