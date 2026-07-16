# betting_sim — paper-trading harness for betting strategies

**No real money. Ever.** This package grades hypothetical bets, places them on
a paper bankroll, settles them against outcomes, and reports whether the
strategy actually beats the market. There is no bet-placement code anywhere in
here by design — `oddsapi.py` only *reads* prices.

The whole reason this exists: prove an edge on paper before risking a cent.
That's not caution for its own sake — it's the only way to find out whether the
"bet the safe favorite late" idea makes money *without paying tuition to a
sportsbook to learn the answer.*

## Run it (zero setup — pure stdlib)

```bash
python -m betting_sim.cli demo            # strategy comparison: why 'safe' loses
python -m betting_sim.cli shop            # line shopping & arbitrage (synthetic)
python -m betting_sim.cli realtest        # backtest on REAL free historical odds
python -m betting_sim.cli compound        # can $100 grow itself on favorites?
python -m betting_sim.cli pm              # LIVE Polymarket: spread, traps, arbs
python -m betting_sim.cli forecast        # score a model: skill, calibration, ROI
python -m betting_sim.cli paper --resolve # the live learning ledger (paper bets)
python -m betting_sim.cli kalshi --series KXFED   # live Kalshi markets (no key)
python -m betting_sim.cli grade           # grade one example market
python -m betting_sim.tests.test_oddsmath # money-math tests (+ test_lineshop, test_realdata)
```

The `demo`/`shop`/`grade` commands use synthetic data (known ground truth, for
testing the strategy logic). `realtest` uses **real** historical odds — see below.

## What the demo shows

The same synthetic season is run three ways:

| strategy | model | win rate | ROI | avg CLV | result |
|---|---|---|---|---|---|
| **favorite** (flat-stake safe sides) | market | **~80%** | **−9%** | **−0.064** | bankroll 100 → ~58 |
| **value** (bet edge) | market (no edge) | — | 0% | 0.000 | places 0 bets |
| **value** (bet edge) | *skilled model* | ~21% | **+21%** | **+0.029** | the only winner |

(Numbers are for the default 4000-game seed and a realistic favorite price floor
of decimal ≥ 1.10 — i.e. we don't even let it bet the insane −1000+ shots. Drop
the floor and the bleed gets *worse*, not better.)

Read the top row twice. **You win ~80% of your bets and still lose money every
season.** That is the "safe favorite" strategy exactly as described, and it's
a loser — not because the wins aren't real, but because:

1. **The vig** — the book's built-in margin — is a headwind on every bet.
2. **The favorite-longshot bias** — a real, decades-documented effect where
   heavy favorites are priced *shorter* than their true chance. Grinding
   favorites means systematically buying the most overpriced side.

The rare losses, each many times bigger than a win, plus that headwind, net out
negative. High win rate, negative expected value.

The middle row is the second lesson: "value hunting" with no better information
than the market places **zero** bets — because you can't beat a price by
agreeing with it. The Kelly sizer correctly stakes nothing on a zero-edge bet.

The bottom row is the only winner, and it required a **model that predicts
better than the market** (`synth.skilled_model`, a stand-in for something you'd
actually have to build and prove). That model is the entire job of profitable
betting. Everything else is bookkeeping.

## The one number that matters: CLV

**Closing Line Value** = did you get a longer price than the market's final
(sharpest) line? Positive CLV over a large sample is the strongest evidence you
have a real, repeatable edge — much more than a good win rate or even short-run
profit, which can both be luck. If a strategy's average CLV isn't positive, it
does not have an edge, full stop. Watch that column.

## Line shopping across books (`python -m betting_sim.cli shop`)

The same bet pays different prices at different sportsbooks. Always taking the
best available price is **free EV on every bet** — and it's the one edge a
retail bettor can capture without any predictive model. The `shop` demo prices
each game across 6 books (a sharp, low-vig book plus softer retail books) and
shows two things:

| what | single book (fanduel) | best line (shop all 6) |
|---|---|---|
| ROI on the same favorite bets | ~−6.9% | ~−4.7% |
| avg CLV | −0.065 | −0.041 |

