"""Learn the operator's aesthetic taste from their site ratings, and feed it back into
the pipeline's generation + selection so the samples drift toward what they'd pick.

WHY THIS EXISTS. `rate_sites.py` collects a stream of YES/NO judgments on REAL websites
(dataset/ratings.jsonl + a fold/full screenshot per site). On its own that data just sits
there — nothing reads it. This module is the missing feedback step: it has the vision model
STUDY the operator's YES sites vs their NO sites (the real screenshots — layout, type, color,
imagery, COPY, motion — not just the motion fingerprint) and distill a short, concrete
"taste profile." `build_sample` then injects that profile into (1) the generation prompt so
every candidate is designed toward the operator's taste, and (2) the pairwise selection judge
so the auto-pick matches what the operator would choose.

It is NOT a trained model — it's in-context learning: the taste lives as text the vision LLM
wrote by comparing examples, regenerated whenever the ratings grow. That's the fast path to
"it learns from what I'm doing" with the data already on disk; a trained scorer can come later.

Layout (config.DATASET_DIR):
    ratings.jsonl            the operator's YES/NO stream (written by rate_sites.py)
    ratings/<id>/fold.jpg    per-site fold screenshot (the taste-dense first impression)
    taste_profile.md         <- THIS module's output; what build_sample injects

CLI:
    python taste.py distill              # (re)learn the profile from ratings.jsonl
    python taste.py distill --max 20     # more examples per side (bigger/pricier vision call)
    python taste.py show                 # print the current profile
"""
import argparse
import base64
import json
import logging
import random
import sys
from pathlib import Path

import config

log = logging.getLogger("taste")

RATINGS_JSONL = config.DATASET_DIR / "ratings.jsonl"
RATINGS_DIR = config.DATASET_DIR / "ratings"
PROFILE_PATH = config.DATASET_DIR / "taste_profile.md"

# how many examples per side to send the vision model by default. Balanced YES/NO so the
# contrast is clean; capped because every example is an inlined screenshot (cost + context).
MAX_PER_SIDE = 16
MIN_PER_SIDE = 3        # below this the contrast is too thin to distill anything trustworthy

_DISTILL_SYSTEM = (
    "You are a senior design director. An operator has hand-rated real local-business "
    "websites YES (would keep / is good) or NO (reject) purely on gut aesthetic and copy "
    "quality. Reverse-engineer their taste into a precise, ACTIONABLE profile that another "
    "designer — and an AI page generator — can follow to produce pages this operator rates "
    "YES. Judge design and copy, not the specific business.")

_DISTILL_TASK = (
    "Study the YES group vs the NO group above as GROUPS, then write the operator's taste "
    "profile. Use these sections; under each, 2-5 CONCRETE, visual bullets phrased as a "
    "contrast where you can (\"YES tend to __; NO tend to __\"). No generic filler like "
    "\"good design\" — be specific enough to act on.\n\n"
    "## Layout & composition\n## Typography\n## Color & contrast\n## Imagery & hero\n"
    "## Copy & tone\n## Motion & interaction\n## The 3-second YES test\n"
    "(the 3 things that most reliably flip a page from NO to YES)\n\n"
    "Keep the whole profile under ~400 words. Output GitHub-flavored markdown only, "
    "starting at the first '## ' heading.")


def _load_labeled() -> tuple[list[dict], list[dict]]:
    """Split ratings.jsonl into (good, bad) records that still have a fold screenshot."""
    good, bad = [], []
    if not RATINGS_JSONL.exists():
        return good, bad
    for line in RATINGS_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        fold = RATINGS_DIR / (r.get("id") or "") / "fold.jpg"
        if not fold.exists():
            continue
        r["_fold"] = fold
        if r.get("label") == "up":
            good.append(r)
        elif r.get("label") == "down":
            bad.append(r)
    return good, bad


def _img_block(path: Path) -> dict | None:
    try:
        data = base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return None
    return {"type": "image", "source": {"type": "base64",
            "media_type": "image/jpeg", "data": data}}


def _sample(records: list[dict], n: int) -> list[dict]:
    """Deterministic representative subset (seeded, so re-runs on the same data match)."""
    if len(records) <= n:
        return records
    return random.Random(0).sample(records, n)


def _group_content(records: list[dict], verdict: str) -> list[dict]:
    """Interleave a label line + fold image for each example in a group."""
    out = [{"type": "text", "text": f"=== {verdict} GROUP ({len(records)} sites) ==="}]
    for i, r in enumerate(records, 1):
        img = _img_block(r["_fold"])
        if not img:
            continue
        biz = r.get("business_name") or r.get("url") or "site"
        cat = r.get("category") or "local business"
        out.append({"type": "text", "text": f"{verdict} #{i} — {biz} ({cat})"})
        out.append(img)
    return out


