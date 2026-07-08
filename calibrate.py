"""Calibration toolkit (sample-design-system.md Principle 6, Phase A).

Three jobs, all invoked via run.py:

  vision   — render a built sample with Playwright, screenshot it (desktop
             fold, mobile fold, full page) and have Opus VISION grade it
             against the A-G rubric. Laptop-only (the VM can't run Chromium).
             Prints a suggested `run.py grade` command — the human grade is
             still the source of truth; this is the assist.
  insights — per-factor grade averages sliced by niche / image source /
             archetype, plus every CHANGE/KEEP note. Turns the first-30
             hand grades into concrete prompt-tuning decisions.
  exemplar — distill a top-graded sample into a reusable style crib
             (exemplars/<niche>.md) that build_sample injects into the
             generator's system prompt for that niche.

Playwright is NOT in requirements.txt on purpose (VM stays lean). On the
laptop:  pip install playwright && playwright install chromium
"""
import base64
import json
import logging
import re

import config
import costs
import db

log = logging.getLogger(__name__)

VISION_EST_USD = 0.15   # pre-call worst case; typical ~$0.05 (3 images + short JSON)
EXEMPLAR_EST_USD = 0.25

FACTOR_LABELS = ("hero", "design", "layout", "imagery", "copy", "trust", "beats")

VISION_RUBRIC = """A hero    — first 3 seconds: does it stop you and make you scroll? (generic/off-putting=1, forgettable=3, striking=5)
B design  — premium vs basic craft: "a designer made this" vs 2010 template
C layout  — section variety and rhythm vs a monotonous centered stack
D imagery — photos elevate the page (well-placed, good crops) vs sit there / broken / gradient default
E copy    — headlines and text sound written for THIS business vs robotic filler
F trust   — through the owner's eyes: nothing embarrassing, no overclaims
G beats   — night-and-day better than a typical dated small-business site"""


# ---------------------------------------------------------------- vision

def _screenshots(target: str) -> list[tuple[str, bytes]]:
    """Render and capture: desktop fold, mobile fold, full desktop page
    (height-capped so the API doesn't downscale it into mush)."""
    from playwright.sync_api import sync_playwright  # laptop-only dependency
    shots = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for label, w, h, full in (("desktop fold", 1280, 900, False),
                                      ("mobile fold", 390, 844, False),
                                      ("desktop full page", 1280, 900, True)):
                page = browser.new_page(viewport={"width": w, "height": h})
                page.goto(target, wait_until="networkidle", timeout=45_000)
                page.wait_for_timeout(1_200)  # let fonts/images settle
                kwargs = {"type": "jpeg", "quality": 60}
                if full:
                    height = min(page.evaluate("document.body.scrollHeight"), 4_500)
                    kwargs["clip"] = {"x": 0, "y": 0, "width": w, "height": height}
                else:
                    kwargs["full_page"] = False
                shots.append((label, page.screenshot(**kwargs)))
                page.close()
        finally:
            browser.close()
    return shots