Shopping recovers **~2 points of ROI and ~+0.025 CLV for free.** Note both stay
negative: **shopping cuts the vig you pay, it doesn't turn a −EV bet into a +EV
one.** What it *does* do is recover a big chunk of the house edge on every
ticket — and paired with a real edge, that's the margin between a slow loss and
a slow win.

**Arbitrage** is the one place shopping alone prints risk-free money: when the
best price on *each* side sums to under 100% implied, you stake both and lock in
profit regardless of outcome. In the sim these show up ~1–2% of games at ~0.3–1%
margin — which is realistic. They're small, fleeting, and books limit you for
taking them, but they're genuinely free. `lineshop.find_arbitrage` computes the
stake split and the guaranteed profit.

One honest caveat the sim makes visible: on an *efficient, simultaneous*
snapshot, even a sharp book's ~2% vig is bigger than the price disagreement
between books, so "bet whichever book beats the consensus fair value" almost
never clears as a standalone +EV play. Real soft-book value comes from **stale
or slow-moving lines** (a book late to update after news/an injury), not from a
clean snapshot — so this sim deliberately doesn't manufacture it.

## Can $100 compound itself on heavy favorites? (`cli compound`)

The plan: start with $100, bet heavy favorites (e.g. −800), and stake more as
the roll grows. `compound` Monte-Carlos it over 20,000 runs. The result is the
most important number in this whole repo:

```
python -m betting_sim.cli compound            # -800 favorite, 25% of roll
python -m betting_sim.cli compound --american -400 --fraction 0.1
```

Even in the **impossible best case** — the book takes *zero* vig, so the bet is
exactly break-even — staking 25% of the roll on a −800 favorite gives:

| | break-even (zero vig) | realistic (you pay vig) |
|---|---|---|
| avg edge per bet | 0.000% | −1.7% |
| **median ending roll (from $100)** | **~$43** | **~$12** |
| mean ending roll | ~$100 | ~$43 |
| ended in profit | 27% of runs | 11% of runs |
| ruin rate | 24% | 47% |

Read the median vs the mean. The **average** stays near $100 — that's the number
that makes the plan feel safe. But the **typical** run (median) loses more than
half, because compounding a high-variance bet has a *negative log-growth rate*
even at break-even. The mean is propped up by a few lucky runs you'll almost
never be in. Betting a bigger slice as you grow doesn't compound your way up —
it just adds drama on the way to zero.

**Compounding multiplies a real edge. It cannot create one.** With no edge it
drifts to zero; with a −EV bet it gets there faster. The only thing that makes
"start small and grow it" work is a genuine edge (positive CLV) *first* — then a
*small* fraction (quarter-Kelly) compounds it safely. Edge is the whole game;
bankroll growth is just what a real edge does on its own.

## Autonomous mode: scan everything, forecast, learn (`cli autopilot`)

The full loop, hands-free: scan every market → triage what's worth forecasting →
forecast it → paper-bet the disagreements → resolve → learn → repeat. Paper bets
are free, so it can run forever and keep improving.

**The one cost that isn't free:** forecasting. Pulling markets is free API calls,
but running an LLM + web search on each costs tokens, and there are thousands of
markets. So the system *triages* first (`scanner.py`) and spends the forecasting
budget only where edge is plausible.

```bash
python -m betting_sim.cli scan                 # rank ALL markets by priority (free)
python -m betting_sim.cli autopilot --dry-run  # show what it would forecast (free)
python -m betting_sim.cli autopilot --budget 20  # forecast + log (needs API key)
python -m betting_sim.cli learn                # what it has learned so far
```

The pieces:

- **`scanner.py`** — pulls every active market, tags a category, and scores each
  by priority: **skips near-certain markets** (no edge room), **requires
  liquidity** (an unbettable edge is worthless), **prefers fast resolution**
  (faster learning). A live scan just now: 405 markets → 123 clear the filters,
  ranked so the forecaster hits the best ones first. That's how you "investigate
  all bets" without paying to research thousands of dead ones.
- **`autopilot`** — walks the ranked list, forecasts each with `llmforecast`,
  applies the learned calibration correction, and logs paper bets on
  disagreements. `--dry-run` does the free triage; the live run needs your key.
