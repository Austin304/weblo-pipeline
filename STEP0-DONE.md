# STEP0-DONE.md

Reply handoff from the **Step-0 training-data-capture** tab back to the
**preview-build / their-own-words** tab. Mirror of how `STEP0-HANDOFF.md` reached me.
Written 2026-07-24. Step-0 is built and verified (incl. one real LLM build — see §5).

---

## ⚠️ 0. TWO THINGS YOU MUST ACT ON (read first)

**1. Two function signatures in `build_sample.py` CHANGED — update any direct caller you hold.**
- `_pairwise_pick(conn, lead, shots_a, shots_b)` now returns **`(winner, reason)`** (was just `winner`).
- `_tournament(conn, lead, htmls)` now returns **`(best, shots, pairwise)`** (was just `best`).
  `shots` is the rendered JPEG bytes keyed by candidate index (Step-0 reuses them instead of
  re-rendering); `pairwise` is the per-pair judge annotations.
  The only in-repo callers are inside `build_sample.py` itself (already updated). I grepped
  the whole repo: **no other module calls them** — `calibrate.py` uses its own separate
  `_screenshots`/`vision_grade` path (single-page absolute grading) and imports only
  `ARCHETYPES`/`niche_key` from build_sample. If your uncommitted work calls either directly,
  it will break until you unpack the new tuple.

**2. Everything is UNCOMMITTED on `best-of-n`, interleaved with your `preview_dir` work.**
Coordinate commits so neither tab clobbers the other. My changed/created files:
`dataset.py` (new), `STEP0-DONE.md` (new), `build_sample.py`, `compare_candidates.py`,
`config.py`, `.gitignore`. Your `preview_dir` threading in `build_sample.py` is preserved
untouched — see §1.

---

## 1. Collision resolution (your §0/§4)

- I edited the same functions you flagged (`_try_best_of_n`, `build_one`, `_ship_sample`)
  but **preserved your `preview_dir` param exactly** and edited alongside it, never rewrote it.
- **Step-0 SKIPS all capture when `preview_dir` is set** — a preview build writes no dataset
  record, no per-build dir, nothing (verified: preview build → `builds.jsonl` delta 0, DB left
  `QUALIFIED`). So capture honors your "preview touches nothing" contract.
- I did **not** touch `logs/bon` behavior at all (left the overwrite bug alone — Step-0
  replaces reliance on it, per your §3). The 278 data-loss is now moot for the gold labels
  (backfilled — §4).

## 2. Spec calls I made where the handoff was ambiguous (all confirmed OK by the requester)

- **Append-only vs. record-pick.** Build *creation* is strictly append-only. `record-pick`
  does an in-place **atomic rewrite** of that one build's pick fields (not an event-stream
  append). `human_pick_idx` + `human_note_change`/`human_note_keep` + `shipped_idx` are set
  on the existing record.
- **`shipped_idx` under a fact-fix.** When the winner gets a post-selection fact-fix
  regeneration, the shipped bytes differ from `cand_<best>.html`. `shipped_idx = best` (which
  candidate was *selected* for taste-labeling); the exact shipped HTML lives in `samples_cache`.
  Don't assume `cand_<shipped_idx>.html` is byte-identical to the live sample.
- **Pick auto-logging is CLI-only.** `compare_candidates.py` is a static HTML file with no
  interactivity, so clicking a card can't log a pick. Added `--pick <idx> [--note …]` which
  logs against the lead's **latest** build via `dataset.record_pick_for_lead`; cards now show
  the 0-based `idx N`. In-page click-to-pick would need a real local server.
- **`dataset/` is gitignored** (like `leads.db` / `samples_cache/` / `logs/`) — machine-local
  data plane. The backfilled gold labels **do not travel in git**; they live on this machine
  next to `leads.db`.

## 3. What Step-0 captures (quick map)

`config.DATASET_DIR` = `dataset/`:
- `builds.jsonl` — one append-only record per build. Keys: `build_id` (`{lead_id}_{UTCstamp}`),
  `ts`, `lead_id`, `business_name`, `category`, `preview:false`, `candidates[]`, `pairwise[]`,
  `auto_pick_idx`, `human_pick_idx`, `human_note_change`, `human_note_keep`, `shipped_idx/slug/url`.
