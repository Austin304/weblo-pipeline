# Lead Scoring — Design Notes (for review)

> **Companion doc:** niche selection, channel fit, and the revenue math live in
> `FUNNEL-AND-NICHE-STRATEGY.md`. That doc decides *which pond to fish in*; this one
> decides *which fish to pull first, in what order, for how much money*. This doc assumes
> the campaign niche + channel have already been chosen there.

Working notes on how the pipeline should decide *which* leads to pursue and in what
order. This is the #1 lever on reply/conversion rate, so it's worth getting right
before touching the schema. Nothing here is built yet — this is for Austin to think over.

Status: **draft / discussion.** Decisions in here still need a final okay.

---

## The core principle

> Pull each signal at the cheapest place it can be pulled, and spend the expensive
> signals only on leads that already survived the cheap ones.

Lead-scoring wastes money two ways if done carelessly:
1. Spending API budget scoring leads you'll never contact.
2. Spending a sample-generation (Claude) + an outreach slot on a lead that was never
   going to buy.

The whole design below is about avoiding both.

---

## 1. Ticket size is a CAMPAIGN lever, not a per-lead score

Revenue-per-customer is decided by **which niche you target**, not by scoring each
business. `CAMPAIGN_NICHE = "roofers"` vs `"nail salons"` settles ticket value for the
entire batch — for free, zero API calls.

**Which niche to pick is decided in `FUNNEL-AND-NICHE-STRATEGY.md`, not here.** That doc
filters niches by *channel reachability first* (can I find an email?), then ticket value,
then how good the sample will look — which is why, e.g., roofers/HVAC are high-ticket but
belong on the **postcard** track (no findable email), while med spas are the top **email**
pick. Don't reduce niche choice to a flat "high-ticket list" — a high-ticket niche you
can't reach on your free channel will starve the email pipeline.

**Action:** treat niche selection as a deliberate strategic choice per campaign (per the
funnel doc's two-bucket model), not a placeholder. It's the biggest, cheapest lever in the
system.

---

## 2. Signal we are deliberately NOT using: Google Ads

"Do they run Google Ads" is the strongest *buying* signal in theory — but the Places API
won't give it to us, and detecting it reliably needs SERP scraping / ad-auction rendering,
which the free VM (~1 GB RAM, no Chromium) can't do. Chasing it would cost more than it's
worth and be fragile. **Dropped as a signal on purpose.**

---

## 3. The per-lead funnel (cheap → expensive)

Only per-lead signals live here. Ticket size is already handled at the campaign level (§1).

| Tier | Cost | What it checks | Notes |
|---|---|---|---|
| **Tier 0** | Free | Category matches niche, has address, is operational | From basic Places fields already pulled. Drops junk for $0. |
| **Tier 1** | Free | Website pain (the "weak link" axis) | One HTTP fetch via `requests` (no Chromium). **No site → auto-passes this axis.** Has a site → score quality by heuristics: no HTTPS, no mobile viewport meta, parked/builder domain, thin content, stale copyright year. Only leads *with* a site cost a fetch. |
| **Tier 2** | **Paid** | Review volume (the "real, investing business" proxy) | `user_ratings_total` from Places. High reviews + bad site = gold. **Fetched LAST, only for Tier 0/1 survivors** — this is the whole cost-control game. |

> **Cost note:** review count / rating are the more expensive Places field tier
> (atmosphere/enterprise). Current pricing shifts — confirm before build. Fetching them
> only on survivors keeps this cheap.

---

## 4. Transparent weighted score (not a black box)

```
lead_score = website_pain (0-40) + review_volume (0-40) + category_fit (0-20)
```

**Store the components, not just the total.** When reply/close data comes in, we want to
see *which signal actually predicted conversion* and reweight — impossible if only the
total is saved.

---

## 5. Rank, don't reject  ← the "take money where I can get it" rule

The score decides **order**, never inclusion. Nobody is deleted for a low score.

- Score everyone → sort descending → work top-down until the daily cap / budget is hit.
- Low scorers sit at the bottom of the queue, not in the trash.
- A $300 customer is still $300, and contacting a low-scored lead by email costs ~nothing.

Selectivity = **sequencing**, not exclusion.

---

## 6. Let the CHANNEL decide how picky to be

This is where the $300-vs-$1k worry actually lives:

| Channel | Marginal cost per send | How picky |
|---|---|---|
| **Email** | ~Free | Cast wide. Loose ranking, contact almost everyone. A $300 close on a free email is pure profit. |
| **Postcards (Lob)** | Real $ each | Be genuinely picky. Spend physical-mail budget on higher-scored / higher-ticket leads only — each send has hard cost + postage. |

So there isn't one universal threshold — there's a **low bar for the free channel and a
higher bar for the paid one.** The postcard track gets its own score gate.

---

## 7. Learning from outcomes — capture now, auto-tune later

Should the score adjust as deals close, weighted by deal size? **Eventually yes. Not
automatically, not early.**

At 10–40 sends/day you'll have a handful of closes for a long while. Auto-reweighting on
3–4 deals isn't learning — it's overfitting to noise (it'll "discover" that businesses
named after the owner's dog convert, because two did).

Phased approach:

1. **Now:** add `deal_value` to the DB, record it on every close. Costs nothing; it's the
   ground truth we'll need.
2. **Early:** *Austin* reweights by eye — look at what closers had in common, nudge weights
   by hand. Human pattern-matching beats regression on tiny N.
3. **At volume (~30–50+ closes):** value-weighted auto-tuning becomes reasonable — and even
   then, kept as a *suggestion to approve*, not a silent change.

---

## 8. Careful tradeoffs to decide consciously

- **Threshold starvation.** Score too hard and the funnel dries up (need ~50 qualified —
  `CAMPAIGN_TARGET_COUNT`). Better to score everyone, *rank*, and take the top N than to
  hard-cutoff and come up short. (This is why §5 exists.)
- **Don't let scoring itself get expensive.** If heuristics (Tier 1) prove too noisy we
  *could* use a cheap Haiku call to judge site quality — but that's one model call per
  lead-with-a-site, a real cost line. Heuristics first; model only if needed.

---

## 9. Proposed schema changes (small)

- `lead_score` (int) + its components: `score_website_pain`, `score_review_volume`,
  `score_category_fit`
- `deal_value` (nullable; set on close)
- postcard track: its own higher score gate (config threshold, e.g. `MAIL_MIN_SCORE`)

---

## Open decisions before build

1. **First niche + geography?** (§1 — biggest free lever.)
2. Confirm the weight split in §4 feels right, or adjust.
3. Set the two channel thresholds (§6): email bar (low) and postcard bar (higher).
