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
python -m betting_sim.cli shop            # line shopping & arbitrage across books
python -m betting_sim.cli grade           # grade one example market
python -m betting_sim.tests.test_oddsmath # the money-math tests
python -m betting_sim.tests.test_lineshop # the line-shopping / arb tests
```

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
- **`oddsapi.py`** — *optional* adapter for real current odds from The Odds API
  (needs `ODDS_API_KEY`; historical odds for a real backtest are a paid tier).

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