def distill(max_per_side: int = MAX_PER_SIDE) -> str | None:
    """Have the vision model compare YES vs NO screenshots and write the taste profile
    to PROFILE_PATH. Returns the profile text, or None if it couldn't run."""
    import llm  # lazy: pulls the provider SDK only when actually distilling
    if not llm.available():
        print("No LLM key configured (config.LLM_API_KEY); cannot distill. Set it and retry.")
        return None
    good, bad = _load_labeled()
    if len(good) < MIN_PER_SIDE or len(bad) < MIN_PER_SIDE:
        print(f"Not enough labeled examples with screenshots to distill "
              f"(have {len(good)} YES / {len(bad)} NO; need >= {MIN_PER_SIDE} of each). "
              f"Rate a few more in rate_sites.py first.")
        return None
    g = _sample(good, max_per_side)
    b = _sample(bad, max_per_side)
    print(f"Distilling taste from {len(g)} YES + {len(b)} NO fold screenshots "
          f"(of {len(good)}/{len(bad)} total) via {config.MODEL_VISION} ...")

    content: list[dict] = [{"type": "text", "text":
        f"Below are {len(g)} sites the operator marked YES (good) and {len(b)} marked NO "
        f"(reject). They are mostly dentists and adjacent local-service businesses."}]
    content += _group_content(g, "YES")
    content += _group_content(b, "NO")
    content.append({"type": "text", "text": _DISTILL_TASK})

    resp = llm.create(model=config.MODEL_VISION, max_tokens=1200, system=_DISTILL_SYSTEM,
                      messages=[{"role": "user", "content": content}])
    profile = (resp.content[0].text or "").strip()
    if not profile:
        print("Vision model returned an empty profile; not written. Try again or raise --max.")
        return None

    # honest accounting: log the spend to the ledger like every other model call
    try:
        import costs
        import db
        with db.connect() as conn:
            costs.record(conn, "claude", "taste_distill",
                         costs.claude_cost(config.MODEL_VISION, resp.usage.input_tokens,
                                           resp.usage.output_tokens),
                         tokens_in=resp.usage.input_tokens, tokens_out=resp.usage.output_tokens)
            conn.commit()
    except Exception:
        log.warning("taste_distill: cost not recorded (non-fatal)", exc_info=True)

    header = (f"<!-- taste_profile.md — learned from {len(good)} YES / {len(bad)} NO ratings "
              f"({len(g)}+{len(b)} sampled). Regenerate: python taste.py distill -->\n"
              f"# Operator taste profile\n\n")
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(header + profile + "\n", encoding="utf-8")
    print(f"Wrote {PROFILE_PATH}  ({len(profile)} chars).")
    return profile


def load_profile() -> str | None:
    """The current taste profile text, or None if it hasn't been distilled yet.
    build_sample calls this; a None result means 'behave exactly as before'."""
    try:
        text = PROFILE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def as_generation_block(profile: str) -> str:
    """Frame the profile for the GENERATION system prompt (design toward it)."""
    return ("HOUSE TASTE — the operator has hand-rated real websites YES/NO, and this profile "
            "was distilled from those judgments. Treat it as a strong design directive: build "
            "THIS page so the operator would rate it YES.\n\n" + profile)


def as_judge_block(profile: str) -> str:
    """Frame the profile for the SELECTION judge (prefer the sample that matches it)."""
    return ("The operator has a specific, learned taste, distilled below from their own YES/NO "
            "ratings of real sites. Weigh it HEAVILY: when the two samples differ, prefer the "
            "one that better matches this taste.\n\n" + profile)


def _section(profile: str, heading_prefix: str) -> str:
    """Pull one '## <heading>' block out of the markdown profile (the lines under the first
    heading that starts with heading_prefix, up to the next '## '). '' if not found."""
    out, capture = [], False
    for ln in profile.splitlines():
        if ln.lstrip().startswith("## "):
            capture = ln.lstrip()[3:].strip().lower().startswith(heading_prefix.lower())
            continue
        if capture:
            out.append(ln)
    return "\n".join(out).strip()


def as_imagery_block(profile: str) -> str | None:
    """Just the imagery slice of the profile, framed for the photo-ranking vision call so the
    HERO score reflects the operator's taste (real faces / warm people-scenes over empty rooms,
    reception desks, and exteriors) — WITHOUT discarding the non-face shots, which stay usable
    as fallback so the page still has a photo when no face exists. None if no imagery section."""
    img = _section(profile, "Imagery")
    if not img:
        return None
    return ("The OPERATOR'S learned HERO taste. Use it to set the 'hero' PRIORITY only: score a "
            "candidate HIGH as a hero when it fits the YES pattern and LOW when it fits the NO "
            "pattern, so a real smiling face / warm people-scene is preferred as the hero over a "
            "clean-but-empty room, reception desk, or exterior. IMPORTANT: this changes hero "
            "RANKING, not usability — do NOT set 'use': false on a decent, on-service non-face "
            "image (a tidy interior, a clean detail) just because it isn't a face; keep it usable "
            "as a fallback/supporting image so the page still has a photo when no face exists. "
            "Reserve 'use': false for genuinely bad or wrong-service images per the rules above.\n"
            + img)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Learn/apply the operator's website taste.")
    sub = ap.add_subparsers(dest="cmd")
    d = sub.add_parser("distill", help="(re)learn the taste profile from ratings.jsonl")
    d.add_argument("--max", type=int, default=MAX_PER_SIDE,
                   help=f"examples per side sent to the vision model (default {MAX_PER_SIDE})")
    sub.add_parser("show", help="print the current taste profile")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for stream in (sys.stdout, sys.stderr):   # Windows console is cp1252; keep it from crashing
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    if args.cmd == "distill":
        distill(max_per_side=args.max)
    elif args.cmd == "show":
        p = load_profile()
        print(p if p else "No taste profile yet. Run: python taste.py distill")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
