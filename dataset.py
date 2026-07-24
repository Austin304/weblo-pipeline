"""Step-0 — durable, append-only training-data capture for the sample pipeline.

PURE INSTRUMENTATION. Nothing here changes which candidate is generated, selected,
or shipped; build_sample calls this AFTER a build has already decided and shipped.
Every capture entry point is wrapped so a failure LOGS and returns None — capture
must never break a build (STEP0-HANDOFF §0).

Why this exists: logs/bon/<lead>_<i>.html is ephemeral — the NEXT build of the same
lead overwrites it (the 278 data-loss in the handoff proved it), and the design
language + the human's pick were never persisted anywhere durable. This module copies
every candidate's assets into a PERMANENT per-build dir and appends one JSON record per
build to builds.jsonl. The single most valuable field is the human pick (the gold
label), recorded separately from the AI's auto-pick so their disagreement (lead 261,
where the human OVERRODE the judge) is never collapsed.

Layout (config.DATASET_DIR):
    builds.jsonl                         one JSON object per build (append-only)
    builds/<build_id>/cand_<i>.html      raw candidate HTML (i = gate-passer index,
    builds/<build_id>/cand_<i>_fold.jpg    same numbering as logs/bon + compare_candidates)
    builds/<build_id>/cand_<i>_full.jpg
    builds/<build_id>/cand_<i>.txt       visible copy (the copy/voice axis)

CLI:
    python dataset.py record-pick <build_id> <idx> [--note "CHANGE: … | KEEP: …"]
    python dataset.py backfill        # the 4 known historical dental picks
    python dataset.py export [--out FILE]   # join builds.jsonl to leads.db grades by lead_id
"""
import hashlib
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import config

log = logging.getLogger(__name__)

BUILDS_JSONL = config.DATASET_DIR / "builds.jsonl"
BUILDS_DIR = config.DATASET_DIR / "builds"

# Mirrors the ORDER of build_sample.DESIGN_LANGUAGES (kept as a local copy so backfill
# — a standalone historical op — needs no import of build_sample, avoiding a cycle).
DESIGN_LANGUAGE_NAMES = ("Dark Luxe", "Warm Editorial", "Airy Minimal", "Clinical Modern")


def _now_ts() -> str:
    """Filesystem-safe, sortable UTC stamp (also the build_id suffix)."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _dl_name(dl) -> str:
    """A design language may arrive as the full dict (live capture) or a name string
    (backfill). Persist the NAME explicitly either way — today it lives only in the log."""
    return dl["name"] if isinstance(dl, dict) else str(dl)


# --- static motion extraction ------------------------------------------------
# Screenshots can't see motion, so parse it out of the inline <style>/<script> (these
# samples are single-file, all CSS/JS inline). Heuristic by design — good enough to
# make "does this candidate move?" a trainable feature.

def extract_motion(html: str) -> dict:
    styles = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.I | re.S))
    scripts = " ".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.I | re.S))
    # hover-raise: a :hover rule that also moves/lifts the element
    hover_raise = bool(re.search(
        r":hover[^{}]*\{[^{}]*(?:transform|translate|box-shadow)", styles, re.I))
    # scroll-reveal: an IntersectionObserver, AOS, or reveal/fade-in classes tied to scroll
    scroll_reveal = bool(re.search(r"IntersectionObserver", scripts, re.I)) or \
        bool(re.search(r"data-aos|\b(?:reveal|fade-in|fade-up|scroll-reveal)\b", html, re.I))
    # count of transition / transition-<prop> declarations
    transitions = len(re.findall(r"transition(?:-[a-z]+)?\s*:", styles, re.I))
    keyframes = len(re.findall(r"@keyframes", styles, re.I))
    # elements that actually animate: an `animation:` rule or a reveal/aos class
    animated = len(re.findall(r"animation(?:-[a-z]+)?\s*:", styles, re.I)) + \
        len(re.findall(r'class=["\'][^"\']*(?:reveal|fade-in|fade-up|aos-)', html, re.I))
    return {
        "hover_raise": hover_raise,
        "scroll_reveal": scroll_reveal,
        "transitions_count": transitions,
        "keyframes_count": keyframes,
        "animated_els_est": animated,
        "has_meaningful_motion": hover_raise or scroll_reveal or keyframes > 0,
    }


def _visible_text(html: str) -> str:
    """The page's visible copy (drop script/style), whitespace-collapsed — the
    copy/voice axis Austin grades on."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    except Exception:
        # fall back to a crude strip so text is never simply missing
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _hero_srcs(html: str) -> list[str]:
    """Hero <img> srcs (image-provenance axis). Prefer <img> inside a hero-ish block;
    fall back to the first <img> and any hero background-image. Deduped, order kept."""
    srcs: list[str] = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        hero_blocks = soup.find_all(
            True, class_=re.compile("hero|banner|masthead", re.I))
        for block in hero_blocks:
            for img in block.find_all("img"):
                if img.get("src"):
                    srcs.append(img["src"])
            # hero photos are often CSS backgrounds, not <img>
            for el in [block, *block.find_all(True)]:
                m = re.search(r"url\((['\"]?)([^)'\"]+)\1\)", el.get("style", "") or "")
                if m:
                    srcs.append(m.group(2))
        if not srcs:
            first = soup.find("img")
            if first and first.get("src"):
                srcs.append(first["src"])
    except Exception:
        srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)', html, re.I)[:2]
    return list(dict.fromkeys(srcs))  # dedupe, preserve order