- **`learn.py`** — the improvement engine. You can't fine-tune the LLM from a few
  outcomes, but you *can*: (1) track **skill/ROI by category** (bet more where it
  works, stop where it doesn't); (2) **fix miscalibration** with Platt scaling
  (if its 70%s hit 60%, correct future forecasts); (3) surface its **biggest
  misses** for few-shot feedback. Every estimate is gated on sample size — below
  ~20 resolved it refuses to conclude, because that's just noise.

**The honest expectation:** scanning everything mostly finds *no* edge — the
market already priced the news. The system's real job is to (a) discover that
truth cheaply, and (b) surface the rare pockets where edge might exist, then
prove them out-of-sample. It's built to accept "no edge" as an answer, not to
manufacture bets.

## Let it learn: the paper-trading ledger (`cli paper`)

This is the live experiment — does an LLM+web-search forecaster actually beat the
market? It's built so the learning is *honest*, with the discipline enforced by
design (see `paperlog.py`):

- Forecasts are logged **with a date, before the outcome exists**, in a JSON
  ledger (`paperbets.json`) committed to git. The commit history is tamper-proof
  proof we didn't predict a settled market with hindsight.
- **Every assessment is recorded, not just bets.** "Agreed with the market, no
  bet" still feeds calibration.
- Scores are computed **only on resolved markets** — strictly out-of-sample.
- The scorecard prints the sample size and refuses to let you conclude anything
  until N is meaningful (a handful of results is noise).

```bash
python -m betting_sim.cli paper            # show the ledger + (once resolved) the score
python -m betting_sim.cli paper --resolve  # snapshot open prices + settle closed ones
```

**CLV — the early-warning signal.** `--resolve` also snapshots each open bet's
current price, keeping the last price seen while the market was live as its
"closing line." On settlement it records **CLV**: did the line move *toward* the
side we bet? Positive CLV means the market came to agree with us — and it shows
up **before** the P&L does, on a smaller sample, *even on bets that lose*. It's
the single fastest read on "is the edge real?" The scorecard prints avg CLV and
flags it as the real-edge signal to watch first.

The first batch (logged 2026-07-16, from real web research on live Polymarket
markets) is a good illustration of how rare edge is: of 5 researched markets,
only **2** disagreed with the market enough to bet — both fading a slightly-high
price (Messi to be WC top scorer at 61%; U.S. to invade Iran before 2027 at 23%).
The other 3, the model just agreed with the market. That ratio *is* the lesson.
A recurring job re-runs `--resolve` so the ledger settles and scores itself over
time; the World Cup markets resolve within days, Iran at year-end.

## Building a forecasting model — the honest way (`cli forecast`)

This is the "use ML + web search to make a smart betting model" path, built so it
can't lie to you. **The model is the easy part; knowing whether it has an edge is
the hard part.** A model that's 80% accurate is worthless if the market is
already 82% — the market price is itself a strong aggregated model.

So the core here isn't a model, it's the **scorecard** (`forecast.py`) that grades
*any* forecaster three ways, all out-of-sample:

- **Brier skill vs the market** — is your forecast genuinely more accurate than the
  price? (skill > 0 or you have nothing)
- **Calibration** — when you say 70%, does it happen ~70% of the time?
- **Realized ROI** on edge-selected bets — the money-truth.

`python -m betting_sim.cli forecast` runs four forecasters over synthetic markets
with known truth:

| forecaster | skill vs market | hit rate | ROI | verdict |
|---|---|---|---|---|
| market-follower | +0.000 | — | 0% | places 0 bets — can't beat a price by matching it |
| **skilled (real edge)** | **+0.018** | 50% | **+7.1%** | **HAS EDGE**, well calibrated |
| random noise | −0.94 | 33% | ~0% | busy but no edge; wildly miscalibrated |
| overconfident | −0.06 | **74%** | **−0.3%** | high hit rate, still loses (miscalibrated) |

Note the last row: **74% of bets win and it still loses money** — the same trap as
"safe favorites," now caught by the scorecard. Your LLM/ML model is just another
row in this table. It only counts if skill *and* ROI are positive out-of-sample.

### The model layer

- **`llmforecast.py`** — an LLM + web-search forecaster: give it a market question,
  it researches with Claude's web search and returns a *calibrated* probability
  (superforecaster prompt: base rate → evidence → avoid overconfidence). It's a
  pluggable estimator whose output goes straight into `forecast.evaluate()`. Uses
  the repo's Anthropic key; costs tokens, so nothing runs it automatically.
  **Honest expectation:** on liquid markets the crowd already read the same news,
  so the LLM usually just reproduces the price (~zero edge). The plausible edge is
  *speed* (breaking news), *breadth* (many neglected markets), and *thin* markets.
- **`kalshi.py`** — live Kalshi market data (US-regulated exchange; the place to
  actually deploy an edge). Adapter is tested; surfacing liquid markets needs a
  `--series` ticker from your account, since the public feed front-loads dead
  combo markets.

**The workflow:** model → forecast → `evaluate` against real prices + outcomes →
deploy *only* if skill and ROI stay positive out-of-sample. Anything else is a
backtest fantasy.

## Prediction markets: Kalshi / Polymarket (`cli pm`)

Prediction-market *exchanges* fix the two things that doom sportsbook betting:
the drag is tiny (you pay the spread, not a ~4.5% vig) and **no one limits you
for winning** — so a real edge can actually scale. `pm` pulls **live** Polymarket
data (public API, no key) and points our tools at it. A recent run showed:

- **Drag:** median bid/ask spread ~**0.1¢** across ~98 liquid markets (vs a
  book's ~4.5% vig). Real, and far lower.
- **Safe-bet trap, still real:** live markets at 0.998 — risk **$499 to win $1**.
  On a ~zero-fee exchange that's roughly *break-even*, not a guaranteed loss like
  a sportsbook — but "break-even with a catastrophic tail" is exactly **Case A**
  of `cli compound`, where the median bankroll still shrank to ~$43. Better
  arena, same law.
- **Arbitrage honesty:** a 60-outcome "World Cup Winner" event showed a naive
  Yes-sum wildly off 1.0 — but **58 of 60 legs were illiquid mirages** (a 0.001
  screen price you can't actually fill). The **2 genuinely liquid legs summed to
  1.001** — i.e. where you can really trade, it's efficient (no arb). This is the
  #1 way naive prediction-market arb scanners fool themselves, and the tool flags
  it instead of pretending.

**Why this is the one arena worth exploring:** the vig is small, winning doesn't
get you banned, and there are real APIs to build on (Kalshi is US-regulated with
a clean trading API; Polymarket is crypto and a legal gray area for US persons —
start with Kalshi). The catch is unchanged: liquid markets are efficient, "safe"
shares are break-even at best, and a real edge = **forecasting or arbitrage you
can prove beats the settle price.** Our arb/CLV tooling transfers directly.

## Real historical data (`python -m betting_sim.cli realtest`)

No paid API needed. This pulls free CSVs from **football-data.co.uk**, which
publish, per match: the result plus decimal odds from 6 books (Bet365, Betway,
Interwetten, Pinnacle, William Hill, VC), both opening and closing. Real
multi-book prices + real closing lines + real outcomes.

```bash
python -m betting_sim.cli realtest                       # 4 EPL seasons (downloads)
python -m betting_sim.cli realtest --div SP1 --seasons 2324,2223   # La Liga
python -m betting_sim.cli realtest --csv path/to/E0.csv  # offline, local file
```

What 1,520 real EPL matches (2020–2024) actually showed:

| | at one book (Bet365) | shop best of 6 |
|---|---|---|
| flat-stake favorites ROI | **−1.44%** | **+1.44%** |
| avg CLV vs Pinnacle close | −0.029 | −0.014 |

Two real findings, straight from real games:

1. **Line shopping is worth ~+2.9 points of ROI, for real** — enough here to
   flip blindly-betting-favorites from a −1.4% loss to a +1.4% "profit". That's
   the free money in always taking the best price.
2. **But don't get excited.** Even the best-line version still has *negative*
   CLV (−0.014) — it isn't truly beating the sharp closing line, so that small
   positive ROI is more likely variance than a real edge. Real proof of an edge
   is *positive CLV*, and flat-favorite betting doesn't have it. Line shopping
   recovers the vig; it doesn't manufacture an edge.

The favorite-longshot bias is visible too: over the same matches, blindly
betting favorites (−1.4%) beats blindly betting longshots (−4.8%) — soccer
bettors overpay for underdogs. And 3-way arbitrage across the six books appears
in ~0.8% of matches at ~0.8% margin: real, rare, and small.

**Honest limit:** these are PRE-MATCH odds. They validate the market's pricing,
line-shopping value, and closing-line value for real — but they can't replay the
exact "bet the safe favorite in the 88th minute" spot, which needs in-game odds
history (paid/rare). The lesson transfers; the specific late-game numbers would
need live data.

## How the pieces fit

```
synth.py      generates games with a KNOWN true probability (+ vig + bias)
                │
                ▼
grader.py     forms a probability estimate, grades both sides of the market,
              picks the bet  ──uses──►  oddsmath.py  (odds/EV/Kelly/CLV math)
                │
                ▼
backtest.py   places the graded bet on paper, settles vs outcome, scores CLV
                │
                ▼
portfolio.py  SQLite paper bankroll → ROI, win rate, CLV, max drawdown
```

- **`oddsmath.py`** — all conversions and EV/edge/Kelly/CLV math. Pure
  functions, fully unit-tested. Trust nothing else until these pass.
- **`grader.py`** — the part you tune. A `Rubric` defines a strategy (weights +
  gates + staking). Ships `FAVORITE` (the control) and `VALUE` (the target).
  Plug your own probability model in as the `estimator`.
- **`synth.py`** — synthetic world with ground truth, so we can measure whether
  grades track EV. Real odds can't do that — you never know the true prob.
  `make_multibook_games` prices each game across several books for shopping.
- **`lineshop.py`** — best-line selection, sharp multi-book consensus, and
  arbitrage detection/sizing. The retail-edge toolkit.
- **`realdata.py`** — loads free real historical odds from football-data.co.uk
  (multi-book, opening + closing, with results). Powers `realtest`. No key.
- **`polymarket.py`** — live Polymarket data via the public Gamma API: spreads,
  safe-bet traps, and liquidity-aware arbitrage. Powers `pm`. No key.
- **`forecast.py`** — the model scorecard: Brier skill vs market, calibration,
  and edge-selected ROI. The honest test for any forecaster. Powers `forecast`.
- **`kalshi.py`** — live Kalshi market data (US-regulated exchange). No key.
- **`llmforecast.py`** — LLM + web-search forecaster (Claude); pluggable into
  `forecast.evaluate`. Costs tokens; never runs in the core sim or tests.
- **`bankroll.py`** — Monte-Carlo of a compounding bankroll (log-growth, ruin
  rate, median vs mean). Powers `compound`.
- **`oddsapi.py`** — *optional* adapter for real *current* odds from The Odds API
  (needs `ODDS_API_KEY`; free tier = live odds for `cli live`/`sports`).

## Where a real edge would come from (and the walls)

This harness will tell you honestly whether a strategy has an edge. To *have*
one, you'd need a genuine predictive model, or structural inefficiencies:
arbitrage across books, soft/stale lines, promo/bonus value. "Bet the heavy
favorite" is none of these — it's the most efficiently priced, highest-vig part
of the market.

Practical walls to know before any real money:
- Most US sportsbooks have **no public bet-placement API**. Realistic surfaces
  are odds-*data* feeds and betting *exchanges* (e.g. Betfair).
- **Books limit or ban winners.** A working edge gets your stakes cut fast.
- Gambling laws vary by jurisdiction; this is a research/simulation tool.

## Next steps if you want to keep going

1. Swap `synth` for real historical odds + results and re-run the backtest.
2. Replace `skilled_model` with an actual model (Elo, market-based, ML —
   whatever) and see if its average CLV stays positive out-of-sample.
3. Only if it does, and only then, talk about tiny live-money tests.