def vision_grade(conn, target: str, context: str, lead=None) -> dict | None:
    """Screenshot `target` (URL or file:// path) and have Opus grade it.
    Returns the parsed grade dict, or None on failure/budget block."""
    if costs.check(conn, "claude", VISION_EST_USD) == "block":
        log.warning("claude budget blocked; vision grade skipped")
        return None
    try:
        shots = _screenshots(target)
    except ImportError:
        print("playwright not installed — this command runs on the laptop:\n"
              "  pip install playwright && playwright install chromium")
        return None
    except Exception as e:
        print(f"could not render {target}: {e}")
        return None

    content = []
    for label, img in shots:
        content.append({"type": "text", "text": f"[{label}]"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.b64encode(img).decode()}})
    content.append({"type": "text", "text": f"""These are screenshots of an
AI-generated SAMPLE website made to win a local business as a client. Grade it
through the business owner's eyes — "would I be impressed seeing my business
rendered this way?" Be harsh; 3 means forgettable.

BUSINESS CONTEXT: {context or 'unknown'}

Score each factor 1-5:
{VISION_RUBRIC}

Also give an overall gut score out of 10 (kept separate on purpose), the ONE
change you'd make first, and what's worth keeping.

Return STRICT JSON only:
{{"scores": {{"hero": n, "design": n, "layout": n, "imagery": n, "copy": n, "trust": n, "beats": n}},
 "overall": n, "change_first": "...", "keep": "...", "fixes": ["specific fix", ...]}}"""})

    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=config.MODEL_QUALITY, max_tokens=800,
        system="You are a brutally honest design reviewer grading sample "
               "websites for a cold-outreach pipeline.",
        messages=[{"role": "user", "content": content}],
    )
    costs.record(conn, "claude", "vision_grade",
                 costs.claude_cost(config.MODEL_QUALITY,
                                   resp.usage.input_tokens,
                                   resp.usage.output_tokens),
                 lead_id=lead["id"] if lead else None,
                 tokens_in=resp.usage.input_tokens,
                 tokens_out=resp.usage.output_tokens)
    conn.commit()
    try:
        return json.loads(re.search(r"\{.*\}", resp.content[0].text, re.S).group(0))
    except (AttributeError, json.JSONDecodeError):
        print("vision grade returned unparseable JSON:\n" + resp.content[0].text[:500])
        return None


def print_vision_grade(grade: dict, lead=None):
    scores = grade.get("scores") or {}
    digits = "".join(str(int(scores.get(f, 0))) for f in FACTOR_LABELS)
    print("  " + "  ".join(f"{f}={scores.get(f, '?')}" for f in FACTOR_LABELS)
          + f"  | overall={grade.get('overall', '?')}/10")
    print(f"  CHANGE FIRST: {grade.get('change_first', '')}")
    print(f"  KEEP:         {grade.get('keep', '')}")
    for fix in grade.get("fixes") or []:
        print(f"  - {fix}")
    if lead is not None and len(digits) == 7 and "0" not in digits:
        note = (f"CHANGE: {grade.get('change_first', '')} | "
                f"KEEP: {grade.get('keep', '')}").replace('"', "'")
        print("\n  suggested (EDIT to your own judgment, then run on the VM):\n"
              f'  sudo -u weblo .venv/bin/python run.py grade {lead["id"]} '
              f'{digits} {grade.get("overall", 5)} "{note}"')


def vision_command(args: list[str]):
    """run.py vision <lead_id ...> | vision --url <url> [context words...]"""
    with db.connect() as conn:
        if args and args[0] == "--url":
            if len(args) < 2:
                print("usage: run.py vision --url <url> [context]")
                return
            grade = vision_grade(conn, args[1], " ".join(args[2:]))
            if grade:
                print(f"\n{args[1]}")
                print_vision_grade(grade)
            return
        ids = [int(a) for a in args if a.isdigit()]
        if not ids:
            print("usage: run.py vision <lead_id ...>  |  vision --url <url> [context]")
            return
        for lid in ids:
            lead = db.get_lead(conn, lid)
            if lead is None or not lead["sample_slug"]:
                print(f"lead {lid}: not found or no sample built")
                continue
            local = config.SAMPLES_CACHE_DIR / lead["sample_slug"] / "index.html"
            target = local.as_uri() if local.is_file() else lead["sample_url"]
            context = (f"{lead['business_name']} ({lead['category']}); "
                       f"their current site's weakness: "
                       f"{lead['qualify_reason'] or 'no web presence'}")
            print(f"\nlead {lid} — {lead['business_name']} — {target}")
            grade = vision_grade(conn, target, context, lead)
            if grade:
                print_vision_grade(grade, lead)


# ---------------------------------------------------------------- insights

def _latest_grades(conn):
    return conn.execute(
        "SELECT g.*, l.category, l.image_source, l.qualify_status, l.business_name"
        " FROM grades g"
        " JOIN (SELECT lead_id, MAX(id) AS mid FROM grades GROUP BY lead_id) last"
        "   ON g.id = last.mid"
        " JOIN leads l ON l.id = g.lead_id").fetchall()