- `candidates[i]`: `idx` (gate-passer position = logs/bon index), `design_language`
  (**now persisted explicitly**), `archetype`, `gate_pass`, paths to `cand_<i>.{html,txt,_fold.jpg,_full.jpg}`,
  `hero_image_srcs`, `image_source`, and a `motion{}` block (static parse of the inline
  CSS/JS: hover_raise, scroll_reveal, transitions_count, keyframes_count, animated_els_est,
  has_meaningful_motion).
- **`auto_pick_idx` (tournament) and `human_pick_idx` (gold) are separate fields** — their
  disagreement (lead 261) is the point; never collapsed.
- CLI: `record-pick <build_id> <idx> [--note "CHANGE: … | KEEP: …"]`, `backfill`, `export`.
- `export` joins `builds.jsonl` → `leads.db grades` **BY lead_id, never slug** (regenerated
  leads' `grade.sample_slug` ≠ current shipped slug).

## 4. Backfill of the 4 historical picks — fidelity notes

All four are recorded with `backfilled:true`. Every claim in your §1 verified against the live
DB + filesystem before recording.
- `246 → Warm Editorial (idx 2)`; `261 → Dark Luxe (idx 0)` with `auto_pick_idx=2` (Airy
  Minimal) marking the **human override**; `271 → Clinical Modern (idx 1)` (only 3 passers);
  `278 → Warm Editorial (idx 0)`.
- **271/278 sidecar (non-picked) candidates are best-effort.** A gate failure makes
  passer-index ≠ generation-index, so the start-hash formula can't perfectly label the
  *non-picked* drafts of 271. The **picks** are all correct; the `backfilled:true` flag marks
  the reduced fidelity.
- **278 is missing `idx 1`.** Your best-of-2 preview overwrote both `278_0` and `278_1`. I
  recovered `278_0` (the pick, Warm Editorial) from the shipped samples_cache slug, and
  **omitted `278_1`** rather than fabricate an "original" from the later generation. So 278's
  record has candidates `idx 0, 2, 3`.

## 5. Verification — real LLM build (gap closed)

The whole layer exists to catch "silently captures nothing," so a stub isn't enough. I fired
**one real, non-preview best-of-N build** (BEST_OF_N=4) through the **live Kimi k3 generator**
— real brand extraction, real vision photo-ranking, 4 real generations, real Playwright render,
real pairwise judge, real ship, real capture. Spend: **$0.76**. Result, all confirmed:

- Real-generation variance showed up honestly: cands **Airy Minimal** + **Clinical Modern**
  FAILED the structural gate (photo reused); **Dark Luxe** + **Warm Editorial** passed. Capture
  handled it exactly as designed — **passers got idx 0,1 (with fold/full jpgs from the
  tournament) and the two gate-failers were still captured at idx 2,3 (`gate_pass:false`, no
  jpgs) as hard-negatives.** cand_<i>.html + cand_<i>.txt written for all four.
- **Motion parser's first run on REAL output** (not a stub): every candidate came back
  `hover_raise:true, scroll_reveal:true, transitions_count:9–12, has_meaningful_motion:true`
  — it genuinely detects motion, not all-false/zeros. (keyframes:0 is correct — these designs
  use transitions + IntersectionObserver, not @keyframes.)
- `pairwise` captured the real judge annotation verbatim: *"B's headline sits across the
  patient's face; A's hero is clean, premium, unobstructed."* `auto_pick_idx=0` (Dark Luxe),
  `human_pick_idx=null` (auto build, no human pick).
- **Shipped identically:** DB `slug/url` == record `shipped_slug/url`, real `QUALIFIED →
  SAMPLE_BUILT` transition, `samples_cache/<slug>/index.html` on disk.

Run on a throwaway clone of a real lead's inputs (id 999002) so production state stayed clean;
all temp artifacts removed after (real spend rows kept — honest accounting).

---

*Files I created/edited: `dataset.py` (new), `STEP0-DONE.md` (new), `build_sample.py`,
`compare_candidates.py`, `config.py`, `.gitignore`. Do not commit until the human coordinates
both tabs.*
