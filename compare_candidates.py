"""Human round-robin viewer: render each best-of-N candidate for a lead to a full-page
screenshot and build a LOCAL side-by-side comparison page for hand-picking/grading.

Usage:
    python compare_candidates.py <lead_id> [<lead_id> ...] [--out DIR] [--open]
    python compare_candidates.py <lead_id> --pick <idx> [--note "CHANGE: … | KEEP: …"]

Reads the raw candidate HTML the build saved to logs/bon/<lead_id>_<i>.html (index i =
position among gate-passers, in generation order). Writes compare_<lead_id>.html with the
N screenshots side-by-side, each with an "open live" link to the real interactive file.

`--pick <idx>` records the human pick (the GOLD label) against that lead's latest Step-0
build via dataset.record_pick — so hand-picking here auto-logs it durably. `<idx>` is the
0-based candidate index shown as "idx N" on each card (the "#N" label minus 1).

LOCAL FILE ONLY — do NOT publish these as web Artifacts: the samples imitate the real
practice's site (real name/reviews) and must not get a shareable URL.

Requires Playwright + Chromium (pip install playwright && playwright install chromium).
"""
import base64
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BON = Path("logs/bon")


def shoot(browser, html_path: Path) -> str:
    """Full-page JPEG (base64) of one candidate; reduced-motion so scroll-reveal
    sections are settled, not captured mid-fade as blank blocks."""
    page = browser.new_page(viewport={"width": 1280, "height": 900},
                            reduced_motion="reduce")
    try:
        page.goto(html_path.as_uri(), wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(1200)
        h = min(page.evaluate("document.body.scrollHeight"), 6000)
        img = page.screenshot(type="jpeg", quality=70, full_page=True,
                              clip={"x": 0, "y": 0, "width": 1280, "height": h})
        return base64.b64encode(img).decode()
    finally:
        page.close()


def build_page(browser, lead: str, out_dir: Path) -> Path | None:
    files = sorted(BON.glob(f"{lead}_*.html"),
                   key=lambda p: int(p.stem.split("_")[1]))
    if not files:
        print(f"  lead {lead}: no candidate files in {BON}")
        return None
    cards = []
    for f in files:
        i = int(f.stem.split("_")[1])
        print(f"  shooting {f.name}")
        b64 = shoot(browser, f.resolve())
        cards.append(f'''<div class="card">
  <div class="cap"><b>#{i + 1}</b> <span class="idx">idx {i}</span>
    <a href="file:///{f.resolve().as_posix()}" target="_blank">open live &#8599;</a></div>
  <img src="data:image/jpeg;base64,{b64}" alt="candidate {i + 1}">
</div>''')
    html = f'''<!doctype html><html><head><meta charset="utf-8">
<title>Lead {lead} - candidate compare</title>
<style>
 body{{margin:0;background:#14161a;color:#e7e9ee;font:15px/1.4 system-ui,sans-serif}}
 header{{padding:16px 22px;border-bottom:1px solid #2a2e37;position:sticky;top:0;background:#14161a;z-index:2}}
 h1{{margin:0;font-size:18px}} .sub{{color:#9aa1ad;font-size:13px;margin-top:4px}}
 .grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px;padding:18px}}
 @media(max-width:1100px){{.grid{{grid-template-columns:1fr}}}}
 .card{{background:#1c1f26;border:1px solid #2a2e37;border-radius:10px;overflow:hidden}}
 .cap{{display:flex;align-items:center;gap:12px;padding:10px 14px;border-bottom:1px solid #2a2e37;font-size:14px}}
 .cap .idx{{color:#9aa1ad;font-size:12px}}
 .cap a{{margin-left:auto;color:#7fb0ff;text-decoration:none;font-size:13px}}
 img{{display:block;width:100%;height:auto}}
</style></head><body>
<header><h1>Lead {lead} &mdash; {len(files)} candidates, pick one</h1>
<div class="sub">Full-page screenshots at 1280px desktop width. Grade factors: hero &middot; design &middot; layout &middot; imagery &middot; copy &middot; trust &middot; beats-their-site.</div></header>
<div class="grid">{''.join(cards)}</div>
</body></html>'''
    out = out_dir / f"compare_{lead}.html"
    out.write_text(html, encoding="utf-8")
    print(f"  WROTE {out.resolve()}")
    return out


def main():
    args = [a for a in sys.argv[1:]]
    do_open = "--open" in args
    args = [a for a in args if a != "--open"]
    out_dir = Path(".")
    if "--out" in args:
        i = args.index("--out")
        out_dir = Path(args[i + 1]); args = args[:i] + args[i + 2:]
    # --pick <idx> [--note "..."]: auto-log the human pick against the lead's latest
    # Step-0 build (the gold label that used to vanish; see dataset.record_pick).
    pick = note = None
    if "--note" in args:
        i = args.index("--note")
        note = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    if "--pick" in args:
        i = args.index("--pick")
        pick = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    out_dir.mkdir(parents=True, exist_ok=True)
    leads = args or []
    if not leads:
        print("usage: python compare_candidates.py <lead_id> [<lead_id> ...] [--out DIR] [--open]")
        print("       python compare_candidates.py <lead_id> --pick <idx> [--note \"CHANGE: … | KEEP: …\"]")
        return
    if pick is not None:
        if len(leads) != 1:
            print("--pick needs exactly one <lead_id>"); return
        import dataset
        dataset.record_pick_for_lead(int(leads[0]), int(pick), note)
        return  # picking is a logging op; don't also re-render/overwrite the compare page
    written = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for lead in leads:
                p = build_page(browser, lead, out_dir)
                if p:
                    written.append(p)
        finally:
            browser.close()
    if do_open:
        for p in written:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Start-Process '{p.resolve()}'"], check=False)


if __name__ == "__main__":
    main()