def print_insights():
    """Slice the hand grades so calibration decisions are obvious: which
    niches, image sources and archetypes are winning, and every human note."""
    from build_sample import ARCHETYPES, niche_key
    rows = _latest_grades(conn := db.connect())
    try:
        if not rows:
            print("no grades recorded yet — grade samples first (run.py grade ...)")
            return
        keys = (*FACTOR_LABELS, "overall")

        def bucket(fn, title):
            groups: dict[str, list] = {}
            for r in rows:
                groups.setdefault(fn(r), []).append(r)
            print(f"\n== by {title} ==")
            for name, grp in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                avgs = "  ".join(
                    f"{k}={sum(g[k] for g in grp if g[k] is not None) / max(1, sum(1 for g in grp if g[k] is not None)):.1f}"
                    for k in keys)
                print(f"  {name:22s} n={len(grp):<3d} {avgs}")

        print(f"{len(rows)} graded sample(s)")
        bucket(lambda r: niche_key(r["category"]), "niche")
        bucket(lambda r: r["image_source"] or "unknown", "image source")
        bucket(lambda r: ARCHETYPES[r["lead_id"] % len(ARCHETYPES)].split(" — ")[0],
               "archetype")
        bucket(lambda r: r["qualify_status"] or "unknown", "qualify status")
        print("\n== notes (CHANGE FIRST / KEEP — feed recurring ones into "
              "exemplars/LESSONS.md) ==")
        for r in sorted(rows, key=lambda r: r["overall"] or 0):
            if r["notes"]:
                print(f"  [{r['overall']}/10] lead {r['lead_id']} "
                      f"({r['business_name']}): {r['notes']}")
    finally:
        conn.close()


# ---------------------------------------------------------------- exemplar

def save_exemplar(lead_id: int):
    """Distill a top-graded sample into exemplars/<niche>.md — a compact
    style crib build_sample injects (cached) into every future generation
    for that niche. Run this on the machine that has the sample HTML."""
    from build_sample import niche_key
    with db.connect() as conn:
        lead = db.get_lead(conn, lead_id)
        if lead is None or not lead["sample_slug"]:
            print(f"lead {lead_id}: not found or no sample built")
            return
        path = config.SAMPLES_CACHE_DIR / lead["sample_slug"] / "index.html"
        if not path.is_file():
            print(f"sample HTML not found at {path} — run where the sample lives")
            return
        if costs.check(conn, "claude", EXEMPLAR_EST_USD) == "block":
            print("claude budget blocked")
            return
        html = path.read_text(encoding="utf-8")
        import anthropic
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=config.MODEL_QUALITY, max_tokens=900,
            system="You distill what makes a specific web design excellent into "
                   "a reusable brief for generating OTHER sites at the same level.",
            messages=[{"role": "user", "content": f"""This sample site was hand-graded
as excellent. Distill WHY into a style crib (<=350 words) another designer could
follow for a DIFFERENT business in the same industry. Describe techniques and
level, never this business's facts: hero composition and how the headline/CTA/
image interact; the type scale and font pairing logic; the spacing system;
how color is deployed (roles, not these exact hexes); section rhythm and
variety; CTA treatment; the small details that make it feel designed rather
than templated. Plain prose/bullets, no preamble.

HTML:
{html[:60000]}"""}],
        )
        costs.record(conn, "claude", "exemplar_distill",
                     costs.claude_cost(config.MODEL_QUALITY,
                                       resp.usage.input_tokens,
                                       resp.usage.output_tokens),
                     lead_id=lead_id, tokens_in=resp.usage.input_tokens,
                     tokens_out=resp.usage.output_tokens)
        conn.commit()
        key = niche_key(lead["category"])
        config.EXEMPLARS_DIR.mkdir(exist_ok=True)
        out = config.EXEMPLARS_DIR / f"{key}.md"
        out.write_text(
            f"<!-- distilled from lead {lead_id} ({lead['business_name']}), "
            f"build {lead['sample_slug']}, {db.now()} -->\n"
            + resp.content[0].text.strip() + "\n", encoding="utf-8")
        print(f"wrote {out} — injected into every future '{key}' generation")
