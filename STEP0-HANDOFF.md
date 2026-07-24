# STEP0-HANDOFF.md

Handoff from the **preview-build / their-own-words** Claude tab to the **Step-0
training-data-capture** tab. Written 2026-07-24.

Scope rule I followed: this file contains only what lived in **my chat / memory** and
is **not already trivially in the repo**. Where the real source is the repo or the DB,
I give a **pointer**, not a copy, and label it `[REPO]`. Everything under `[CHAT]` is
connective tissue that is *not* recoverable by reading files.

---

## 0. TL;DR for you (read this first)

- **COLLISION RISK:** I modified `build_sample.py` (added a `preview_dir` param through
  `_ship_sample` → `_try_best_of_n` → `build_one`). You will almost certainly also touch
  `build_sample.py` to hook candidate/pick logging in the *same* functions. That file was
  **already dirty** (uncommitted prior-session work) before I touched it. **Coordinate /
  rebase carefully.** See §4.
- **DATA DAMAGE:** my preview build **overwrote `logs/bon/278_0.html` and
  `logs/bon/278_1.html`** today at 11:07. Those were the raw best-of-N candidates from the
  278 human-pick run. `logs/bon/278_0.html` was *the picked candidate* — it is now a
  different generation. `278_2/3.html` (Jul 23) are untouched. See §4. This is a live demo
  of exactly the ephemerality Step-0 must fix (`logs/bon` is overwritten by any later build
  of the same lead).
- **The real human signal is pairwise PICKS + free-text CHANGE/KEEP notes, not scalar
  scores.** Design that around this.

---

## 1. Grading data

### `[CHAT]` Human picks — round-robin, dental (NOT fully in the DB)
The DB stores the shipped slug but **not** which design language / which best-of-N
candidate index the human chose. That mapping is chat/memory-only:

| Lead | Human pick | Candidate file | Shipped slug | Status |
|---|---|---|---|---|
| 246 Lakewood Family Dental Care | **#3 Warm Editorial** | `logs/bon/246_2.html` | `lakewood-family-dental-care-dallas-dallas-27f0b9ac60` | shipped 2026-07-24 |
| 261 Park Cities Family Dentistry | **#1 Dark Luxe** | `logs/bon/261_0.html` | `park-cities-family-dentistry-dallas-01f73b6176` | shipped 2026-07-24 |
| 278 Dallas Dental Arts | **#1 Warm Editorial** | `logs/bon/278_0.html` ⚠ **overwritten today** | `dallas-dental-arts-dallas-99d89a2c13` | shipped 2026-07-24 |
| 271 Whitley Family Dental | **#2 Clinical Modern** | (not noted) | `whitley-family-dental-dallas-3cd46707ba` | LIVE, done 2026-07-23 |

- **246 v1 candidate order** (best-of-4, gate-passers in generation order):
  #1 Clinical Modern, #2 Dark Luxe, #3 Warm Editorial, #4 Airy Minimal.
- **261:** the AI pairwise-tournament winner was **Airy Minimal**, but the human picked
  **#1 Dark Luxe** — i.e. a concrete case where the human OVERRODE the AI judge. Good
  hard-negative/positive pair for training the scorer.
- Candidate-file index `i` (`logs/bon/<lead>_<i>.html`) = position among **gate-passers**
  in generation order. The design language per index is **not** stored next to the file —
  it's only in the build log line `bon cand N (<language>)`. Step-0 should persist language
  explicitly.

### `[CHAT]` Austin's qualitative review of 246 v1 (words, not numbers → became the lessons)
- Clinical Modern = **best visuals**
- Warm Editorial = **best copy** (he now uses "246 Warm Editorial" as the copy-voice benchmark)
- Dark Luxe = best **"what we do"** (compact side-by-side, less scrolling)
- All smiling photos great; **none of the empty-office heroes** were good.

### `[REPO]` Numeric grades already in `leads.db` → table `grades` (pointer, not reproduced)
- **16 rows exist.** Columns (the real rubric factors): `hero, design, layout, imagery,
  copy, trust, beats, overall, vision, notes, sample_slug, created_at`.
- Most rows are **med-spa era** (leads 41/46/144/189/191/206/207/209/215/230/237…) — still
  valuable: they're the bulk of the human-annotated signal.
- **Dental-relevant rows:** lead **246** (hero 4 / imagery 4) and lead **271** (hero 4 /
  imagery 4), both with rich notes. **Leads 261 and 278 have NO grade row** — they were
  eyeballed and shipped, never graded.
- **Join gotcha:** the 246 and 271 grade rows point at **earlier v1 slugs**
  (`…lakewood…-3c2a89990f`, `…whitley…-9460f36dfd`), *not* the current shipped slugs above.
  `grade.sample_slug ≠ current shipped slug` for regenerated leads.
- **Annotation format to preserve:** notes follow a consistent
  **`CHANGE: … | KEEP: …`** structure. That's the natural human-label schema — capture it
  as two fields, not one blob.
- Scores are coarse (mostly 2–4 on 1–5). The **notes** carry far more signal than the numbers.

### `[CHAT]` This session added **no** new grades and no new picks.

---

## 2. Grading criteria / rubric

### `[REPO]` The graded dimensions (from the `grades` schema; listed because you asked)
`hero, design, layout, imagery, copy, trust, beats` = the 7 factors (the `ABCDEFG` in
`run.py grade <id> <7 digits> <overall/10> [note]`), plus `overall` (/10) and `vision`.
Behavior link `[REPO, build_sample.build_one]`: a grade with **hero ≤ 3 or imagery ≤ 3**
makes the next rebuild EXCLUDE that sample's images.