# --- append-only record store ------------------------------------------------

def _read_records() -> list[dict]:
    if not BUILDS_JSONL.exists():
        return []
    out = []
    for line in BUILDS_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _append_record(record: dict) -> None:
    """Append one build record. The build-creation path is strictly append-only —
    we NEVER overwrite a prior build (that's the logs/bon bug we're replacing)."""
    BUILDS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with BUILDS_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _rewrite_records(records: list[dict]) -> None:
    """Atomic full rewrite — ONLY for the record-pick labelling op (a targeted mutation
    of an existing build's human-pick fields, not the build-creation path)."""
    BUILDS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    tmp = BUILDS_JSONL.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, BUILDS_JSONL)  # atomic on same volume (Windows-safe)


def split_change_keep(note: str | None) -> tuple[str | None, str | None]:
    """Split the reviewer's "CHANGE: … | KEEP: …" convention into two fields (the
    natural human-label schema; handoff §1). Tolerates 'CHANGE FIRST:' and a missing
    half. Reused by both record-pick (pick notes) and export (grade notes)."""
    if not note:
        return None, None
    parts = re.split(r"\bKEEP\s*:", note, maxsplit=1, flags=re.I)
    change = re.sub(r"^\s*(?:\|\s*)?CHANGE(?:\s+FIRST)?\s*:\s*", "", parts[0], flags=re.I)
    change = change.strip(" |\t\r\n")
    keep = parts[1].strip(" |\t\r\n") if len(parts) > 1 else ""
    return (change or None), (keep or None)


# --- capture -----------------------------------------------------------------

def capture_build(conn, lead, cap_cands: list[dict], shots, pairwise, *,
                  auto_pick_idx: int, shipped_idx: int,
                  preview: bool = False, backfilled: bool = False,
                  human_pick_idx=None, shipped_slug=None, shipped_url=None,
                  build_id: str | None = None) -> str | None:
    """Persist ONE build: copy every candidate's assets into a permanent per-build dir
    and append a record to builds.jsonl. Non-fatal — any failure is logged and swallowed.

    cap_cands: every generated candidate, in generation order, each
        {html, dl, arch, gate_pass, gate_reason, image_source}. Gate-passers are indexed
        0..k-1 (matching logs/bon + compare_candidates + shots keys); the rare gate-failer
        gets an index after the passers (never a pick target).
    shots: {passer_idx: {"fold": bytes, "full": bytes}} reused from _tournament (not
        re-rendered), or None/{} when there was no tournament.
    Returns the build_id, or None on skip/failure."""
    if preview:   # preview_dir builds touch no DB/samples — and write no dataset record
        return None
    try:
        return _capture_build(
            conn, lead, cap_cands, shots or {}, pairwise or [],
            auto_pick_idx=auto_pick_idx, shipped_idx=shipped_idx, backfilled=backfilled,
            human_pick_idx=human_pick_idx, shipped_slug=shipped_slug,
            shipped_url=shipped_url, build_id=build_id)
    except Exception:
        log.exception("lead %s: Step-0 capture failed (non-fatal, build unaffected)",
                      lead["id"] if lead else "?")
        return None


