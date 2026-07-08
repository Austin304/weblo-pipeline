# Exemplars — the grading feedback loop

Files here are injected into the sample generator's **system prompt** (see
`build_sample._style_context`), so they steer every future generation at
almost no cost (the system prompt is cache-read at ~10% of input price).

- `<niche>.md` (e.g. `beauty.md`, `trades.md`) — a distilled style crib from a
  top-graded sample of that niche. Created by `run.py exemplar <lead_id>`.
  Only distill samples you'd be proud to send; one great exemplar beats three
  okay ones (a new distill overwrites the old file).
- `LESSONS.md` — hand-curated, cross-niche lessons from grading (recurring
  CHANGE-FIRST notes, confirmed wins). Written by a human, not a tool. Keep it
  short and imperative ("Never open with a star-rating badge"; "Overlay heroes
  need a gradient scrim for text contrast"). `run.py insights` surfaces the
  raw notes to curate from.

This README is NOT injected — only `<niche>.md` and `LESSONS.md` are.
