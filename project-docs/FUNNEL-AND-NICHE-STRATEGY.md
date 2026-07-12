# Funnel & Niche Strategy — Thinking Doc

*Created 2026-07-01. Working notes for sizing the web-design pipeline. Every number labeled "est." is an assumption to replace with real data once the pipeline runs. This is for thinking, not a forecast.*

> **Companion doc:** this doc picks *which pond to fish in* (niche, channel, revenue math). The per-lead mechanics — how the pipeline scores and orders leads within a chosen niche — live in `lead-scoring-design.md`.

---

## Bottom line (read this first)

- **Your $1-2k/mo side-income target is reachable — but NOT the naive way.**
- The **send cap (up to 40 emails/day = ~780/mo) is not your real limit.** Your real limit is **how many qualified leads you can actually supply.** Leads are a bucket you empty, not a tap that runs.
- Realistic sustainable supply is more like **~150-300 emails/mo**, not 780.
- So you hit the target by winning on the levers you **control** — **reply rate, price, and how fast you open new territories** — not by sending more.

**The trap (looks fine on paper, isn't):**
> $500 site + 1.5% reply rate + *hoping* for 780 emails you can't supply = **$1,755 on paper, ~$420 in reality.**

**The bulletproof recipe:**
> Tight niche (pushes reply rate to ~3%) + price at **$750-1,000** + open **3-5 territories/month** = **~$1,700-2,100/mo** on volume you can actually feed.

---

## Part 1 — What niches to target

Think of it as **two buckets, split by how you reach them.**

### Bucket A — Email-friendly (feeds the automated machine) ← PRIORITY

Businesses with **office staff**, so an email is usually findable on their site or Facebook page. This is the channel you're automating, so these come first.

- **Med spas / aesthetic clinics** ← top pick
- Small / solo **law firms**
- **Dentists**, orthodontists
- Chiropractors, physical therapy, wellness studios
- Accountants / bookkeepers

**Why they work:**
1. Findable email → fuel for the auto-emailer.
2. Real money per customer → a $500-1,000 site is trivial to them.
3. A good website genuinely wins them business.
4. Med spas especially are **visual** → your sample site looks gorgeous → drives replies.

### Bucket B — Postcard-friendly (high value, but no email)

The **trades.** Huge job value, terrible/no websites — but often just a cell number, so you usually **can't find an email.** Reach them **by mail** (postcard track).

- Roofing, HVAC, plumbing, electrical
- Remodeling / general contractors
- Landscaping, hardscaping, pools

**Why they're a mail play:** one job is worth $5-15k, so $500 is a rounding error to them — but no findable email means the postcard is how you get in front of them.

### The move

**Start with ONE Bucket A niche in ONE city** (med spas or small law firms). Get the email loop working there first. Run trades on postcards in parallel once it's humming.

### Why niche down at all — three plain reasons

1. **Better emails** — you learn how they talk, so your "one specific observation" gets sharp → more replies.
2. **Better samples** — your template gets dialed in for one business type → consistent quality.
3. **Easier lead-finding** — you know exactly what to search and where their emails hide.

---

## Part 2 — The math, step by step

Using an office niche (Bucket A). All rates are **estimates** — replace with real numbers as you go.

### Stage 1 — What ONE niche in ONE city gives you

```
Businesses you can find (search by area/zip)        450   (est.)
  |- have a bad or no website?   ~25%   (est.)  ->  112   qualified
  |- of those, email findable?   ~55%   (est.)  ->   62   emailable leads
                                                    (+ ~50 no-email -> postcards)
```

**One niche + one city = ~62 email leads. ONE-TIME.**
You email them once (+ one follow-up), and that territory is spent.

### Stage 2 — Supply over time (how fast the bucket refills)

You get more leads only by **opening new niche-cities** (research + qualify a new one).
Assume you can open **3 per month:**

```
3 territories/month  x  62 email leads each  =  ~186 emails/month
```

**~186 emails/month is your real fuel — not 780.**
(Plus ~150 postcards/month from the no-email leftovers = bonus second stream.)

### Stage 3 — Turning emails into money

```
Emails sent this month                              186
  x interested-reply rate    1.5%  (est.)  ->       2.8   interested replies
  x close rate               30%   (est.)  ->       0.84  paying clients
  x price per site           $500          ->       $420  / month
```

**$420/month — the honest baseline. UNDER the $1-2k target.** This is the gap to close.

### Stage 4 — Three ways to close the gap (with math)

Four knobs: **emails, reply rate, close rate, price.** Emails are capped by supply, so push the other three.

**Fix A — Raise price to $1,000**
```
0.84 clients x $1,000 = $840/mo        <- better, still short
```

**Fix B — Raise reply rate to 3%** (tight niche + great samples)
```
186 x 3% = 5.6 interested x 30% = 1.7 clients x $500 = $840/mo   <- same ballpark
```

**Fix C — Do both (the winner)**
```
186 x 3% x 30% x $1,000 = 1.7 clients x $1,000 = ~$1,680/mo      <- ON TARGET
```

**Fix D — Or open more territories (5/mo instead of 3)**
```
5 x 62 = 310 emails x 3% x 30% x $750 = 2.8 clients x $750 = ~$2,100/mo   <- ON TARGET
```

Postcards stack on top of any of these.

---

## The levers, ranked by impact

1. **Reply rate** — the widest swing. 1.5% -> 3% *doubles* everything downstream. Driven by **sample quality + niche fit.** This is why grading the first ~30 samples by hand matters most. *Operationalized by `lead-scoring-design.md`* — contacting better-fit leads first is how you actually move this number.
2. **Price** — $500 -> $1,000 halves the clients you need. Easiest single fix. Fewer, better-paid clients beats chasing volume you can't supply.
3. **Territories/month** — sets your supply ceiling. 3 -> 5 is +67% fuel.
4. **Close rate** — matters, but you only control it partway (depends on the lead).
5. **Send cap** — was never the real limit. Don't optimize this first.

---

## Reference: cost per sample (from earlier)

- AI sample build uses `claude-opus-4-8`: **$5 / 1M input tokens, $25 / 1M output.**
- One generation ~ 2k in + ~7k out = **~$0.19.** With up to 2 quality-gate retries: **~$0.20-0.30 per finished sample.**
- At `CLAUDE_MONTHLY_BUDGET_USD=50`, that covers ~150-250 samples/mo — **more than enough** at realistic lead volume. Pre-building a sample for every lead is affordable; cost is NOT the constraint. (Templating is a *quality-consistency* lever, not a cost lever.)

---

## Assumptions to validate (these numbers are guesses today)

| Assumption | Guess used | How to check |
|---|---|---|
| Businesses per niche per city | ~450 | Run a real Places search for your niche + city |
| Bad/no-site rate (qualify %) | ~25% (office) | Count from your first qualify run |
| Email-found rate | ~55% (office) | Track `email_status='found'` vs total qualified |
| Interested-reply rate | 1.5% (est.) | **The #1 number to measure** — first real campaign |
| Close rate | 30% | Your own results after talking to interested leads |
| Territories openable / month | 3 | Time yourself researching + qualifying one |

**Watch item — Places cost:** pulling ~450 businesses/niche with full Place Details calls may bump the `PLACES_MONTHLY_BUDGET_USD=20` cap once you open several territories/month (Place Details is the expensive call). Verify current Places pricing; minimize detail lookups by leaning on Text Search's built-in fields.

---

## One-line summary

The whole game is **reply rate x price x how fast you open territories** — three numbers you control — **not** the send cap, which was never the real limit. Nail one visual, email-friendly niche (med spas), price at $750-1,000, and keep opening territories, and $1-2k/mo is realistic on volume you can actually supply.