def _capture_build(conn, lead, cap_cands, shots, pairwise, *, auto_pick_idx,
                   shipped_idx, backfilled, human_pick_idx, shipped_slug,
                   shipped_url, build_id) -> str:
    lead_id = lead["id"]
    build_id = build_id or f"{lead_id}_{_now_ts()}"
    build_dir = BUILDS_DIR / build_id
    build_dir.mkdir(parents=True, exist_ok=True)
    rel = f"builds/{build_id}"  # paths stored relative to DATASET_DIR

    # gate-passers keep 0..k-1 (the pick/shots/logs-bon index space); failers trail after.
    # A cand may pin an explicit "idx" (backfill, where a skipped file leaves a gap and
    # the index must stay equal to the logs/bon file number); otherwise number in order.
    n_pass = sum(1 for c in cap_cands if c.get("gate_pass"))
    p = f = 0
    numbered = []
    for c in cap_cands:
        if c.get("idx") is not None:
            idx, sh = int(c["idx"]), shots.get(int(c["idx"]))
        elif c.get("gate_pass"):
            idx, sh = p, shots.get(p); p += 1
        else:
            idx, sh = n_pass + f, None; f += 1
        numbered.append((idx, c, sh))

    candidates = []
    for idx, c, sh in sorted(numbered, key=lambda t: t[0]):
        html = c["html"] or ""
        (build_dir / f"cand_{idx}.html").write_text(html, encoding="utf-8")
        (build_dir / f"cand_{idx}.txt").write_text(_visible_text(html), encoding="utf-8")
        fold_path = full_path = None
        if sh:
            if sh.get("fold"):
                (build_dir / f"cand_{idx}_fold.jpg").write_bytes(sh["fold"])
                fold_path = f"{rel}/cand_{idx}_fold.jpg"
            if sh.get("full"):
                (build_dir / f"cand_{idx}_full.jpg").write_bytes(sh["full"])
                full_path = f"{rel}/cand_{idx}_full.jpg"
        candidates.append({
            "idx": idx,
            "design_language": _dl_name(c["dl"]),
            "archetype": c.get("arch"),
            "gate_pass": bool(c.get("gate_pass")),
            "gate_reason": c.get("gate_reason"),
            "html_path": f"{rel}/cand_{idx}.html",
            "fold_path": fold_path,
            "full_path": full_path,
            "text_path": f"{rel}/cand_{idx}.txt",
            "hero_image_srcs": _hero_srcs(html),
            "image_source": c.get("image_source"),
            "motion": extract_motion(html),
        })

    # shipped slug/url: read back what _ship_sample just wrote (unless supplied, e.g. backfill)
    if shipped_slug is None or shipped_url is None:
        row = db_get_lead(conn, lead_id)
        if row is not None:
            shipped_slug = shipped_slug if shipped_slug is not None else row["sample_slug"]
            shipped_url = shipped_url if shipped_url is not None else row["sample_url"]

    record = {
        "build_id": build_id,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lead_id": lead_id,
        "business_name": lead["business_name"],
        "category": lead["category"],
        "preview": False,
        "candidates": candidates,
        "pairwise": pairwise,          # AI judge, free annotations
        "auto_pick_idx": auto_pick_idx,  # _tournament winner
        "human_pick_idx": human_pick_idx,   # GOLD label; null until record-pick
        "human_note_change": None,       # split from "CHANGE: … | KEEP: …"; null until recorded
        "human_note_keep": None,
        "shipped_idx": shipped_idx,
        "shipped_slug": shipped_slug,
        "shipped_url": shipped_url,
    }
    if backfilled:
        record["backfilled"] = True
    _append_record(record)
    log.info("lead %s: Step-0 captured build %s (%d candidate(s) -> %s)",
             lead_id, build_id, len(candidates), build_dir)
    return build_id


