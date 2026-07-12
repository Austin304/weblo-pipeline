"""Calibration toolkit (sample-design-system.md Principle 6, Phase A).

Four jobs, all invoked via run.py:

  review   — THE fast grading loop: batch vision-grade several samples and
             write ONE local review page (logs/review.html) — screenshots
             beside 1-5 factor buttons PREFILLED with the AI grade. The human
             adjusts only where they disagree ("grade by exception"), then a
             single button copies every `run.py grade` command for the VM.
             Each command carries --vision (what the AI predicted), so the DB
             accumulates human-vs-AI pairs and `insights` can report when the
             AI grader is calibrated enough to stop hand-grading everything.
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
                # reduced_motion=reduce: our pages use scroll-reveal (.reveal starts
                # at opacity:0, IntersectionObserver fades it in on scroll). A headless
                # full-page screenshot never scrolls, so without this the grader captures
                # every reveal section still invisible ("empty colored blocks") and grades
                # blanks. The prompt mandates a prefers-reduced-motion override
                # (.reveal{opacity:1}), so reducing motion shows the real, settled page.
                page = browser.new_page(viewport={"width": w, "height": h},
                                        reduced_motion="reduce")
                page.goto(target, wait_until="networkidle", timeout=45_000)
                page.wait_for_timeout(1_200)  # let fonts/images settle
                kwargs = {"type": "jpeg", "quality": 60}
                if full:
                    # clip WITHOUT full_page silently clamps to the viewport —
                    # the grader would only ever see the fold
                    height = min(page.evaluate("document.body.scrollHeight"), 4_500)
                    kwargs["clip"] = {"x": 0, "y": 0, "width": w, "height": height}
                    kwargs["full_page"] = True
                else:
                    kwargs["full_page"] = False
                shots.append((label, page.screenshot(**kwargs)))
                page.close()
        finally:
            browser.close()
    return shots


def vision_grade(conn, target: str, context: str, lead=None,
                 shots: list[tuple[str, bytes]] | None = None) -> dict | None:
    """Screenshot `target` (URL or file:// path) and have Opus grade it.
    Pass pre-captured `shots` to skip the render (review page reuses them).
    Returns the parsed grade dict, or None on failure/budget block."""
    if costs.check(conn, "claude", VISION_EST_USD) == "block":
        log.warning("claude budget blocked; vision grade skipped")
        return None
    if shots is None:
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
        overall = grade.get("overall", 5)
        print("\n  suggested (EDIT to your own judgment, then run on the VM):\n"
              f'  sudo -u weblo .venv/bin/python run.py grade {lead["id"]} '
              f'{digits} {overall} "{note}" --vision {digits}:{overall}')


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


# ---------------------------------------------------------------- review

CMD_PREFIX = "sudo -u weblo .venv/bin/python run.py"

# Plain string on purpose (no f-string): CSS/JS braces stay literal.
_REVIEW_HEAD = """<!doctype html><html><head><meta charset="utf-8">
<title>sample review</title><style>
  body{margin:0;font:14px/1.45 system-ui,sans-serif;background:#111;color:#ddd}
  .card{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:16px;
        padding:20px;border-bottom:4px solid #000;background:#181818}
  .card.done{background:#14201a}
  .card.skipped .shots,.card.skipped .panel{opacity:.25;pointer-events:none}
  .card.skipped header label.skip-l{pointer-events:auto}
  header{grid-column:1/-1;display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
  header h2{margin:0;font-size:17px;color:#fff}
  header a{color:#7ab8ff} .muted{color:#888;font-size:12px}
  .shots{display:flex;flex-direction:column;gap:10px;min-width:0}
  .shots img{max-width:100%;border:1px solid #333;border-radius:4px;background:#fff}
  .foldrow{display:flex;gap:10px;align-items:flex-start}
  .foldrow .desk{flex:1;min-width:0}
  .foldrow .mob{width:26%}
  .full{max-height:520px;overflow-y:auto;border:1px solid #333;border-radius:4px}
  .full img{border:0;display:block;width:100%}
  .panel{position:sticky;top:12px;align-self:start}
  .factor,.overall{display:flex;align-items:center;gap:6px;margin:5px 0}
  .factor span.lbl,.overall span.lbl{width:78px;font-size:12px;color:#aaa;cursor:help}
  button.seg{width:30px;height:26px;border:1px solid #444;background:#222;color:#bbb;
             border-radius:4px;cursor:pointer;font-size:13px}
  .overall button.seg{width:26px}
  button.seg.sel{background:#2563eb;border-color:#2563eb;color:#fff;font-weight:700}
  .factor button.seg.sel[data-v="1"],.factor button.seg.sel[data-v="2"]{background:#b91c1c;border-color:#b91c1c}
  .factor button.seg.sel[data-v="5"]{background:#15803d;border-color:#15803d}
  input[type=text]{width:100%;box-sizing:border-box;margin:4px 0;padding:6px 8px;
      background:#222;border:1px solid #444;border-radius:4px;color:#ddd;font-size:13px}
  .fixes{font-size:12px;color:#8a8a8a;margin-top:8px}
  .fixes li{margin:2px 0}
  .bar{position:fixed;bottom:0;left:0;right:0;display:flex;gap:14px;align-items:center;
       padding:10px 20px;background:#000;border-top:1px solid #333;z-index:9}
  .bar button{padding:9px 18px;font-size:14px;border:0;border-radius:6px;
       background:#2563eb;color:#fff;cursor:pointer}
  .bar button:disabled{background:#333;color:#777;cursor:default}
  .spacer{height:70px}
  .vtag{font-size:12px;color:#c9a227}
</style></head><body>
"""

_REVIEW_FOOT = """<div class="spacer"></div>
<div class="bar"><button id="copy" disabled>copy grade commands</button>
<span id="count" class="muted"></span>
<span class="muted">buttons are prefilled with the AI grade — change only where you
disagree; ALWAYS sanity-check hero + overall yourself. Paste the copied block into
the VM SSH.</span></div>
<script>
const PREFIX = %PREFIX%;
document.addEventListener('click', ev => {
  const b = ev.target.closest('button.seg');
  if (!b) return;
  for (const s of b.parentElement.querySelectorAll('button.seg')) s.classList.remove('sel');
  b.classList.add('sel');
  refresh();
});
document.addEventListener('input', refresh);
document.addEventListener('change', refresh);
function cmdFor(card) {
  const digits = [...card.querySelectorAll('.factor')].map(f => {
    const s = f.querySelector('button.seg.sel');
    return s ? s.dataset.v : null;
  });
  const ov = card.querySelector('.overall button.seg.sel');
  if (digits.some(d => !d) || !ov) return null;
  const note = ('CHANGE: ' + card.querySelector('.change').value.trim() +
                ' | KEEP: ' + card.querySelector('.keep').value.trim())
               .replace(/"/g, "'");
  let cmd = PREFIX + ' grade ' + card.dataset.id + ' ' + digits.join('') +
            ' ' + ov.dataset.v + ' "' + note + '"';
  if (card.dataset.vision) cmd += ' --vision ' + card.dataset.vision;
  const lines = [cmd];
  if (card.querySelector('.star').checked)
    lines.push(PREFIX + ' exemplar ' + card.dataset.id);
  return lines.join('\\n');
}
function refresh() {
  let done = 0, total = 0; const out = [];
  for (const card of document.querySelectorAll('.card')) {
    const skip = card.querySelector('.skip').checked;
    card.classList.toggle('skipped', skip);
    if (skip) continue;
    total++;
    const c = cmdFor(card);
    card.classList.toggle('done', !!c);
    if (c) { done++; out.push(c); }
  }
  document.getElementById('count').textContent = done + '/' + total + ' graded';
  window._cmds = out.join('\\n');
  document.getElementById('copy').disabled = !out.length;
}
document.getElementById('copy').addEventListener('click', () => {
  const done = () => {
    const b = document.getElementById('copy');
    b.textContent = 'copied \\u2713 — paste into the VM SSH';
    setTimeout(() => b.textContent = 'copy grade commands', 3000);
  };
  if (navigator.clipboard && navigator.clipboard.writeText)
    navigator.clipboard.writeText(window._cmds).then(done, () => fallback(done));
  else fallback(done);
  function fallback(cb) {
    const t = document.createElement('textarea');
    t.value = window._cmds; document.body.appendChild(t);
    t.select(); document.execCommand('copy'); t.remove(); cb();
  }
});
refresh();
</script></body></html>
"""


def _card_html(lead, target: str, shots, grade) -> str:
    """One sample as a review card: screenshots left, prefilled controls right."""
    import html as H
    imgs = {label: base64.b64encode(img).decode() for label, img in shots}
    scores = (grade or {}).get("scores") or {}
    digits = "".join(str(int(scores.get(f, 0))) for f in FACTOR_LABELS)
    overall = (grade or {}).get("overall")
    vision_attr = (f' data-vision="{digits}:{overall}"'
                   if len(digits) == 7 and "0" not in digits and overall else "")
    rubric_hint = {ln.split("—")[0].split()[1]: ln.strip()
                   for ln in VISION_RUBRIC.splitlines()}

    def seg_row(cls, lbl, hint, lo, hi, sel):
        btns = "".join(
            f'<button class="seg{" sel" if v == sel else ""}" data-v="{v}">{v}</button>'
            for v in range(lo, hi + 1))
        return (f'<div class="{cls}"><span class="lbl" title="{H.escape(hint)}">'
                f'{H.escape(lbl)}</span>{btns}</div>')

    rows = "".join(
        seg_row("factor", f"{chr(65 + i)} {f}", rubric_hint.get(f, f), 1, 5,
                scores.get(f) if isinstance(scores.get(f), int) else None)
        for i, f in enumerate(FACTOR_LABELS))
    rows += seg_row("overall", "overall /10", "gut score, kept separate on purpose",
                    1, 10, overall if isinstance(overall, int) else None)
    fixes = "".join(f"<li>{H.escape(str(fx))}</li>"
                    for fx in (grade or {}).get("fixes") or [])
    live = lead["sample_url"] or target
    return f"""<section class="card" data-id="{lead['id']}"{vision_attr}>
<header><h2>#{lead['id']} {H.escape(lead['business_name'] or '')}</h2>
<span class="muted">{H.escape(lead['category'] or '')}</span>
<a href="{H.escape(live)}" target="_blank">open live &nearr;</a>
{f'<span class="vtag">AI: {overall}/10</span>' if overall else '<span class="vtag">no AI grade</span>'}
<label class="muted skip-l"><input type="checkbox" class="skip"> skip</label>
<label class="muted"><input type="checkbox" class="star"> &#11088; exemplar this</label>
</header>
<div class="shots">
  <div class="foldrow">
    <img class="desk" src="data:image/jpeg;base64,{imgs['desktop fold']}" alt="desktop fold">
    <img class="mob" src="data:image/jpeg;base64,{imgs['mobile fold']}" alt="mobile fold">
  </div>
  <div class="full"><img src="data:image/jpeg;base64,{imgs['desktop full page']}" alt="full page"></div>
</div>
<div class="panel">{rows}
<input type="text" class="change" placeholder="CHANGE FIRST — the one thing to fix"
 value="{H.escape((grade or {}).get('change_first') or '')}">
<input type="text" class="keep" placeholder="KEEP — worth stealing even if the grade is low"
 value="{H.escape((grade or {}).get('keep') or '')}">
{f'<ul class="fixes">{fixes}</ul>' if fixes else ''}
</div></section>
"""


def _write_review_html(cards: list[str]):
    config.LOGS_DIR.mkdir(exist_ok=True)
    out = config.LOGS_DIR / "review.html"
    foot = _REVIEW_FOOT.replace("%PREFIX%", json.dumps(CMD_PREFIX))
    out.write_text(_REVIEW_HEAD + "".join(cards) + foot, encoding="utf-8")
    return out


def review_command(args: list[str]):
    """run.py review <lead_id ...> — the fast grading loop (see module doc).

    Two arg forms, mixable:
      review 18 22            lead ids resolved from the LOCAL leads.db
      review 121=https://...  id=url pairs — for when the leads live in the
                              VM DB and the laptop DB doesn't have them
                              (the grade command only needs the id + the URL)
    """
    jobs = []  # (lead-ish mapping, target)
    with db.connect() as conn:
        for a in args:
            if a.isdigit():
                lead = db.get_lead(conn, int(a))
                if lead is None or not lead["sample_slug"]:
                    print(f"lead {a}: not in the LOCAL db or no sample built — "
                          f"skipped (use {a}=<sample_url> for VM leads)")
                    continue
                local = config.SAMPLES_CACHE_DIR / lead["sample_slug"] / "index.html"
                jobs.append((lead, local.as_uri() if local.is_file()
                             else lead["sample_url"]))
            elif "=" in a and a.split("=", 1)[0].isdigit():
                lid, url = a.split("=", 1)
                jobs.append(({"id": int(lid), "business_name": f"lead {lid}",
                              "category": "", "sample_url": url,
                              "qualify_reason": None}, url))
            else:
                print(f"unrecognized arg {a!r} — want <id> or <id>=<url>")
    if not jobs:
        print("usage: run.py review <lead_id | id=url> ...   (laptop; needs playwright)")
        return
    cards = []
    with db.connect() as conn:
        for lead, target in jobs:
            print(f"lead {lead['id']} — {lead['business_name']} — rendering + AI grade ...")
            try:
                shots = _screenshots(target)
            except ImportError:
                print("playwright not installed — this command runs on the laptop:\n"
                      "  pip install playwright && playwright install chromium")
                return
            except Exception as e:
                print(f"  could not render {target}: {e} — skipped")
                continue
            context = (f"{lead['business_name']} ({lead['category']}); "
                       f"their current site's weakness: "
                       f"{lead['qualify_reason'] or 'no web presence'}")
            grade = vision_grade(conn, target, context, lead, shots=shots)
            if grade:
                print_vision_grade(grade)
            cards.append(_card_html(lead, target, shots, grade))
    if not cards:
        print("nothing to review")
        return
    out = _write_review_html(cards)
    print(f"\nreview page: {out}")
    print("adjust only where you disagree, then 'copy grade commands' -> paste on the VM")
    import webbrowser
    webbrowser.open(out.as_uri())


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
        bucket(lambda r: niche_key(r["category"], r["business_name"]), "niche")
        bucket(lambda r: r["image_source"] or "unknown", "image source")
        bucket(lambda r: ARCHETYPES[r["lead_id"] % len(ARCHETYPES)].split(" — ")[0],
               "archetype")
        bucket(lambda r: r["qualify_status"] or "unknown", "qualify status")
        # -- vision-grader calibration: is the AI close enough to stop
        #    hand-grading everything? delta = human - AI (positive = AI harsher)
        pairs = []
        for r in rows:
            v = r["vision"] if "vision" in r.keys() else None
            if not v or ":" not in v:
                continue
            digits, _, voverall = v.partition(":")
            if len(digits) == 7 and digits.isdigit() and voverall.lstrip("-").isdigit():
                pairs.append((r, digits, int(voverall)))
        if pairs:
            print(f"\n== vision-grader calibration ({len(pairs)} sample(s) "
                  "with an AI pre-grade; delta = human - AI) ==")
            within = total = 0
            for i, f in enumerate(FACTOR_LABELS):
                deltas = [(p[0][f] or 0) - int(p[1][i]) for p in pairs]
                mad = sum(abs(d) for d in deltas) / len(deltas)
                w = sum(1 for d in deltas if abs(d) <= 1)
                within += w
                total += len(deltas)
                bias = sum(deltas) / len(deltas)
                print(f"  {f:8s} avg|d|={mad:.2f}  within +/-1: {w}/{len(deltas)}"
                      f"  bias={bias:+.2f}")
            ov = [(p[0]["overall"] or 0) - p[2] for p in pairs]
            print(f"  {'overall':8s} avg|d|={sum(abs(d) for d in ov)/len(ov):.2f}"
                  f"  bias={sum(ov)/len(ov):+.2f}  (/10 scale, informational)")
            pct = within / total if total else 0
            if len(pairs) >= 10 and pct >= 0.9:
                print(f"  -> CALIBRATED: {pct:.0%} of factor scores within +/-1 "
                      f"over {len(pairs)} samples.\n"
                      "     The AI grader sees what you see — switch to "
                      "spot-checking ~1 in 5 samples\n"
                      "     and let `run.py review` prefills stand for the rest.")
            else:
                print(f"  -> not yet calibrated: {pct:.0%} within +/-1 "
                      f"(want >=90% across >=10 samples). Keep hand-grading.")

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
        key = niche_key(lead["category"], lead["business_name"])
        config.EXEMPLARS_DIR.mkdir(exist_ok=True)
        out = config.EXEMPLARS_DIR / f"{key}.md"
        out.write_text(
            f"<!-- distilled from lead {lead_id} ({lead['business_name']}), "
            f"build {lead['sample_slug']}, {db.now()} -->\n"
            + resp.content[0].text.strip() + "\n", encoding="utf-8")
        print(f"wrote {out} — injected into every future '{key}' generation")