### `[CHAT]` The specifics we defined (now mirrored in `exemplars/LESSONS.md` `[REPO]`, consolidated here)
**Voice / Copy**
- Warm, natural, like a real grounded person. Benchmark = **246 Warm Editorial**.
- Plain everyday phrasing, not quirky near-misses ("fits your week" → "fits your schedule").
- Don't hang a sub-description under **every** heading (reads as "trying too hard").
- Don't lean on one mood-word (generator overused **"unhurried"** — now de-seeded from the brief).
- Cut stiff/filler/robotic lines. Real rejected examples: "planned around your face"
  (scary), "lighter legs" (no one says that), "treats you like a person not a chart",
  copy that "reads like someone who just learned english."

**Typography**
- No cursive / heavy-italic face for body-length text, **especially testimonial quotes**
  (the offender was large italic Playfair). Also flagged a "curvy f in 'Refined'."

**Design**
- Premium/designed beats flat template ("clean but plain" loses).
- **Add motion** — hover-raise on buttons, reveal-on-scroll. Repeated ask (leads 209, 46, 230).
- Warmer/richer palettes read premium (Dark Luxe cited positively).

**Layout**
- "What we do" / services **compact side-by-side, not an endless scroll**.
- Clear hierarchy/rhythm; avoid randomly-placed / off-center stacking (lead 144).

**Hero**
- A genuine **smiling person beats an empty room** / empty office / clinical or
  storefront/parking shot.

**Imagery**
- **NEVER reuse a photo** across samples (each photo at most once). Repeated complaint
  (230, 46, 271).
- Photo must match the actual modality (fatal example: sauna/massage photos on an
  injectables practice, lead 206).

### `[CHAT]` Meta-point for the scorer
Austin grades by **pairwise "which is better" + CHANGE/KEEP prose**, and only sometimes
gives coarse 1–5 numbers. A pairwise/preference model over candidates will fit his signal
better than regressing the scalar factor scores. (Note also: calibration found an **absolute
1–5 vision judge was unreliable; pairwise was 4/4** — same lesson applies to the human data.)

---

## 3. Step-0 plan / spec / data schema I drafted

**None.** I drafted no Step-0 plan, spec, or schema. My session was unrelated work
(preview-build mode + diagnosing the "their-own-words" extractor).

`[CHAT]` Non-spec findings from this session that your schema should account for (offered as
facts, not a design):
- `logs/bon/<lead>_<i>.html` is **ephemeral** — overwritten by any later build of the same
  lead (I just proved it on 278). Not a durable store.
- The **design language** of each candidate is not persisted beside the file (only in the
  build log). Persist it.
- The **human pick** (which index won) was never persisted anywhere durable — it lived in
  Austin's terminal + a memory note. This is the single most important field to capture.
- **the-own-words extractor is currently a no-op on 3 of 4 dental sites** — their sites are
  JS-rendered and `_site_text_for_facts` (plain `requests.get`) reads **0 chars**
  (dallasdentalspa.com, cosmeticdentistindallas.com, billwhitleydds.com). Only
  lakewoodfamilydental.com reads, and even it yielded services but **zero** voice lines. If
  Step-0 training features include "their real wording," know it's empty for most leads
  until the fetch uses a real browser (Playwright is installed).

---

## 4. Files I created / edited this session (collision list)

**Mine, this session:**
- **`build_sample.py` — MODIFIED.** Added optional `preview_dir` param to `_ship_sample`,
  `_try_best_of_n`, and `build_one` (renders a rebuild to a folder with **no** DB /
  `samples_cache` write). All new params default `None`, so normal builds are unchanged.
  ⚠ **This is the high-collision file** — you will likely edit `_try_best_of_n` / `build_one`
  too. It was **already uncommitted-dirty** before my edits.
- **`_preview_build.py` — CREATED** (repo root). Disposable runner:
  `python _preview_build.py <lead_id> [n]`. Safe to delete.
- **`preview/lead_278/` — CREATED** (build output: `index.html`, `img0.png`, `img1.png`,
  `img2.jpg`, `img3.jpg`, `sources.json`). Untracked dir. Delete anytime.
- **`logs/bon/278_0.html`, `logs/bon/278_1.html` — OVERWRITTEN** (2026-07-24 11:07) by my
  best-of-2 preview of 278. **Provenance loss:** `278_0.html` was the human-picked candidate;
  it's now a different generation. `278_2/3.html` (Jul 23) are intact.
- **`leads.db` — cost rows added.** My preview build wrote `costs` rows for the 278
  generate/vision/fact-check calls. **No** `leads`/`grades`/`samples` rows changed (preview
  mode skips all of that).
- **`logs/site_facts_cache.json` — holds schema-2 extractions** for the 4 dental sites
  (lakewood = services only; other three = empty `{}`). May have been written by my build or
  by a `_test_sitewords.py` run; either way it's current.

**NOT mine — pre-existing untracked at session start (don't attribute to me, but you'll see
them):** `_test_sitewords.py`, `compare_candidates.py`, `compare_246.html`,
`compare_261.html`, `compare_278.html`, `llm.py`, `NICHE.md`, `PERSONAL-MACHINE-SETUP.md`,
`.claude/`. (`build_sample.py`, `calibrate.py`, `config.py`, `costs.py`,
`exemplars/LESSONS.md`, and others were already `M` before my session too — only the
`preview_dir` threading in `build_sample.py` is mine.)