def capture_single_shot(conn, lead, html: str, design_language, archetype,
                        image_source: str, fact_flagged: bool = False) -> str | None:
    """Degenerate 1-candidate record for the single-shot fallback path (build_one
    non-best-of-N: Playwright missing, BEST_OF_N=1, or too few candidates cleared the
    gate). No tournament, so the sole candidate is trivially the auto- and shipped pick."""
    cand = {"html": html, "dl": design_language, "arch": archetype,
            "gate_pass": True, "gate_reason": ("fact_flagged" if fact_flagged else None),
            "image_source": image_source}
    return capture_build(conn, lead, [cand], None, [],
                         auto_pick_idx=0, shipped_idx=0)


def db_get_lead(conn, lead_id):
    """Small indirection so capture never hard-depends on importing db at module load."""
    return conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()


# --- human pick (the gold label) ---------------------------------------------

def record_pick(build_id: str, idx: int, note: str | None = None) -> bool:
    """Set the human pick on an existing build's record: human_pick_idx (+ split note
    into human_note_change/human_note_keep) and shipped_idx (the human ships their pick).
    In-place mutation of that one record — the labelling op, not the append path."""
    records = _read_records()
    hit = None
    for r in records:
        if r.get("build_id") == build_id:
            hit = r
            break
    if hit is None:
        print(f"record-pick: no build {build_id!r} in {BUILDS_JSONL}")
        return False
    change, keep = split_change_keep(note)
    hit["human_pick_idx"] = int(idx)
    hit["shipped_idx"] = int(idx)
    hit["human_note_change"] = change
    hit["human_note_keep"] = keep
    _rewrite_records(records)
    auto = hit.get("auto_pick_idx")
    flag = "  (OVERRIDE of AI auto-pick)" if auto is not None and auto != int(idx) else ""
    print(f"record-pick: build {build_id} human_pick_idx={idx}{flag}")
    return True


def latest_build_id(lead_id: int) -> str | None:
    """Most recent build_id for a lead (builds.jsonl is append-time-ordered)."""
    ids = [r["build_id"] for r in _read_records() if r.get("lead_id") == int(lead_id)]
    return ids[-1] if ids else None


def record_pick_for_lead(lead_id: int, idx: int, note: str | None = None) -> bool:
    """Convenience for compare_candidates: log the pick against the lead's LATEST build."""
    bid = latest_build_id(lead_id)
    if bid is None:
        print(f"record-pick: no dataset build for lead {lead_id} "
              f"(build one first, or use `python dataset.py record-pick`)")
        return False
    return record_pick(bid, idx, note)


# --- backfill the 4 known historical dental picks (handoff §1) ----------------
# The picks lived only in chat/memory; the DB stored the shipped slug but never which
# candidate index / design language the human chose. Recover them into first-class
# records (backfilled=true). Design language is derived from the SAME start-hash formula
# build_sample uses (verified: it labels all four picks correctly). No screenshots — the
# HTML is preserved, so shots can be regenerated later.

_BACKFILL = {
    246: {"human": 2, "auto": None},                    # #3 Warm Editorial (246_2.html)
    261: {"human": 0, "auto": 2},                        # #1 Dark Luxe; AI auto-picked Airy Minimal (idx2) — human OVERRIDE
    271: {"human": 1, "auto": None},                     # #2 Clinical Modern (271_1.html); only 3 passers
    278: {"human": 0, "auto": None,                      # #1 Warm Editorial; 278_0 OVERWRITTEN -> recover from shipped slug
          "recover": {0: config.SAMPLES_CACHE_DIR / "dallas-dental-arts-dallas-99d89a2c13" / "index.html"},
          "skip": (1,)},                                 # 278_1 also overwritten (a later best-of-2 preview) — not the original candidate
}


def _formula_dl(lead_id: int, file_idx: int) -> str:
    start = int(hashlib.sha1(str(lead_id).encode()).hexdigest(), 16) % len(DESIGN_LANGUAGE_NAMES)
    return DESIGN_LANGUAGE_NAMES[(start + file_idx) % len(DESIGN_LANGUAGE_NAMES)]


