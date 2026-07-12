# 09 — Metrics, Kill-Switch & Feedback Loop (Know if it's working; lean in or stop)

## Objective
Turn the numbers already sitting in `leads.db` into a simple feedback system that (a) **stops the campaign and pings the operator when the strategy isn't working** (the brake), and (b) **flags — and eventually leans into — what IS working** (the accelerator). It reports *what* is happening, not *why*; the operator infers the why and decides the move.

> Read doc 00, doc 04 (state/orchestration), and doc 02 (Telegram) first. This doc reads the same DB every other stage writes to and reuses the Telegram notifier from doc 02 and the pause/alert pattern from doc 08. It adds no new external service.

---

## What it measures (the funnel)
Every stage already writes to `leads.db`, so the metrics are just counts and ratios over it. Measured per campaign (niche + location), rolling and month-to-date:

| Step | Count | Rate that matters |
|---|---|---|
| Emailed | emails sent | — |
| Delivered | sends minus bounces | **bounce rate** = bounces / sent |
| Opened sample | first `sample_visits` row per lead | **open rate** = opened / delivered |
| Replied | replies detected (doc 02) | reply rate = replies / delivered |
| Interested | replies classified positive | **positive rate** = interested / delivered |
| (postcards) | mailed / scanned QR / interested | same shape, tracked separately |

The postcard track (doc 06) gets its own copy of these — never blend the two channels into one number, or you can't tell which is working.

---

## The brake — kill-switch (stop when it isn't working)
For each metric, a floor (or ceiling) in `.env`. When a metric crosses its line **and enough sends have happened to be real** (`METRICS_MIN_SENDS`, default 100), the check:
1. **Pauses** the relevant job (sets a `paused` flag the sender/finder checks before running — same mechanism doc 08 uses for budget).
2. **Pings the operator on Telegram** with the symptom, not a guess:

> ⚠️ Campaign *plumbers / Dallas*: 120 delivered, 0.2% positive (floor 0.5%). Sending paused. Regroup, then `/resume`.

| Guard | Default | Why |
|---|---|---|
| `MAX_BOUNCE_RATE` | 3% | **Build this first, always on.** High bounce burns the domain you warmed for 2 weeks. Trip early (even before 100 sends). |
| `MIN_OPEN_RATE` | 15% | Low opens → subject line or deliverability, not the sample. |
| `MIN_POSITIVE_RATE` | 0.5% | The real "is this working" line. Only tunable *after* real sends give real numbers. |

The message names which metric failed, so the operator can infer the fix: bounce → lead/verify quality; opens fine but no replies → email copy or the sample; nothing opens → subject line / deliverability.

---

## The accelerator — lean into what works
Same data, opposite direction. Instead of one blended number, break the funnel down **by segment**: niche, location, sample archetype, image source (doc: `sample-design-system.md`), and channel. When one segment clearly beats the rest, surface it.

**Two-speed, because winners need more data than losers to trust** (failure shows up fast; "plumbers beat salons" off 3 replies is noise):

- **Early (low volume) → suggest, operator approves.** Telegram flags the direction and asks:
  > 📈 Campaign: plumbers 3/60 positive (5%) vs salons 0/55 (0%). Weight new leads toward plumbers? `/yes` `/no`
  Nothing pivots without the operator's ok.
- **Later (`METRICS_MIN_SEGMENT_SENDS`, default 200 per segment) → auto-lean.** Once a segment has real volume, the pipeline can bias on its own and just report it.

What "leaning in" actually adjusts:
| Signal | Action |
|---|---|
| Healthy bounce + open + positive rates | Raise `DAILY_SEND_CAP` toward the top of the safe range faster (still inside doc 05's ramp ceiling). |
| One **niche/location** converts best | Bias stage-1 auto-top-up (doc 04) toward it — point the machine at the fish that bite. |
| One **sample archetype / image source** converts best | Bias generation (`sample-design-system.md`) toward it. |
| One **channel** (email vs postcard) converts best | Shift volume/budget toward it. |

Hard rule: the accelerator can raise the send cap only **within** the deliverability ramp in doc 05 — it never overrides the warm-up ceiling to chase a good number.

---

## Where it lives
- A small `metrics.py` that computes the funnel from `leads.db` (pure reads).
- A scheduled check (piggybacks on the existing `cron` jobs, doc 04) that runs the brake + accelerator logic and calls the doc-02 notifier.
- A `/metrics` Telegram command (doc 02) to pull the current funnel on demand.
- The pause flag + `/resume`, `/yes`, `/no` operator commands wire into the same command handler doc 02 already has.

## Config (`.env`)
```
METRICS_MIN_SENDS=100              # min delivered before rate-based kill switches arm
METRICS_MIN_SEGMENT_SENDS=200      # min per-segment volume before auto-leaning (below this: suggest only)
MAX_BOUNCE_RATE=0.03               # always on, can trip before MIN_SENDS
MIN_OPEN_RATE=0.15
MIN_POSITIVE_RATE=0.005
METRICS_CHECK_CRON=daily           # how often the check runs
```

## Honest caveats (don't skip)
- **It says WHAT, not WHY.** By design — the operator infers the why from which metric moved. Don't try to make it diagnose root cause.
- **Thresholds are guesses until you have data.** Start with the defaults, watch the first real batch, then set them from your own numbers. Bad thresholds either cry wolf or never trip.
- **Failure is trustworthy fast; success is not.** Never auto-pivot the whole campaign on a handful of replies — that's what the suggest-then-approve early mode is for.
- **Build order:** bounce guard first (protects the domain), then the funnel + `/metrics` view, then the positive-rate kill switch, then the accelerator last (it's the least time-sensitive and needs data to be meaningful).