def backfill() -> None:
    import db
    bon = config.LOGS_DIR / "bon"
    with db.connect() as conn:
        for lead_id, spec in _BACKFILL.items():
            build_id = f"{lead_id}_backfill"
            if any(r.get("build_id") == build_id for r in _read_records()):
                print(f"backfill: lead {lead_id} already present ({build_id}); skipping")
                continue
            lead = db.get_lead(conn, lead_id)
            if lead is None:
                print(f"backfill: lead {lead_id} not in DB; skipping")
                continue
            recover = spec.get("recover", {})
            skip = set(spec.get("skip", ()))
            # candidate file indices = the bon files present, plus any recovered index, minus skips
            idxs = {int(p.stem.split("_")[1]) for p in bon.glob(f"{lead_id}_*.html")}
            idxs |= set(recover)
            idxs -= skip
            cap_cands = []
            for i in sorted(idxs):
                src = recover.get(i) or (bon / f"{lead_id}_{i}.html")
                if not Path(src).is_file():
                    print(f"backfill: lead {lead_id} cand {i}: {src} missing; skipping cand")
                    continue
                html = Path(src).read_text(encoding="utf-8", errors="replace")
                # pin idx = the real logs/bon file number so a skipped file (278) leaves a
                # gap instead of shifting later candidates' design-language mapping.
                cap_cands.append({"html": html, "idx": i, "dl": _formula_dl(lead_id, i),
                                  "arch": None, "gate_pass": True,
                                  "gate_reason": ("recovered from shipped slug (logs/bon overwritten)"
                                                  if i in recover else None),
                                  "image_source": None})
            if not cap_cands:
                print(f"backfill: lead {lead_id}: no candidate HTML found; skipping")
                continue
            capture_build(conn, lead, cap_cands, None, [],
                          auto_pick_idx=spec["auto"], shipped_idx=spec["human"],
                          human_pick_idx=spec["human"], backfilled=True,
                          shipped_slug=lead["sample_slug"], shipped_url=lead["sample_url"],
                          build_id=build_id)
            print(f"backfill: lead {lead_id} -> {build_id} "
                  f"(human_pick_idx={spec['human']}, auto_pick_idx={spec['auto']})")


# --- export: join builds to graded factors by lead_id (handoff §1 join gotcha) ----
# Join BY lead_id, never by slug: a regenerated lead's grade row points at an EARLIER
# v1 slug, so grade.sample_slug != the current shipped slug. Attach the latest grade per
# lead plus its split CHANGE/KEEP notes to every build of that lead.

_GRADE_FACTORS = ("hero", "design", "layout", "imagery", "copy", "trust", "beats")


def export(out_path: str | None = None) -> Path:
    import db
    out = Path(out_path) if out_path else (config.DATASET_DIR / "export.jsonl")
    grades_by_lead: dict[int, dict] = {}
    with db.connect() as conn:
        # latest grade per lead (MAX(id)) — matches db.grade_averages' "latest per lead"
        for g in conn.execute(
                "SELECT g.* FROM grades g JOIN (SELECT lead_id, MAX(id) mid FROM grades "
                "GROUP BY lead_id) last ON g.id = last.mid"):
            change, keep = split_change_keep(g["notes"])
            grades_by_lead[g["lead_id"]] = {
                "factors": {f: g[f] for f in _GRADE_FACTORS},
                "overall": g["overall"],
                "grade_slug": g["sample_slug"],
                "grade_change": change,
                "grade_keep": keep,
            }
    records = _read_records()
    n_graded = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in records:
            grade = grades_by_lead.get(r.get("lead_id"))
            if grade:
                r = {**r, "grade": grade}
                n_graded += 1
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"export: {len(records)} build(s), {n_graded} with a grade -> {out}")
    return out


# --- CLI ---------------------------------------------------------------------

def main(argv=None):
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO)
    if not argv:
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "record-pick":
        note = None
        if "--note" in rest:
            i = rest.index("--note")
            note = rest[i + 1] if i + 1 < len(rest) else None
            rest = rest[:i] + rest[i + 2:]
        if len(rest) < 2:
            print('usage: python dataset.py record-pick <build_id> <idx> [--note "CHANGE: … | KEEP: …"]')
            return
        record_pick(rest[0], int(rest[1]), note)
    elif cmd == "backfill":
        backfill()
    elif cmd == "export":
        out = None
        if "--out" in rest:
            i = rest.index("--out")
            out = rest[i + 1] if i + 1 < len(rest) else None
        export(out)
    else:
        print(f"unknown command {cmd!r}")
        print(__doc__)


if __name__ == "__main__":
    main()
