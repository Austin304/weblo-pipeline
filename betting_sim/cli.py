"""Command line for the paper-trading sim.

  python -m betting_sim.cli demo                # the headline comparison
  python -m betting_sim.cli demo --games 5000   # bigger sample
  python -m betting_sim.cli grade               # grade one example market

The `demo` runs the SAME synthetic season through three lenses:

  1. favorite / market model  -> your original 'grind safe favorites' idea
  2. value    / market model  -> value hunting with NO edge over the market
  3. value    / skilled model -> value hunting WITH a real predictive edge

Watch the ROI and avg CLV columns. (1) and (2) bleed; only (3) — the one that
required actually building a model that beats the market — makes money.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from . import grader, lineshop, synth
from .backtest import run
from .grader import market_estimator
from .portfolio import Portfolio


def _tmp_db(tag: str) -> str:
    d = Path(tempfile.mkdtemp(prefix="betting_sim_"))
    return str(d / f"paper_{tag}.db")


def cmd_demo(args: argparse.Namespace) -> None:
    games = synth.make_games(args.games, seed=args.seed)
    bankroll = args.bankroll
    print(f"\nSynthetic season: {len(games)} late-game spots "
          f"(seed={args.seed}, starting bankroll={bankroll:.0f}u)")
    print("FAVORITE flat-stakes 1u per bet; VALUE sizes by quarter-Kelly.\n"
          "ROI < 0 or CLV < 0 means the strategy loses over the long run.")

    # 1) favorite strategy, believing the market (your original idea)
    p1 = Portfolio(_tmp_db("fav"), bankroll)
    r1 = run(games, grader.FAVORITE, p1, estimator=market_estimator,
             min_grade=args.min_grade)
    print(r1.format())
    p1.close()

    # 2) value strategy, but with no better estimate than the market
    p2 = Portfolio(_tmp_db("val_market"), bankroll)
    r2 = run(games, grader.VALUE, p2, estimator=market_estimator,
             min_grade=args.min_grade)
    print(r2.format().replace("VALUE", "VALUE (market model — no edge)"))
    p2.close()

    # 3) value strategy WITH a real predictive model (the only winner)
    model = synth.skilled_model()
    for g in games:                       # let the model know each game's truth
        model.registry[g.game_id] = g.true_home_prob  # type: ignore[attr-defined]
    p3 = Portfolio(_tmp_db("val_skilled"), bankroll)
    r3 = run(games, grader.VALUE, p3, estimator=model, min_grade=args.min_grade)
    print(r3.format().replace("VALUE", "VALUE (skilled model — real edge)"))
    p3.close()

    print("Takeaways")
    print("  * 'safe favorite' grinding: high win rate, negative ROI & CLV.")
    print("    The wins are real; the rare losses + vig eat them and more.")
    print("  * value hunting with no model edge is just as dead — you can't")
    print("    beat a price by agreeing with it.")
    print("  * the ONLY profitable run is the one backed by a model that")
    print("    actually predicts better than the market. That model is the")
    print("    whole job. Build & prove it HERE before any real dollar.\n")


def cmd_grade(args: argparse.Namespace) -> None:
    game = synth.make_games(1, seed=args.seed)[0]
    snap = game.snapshots[0]
    print(f"\nMarket: {game.sport} {game.game_id}  "
          f"({snap.minutes_left} min left, home lead {snap.lead:+})")
    print(f"  home @ {snap.home_price.decimal:.2f}   "
          f"away @ {snap.away_price.decimal:.2f}")
    print(f"  (hidden truth: P(home)={game.true_home_prob:.1%}, "
          f"winner={game.winner})\n")
    for name, rub in grader.RUBRICS.items():
        g = grader.grade_snapshot(snap, rub)
        print(f"  [{name:9}] grade {g.letter} ({g.score:.0f})  "
              f"bet {g.selection} @ {g.decimal:.2f}  stake {g.stake:.4f}u")
        for reason in g.reasons:
            print(f"             - {reason}")
        print()


def cmd_shop(args: argparse.Namespace) -> None:
    games = synth.make_multibook_games(args.games, seed=args.seed)
    bankroll = args.bankroll
    single = args.book
    print(f"\nSynthetic season: {len(games)} games priced across "
          f"{len(synth.BOOKS)} books (seed={args.seed}).")
    print(f"Comparing 'always bet at {single}' vs 'shop for the best line'.\n")

    # --- 1) Pure line-shopping benefit: same bets, different price -------
    print("1) Same favorite bets, single book vs best line "
          "(isolates shopping alone)")
    at_book = [lineshop.reprice_book(g, single) for g in games]
    at_best = [lineshop.reprice_best(g) for g in games]

    p_a = Portfolio(_tmp_db("fav_single"), bankroll)
    r_a = run(at_book, grader.FAVORITE, p_a, estimator=market_estimator)
    p_a.close()
    p_b = Portfolio(_tmp_db("fav_best"), bankroll)
    r_b = run(at_best, grader.FAVORITE, p_b, estimator=market_estimator)
    p_b.close()
    print(f"   {single:9}: ROI {r_a.roi:+.2%}, avg CLV {r_a.avg_clv:+.3f}, "
          f"{r_a.n_bets} bets")
    print(f"   best line: ROI {r_b.roi:+.2%}, avg CLV {r_b.avg_clv:+.3f}, "
          f"{r_b.n_bets} bets")
    print(f"   -> shopping recovers {(r_b.roi - r_a.roi) * 100:+.2f} pts of ROI "
          f"and {r_b.avg_clv - r_a.avg_clv:+.3f} CLV for free.")
    print("      (both still negative — shopping cuts the vig you pay, it does")
    print("       not make a -EV bet +EV. Only arbitrage does that.)\n")

    # --- 2) Arbitrage scan ----------------------------------------------
    print("2) Arbitrage scan (best price on EACH side sums under 100%)")
    arbs = [a for g in games
            if (a := lineshop.find_arbitrage(
                g.game_id, g.snapshots[0].book_quotes, total_stake=1.0))]
    if arbs:
        avg_margin = sum(a.profit_margin for a in arbs) / len(arbs)
        total_profit = sum(a.guaranteed_profit for a in arbs)
        print(f"   found {len(arbs)} arbs in {len(games)} games "
              f"({len(arbs) / len(games):.1%} hit rate)")
        print(f"   avg guaranteed margin {avg_margin:+.2%} per arb; "
              f"{total_profit:+.2f}u risk-free on 1u stakes total")
        ex = max(arbs, key=lambda a: a.profit_margin)
        print(f"   e.g. {ex.game_id}: home @ {ex.home_decimal:.2f} ({ex.home_book}) "
              f"+ away @ {ex.away_decimal:.2f} ({ex.away_book}) "
              f"-> {ex.profit_margin:+.2%} locked")
    else:
        print("   none this season (tighten books or add dispersion to see them)")

    print("\nTakeaway: always taking the best available price recovers a big")
    print("chunk of the vig on every bet (better CLV, better ROI) and is the")
    print("precondition for arbitrage — the only risk-free money here. It needs")
    print("no predictive model, just prices from several books and the discipline")
    print("to always take the best one. Pair it with a real edge and it's the")
    print("difference between a slow loss and a slow win.\n")


def _fav_backtest(matches, pricing: str):
    """Flat-stake the market favorite each match, priced at `pricing`
    ('best' = best line across books, else a book name). Returns a dict of
    n, wins, roi, win_rate, avg_clv, net — CLV vs de-vigged Pinnacle close."""
    from . import realdata
    n = wins = 0
    staked = net = 0.0
    clvs = []
    for m in matches:
        fav = realdata.favorite(m)
        if pricing == "best":
            price, _ = realdata.best_price(m, fav)
        else:
            odds = m.open_odds.get(pricing)
            if not odds:
                continue
            price = odds[fav]
        if price <= 1.0:
            continue
        won = m.result == fav
        net += (price - 1.0) if won else -1.0
        staked += 1.0
        n += 1
        wins += int(won)
        fair_close = realdata.pinnacle_fair_close(m)
        if fair_close:
            clvs.append(fair_close[fav] - 1.0 / price)
    return {
        "n": n, "wins": wins,
        "roi": net / staked if staked else 0.0,
        "win_rate": wins / n if n else 0.0,
        "avg_clv": sum(clvs) / len(clvs) if clvs else 0.0,
        "net": net,
    }


def cmd_realtest(args: argparse.Namespace) -> None:
    from . import realdata
    matches = []
    try:
        if args.csv:
            for path in args.csv:
                matches += realdata.load(path)
            src_desc = ", ".join(args.csv)
        else:
            seasons = args.seasons.split(",")
            for s in seasons:
                url = realdata.season_url(s.strip(), args.div)
                got = realdata.load(url)
                matches += got
                print(f"  loaded {len(got):4} matches  {args.div} {s.strip()}")
            src_desc = f"{args.div} seasons {args.seasons}"
    except Exception as e:  # noqa: BLE001
        print(f"\ncould not load real data: {e}\n")
        return
    if not matches:
        print("\nno matches loaded.\n")
        return

    print(f"\nREAL DATA: {len(matches)} matches ({src_desc}), "
          f"{len(realdata.BOOKS)} books, real closing lines & results.\n")

    # 1) Betting favorites for real + the line-shopping benefit
    single = _fav_backtest(matches, args.book)
    best = _fav_backtest(matches, "best")
    print("1) Flat-stake the market favorite every match (real results)")
    print(f"   at {args.book:12}: win {single['win_rate']:.1%}  "
          f"ROI {single['roi']:+.2%}  CLV {single['avg_clv']:+.3f}  "
          f"({single['n']} bets, net {single['net']:+.1f}u)")
    print(f"   shop best line : win {best['win_rate']:.1%}  "
          f"ROI {best['roi']:+.2%}  CLV {best['avg_clv']:+.3f}  "
          f"({best['n']} bets, net {best['net']:+.1f}u)")
    print(f"   -> line shopping is worth {(best['roi'] - single['roi']) * 100:+.2f} "
          f"pts of ROI and {best['avg_clv'] - single['avg_clv']:+.3f} CLV, real.\n")

    # 2) Favorite-longshot bias, measured for real
    fav_roi = single["roi"]
    dog_net = dog_stake = 0.0
    for m in matches:
        dog = max(realdata.OUTCOMES,
                  key=lambda oc: sum(o[oc] for o in m.open_odds.values()))
        odds = m.open_odds.get(args.book)
        if not odds:
            continue
        price = odds[dog]
        dog_net += (price - 1.0) if m.result == dog else -1.0
        dog_stake += 1.0
    dog_roi = dog_net / dog_stake if dog_stake else 0.0
    print("2) Favorite-longshot bias (flat 1u, real results)")
    print(f"   betting favorites: ROI {fav_roi:+.2%}")
    print(f"   betting longshots: ROI {dog_roi:+.2%}")
    print("   (both usually negative — the vig taxes every side. the gap is the "
          "bias.)\n")

    # 3) Arbitrage across the real books
    arbs = [(m, a) for m in matches if (a := realdata.find_arbitrage(m))]
    print("3) Arbitrage scan across the real books (3-way)")
    if arbs:
        print(f"   found {len(arbs)} in {len(matches)} matches "
              f"({len(arbs) / len(matches):.1%}); "
              f"avg margin {sum(a for _, a in arbs) / len(arbs):+.2%}")
    else:
        print(f"   none in {len(matches)} matches — expected: these books are "
              "sharp and this is pre-match, not a fast-moving live line.")
    print("\n  All real prices, real outcomes. No bets placed — read only.\n")


def cmd_paper(args: argparse.Namespace) -> None:
    from . import paperlog as pl
    data = pl.load()

    if args.resolve:
        try:
            newly = pl.resolve(data)
        except Exception as e:  # noqa: BLE001
            print(f"\ncould not resolve: {e}\n")
            newly = []
        if newly:
            pl.save(data)
            print(f"\nresolved {len(newly)} bet(s):")
            for b in newly:
                res = "YES" if b["outcome"] == 1 else "NO"
                mark = "" if b["side"] == "none" else \
                    (f"  P&L {b['pnl']:+.3f}" if b["pnl"] else "")
                print(f"  [{res:3}] {b['question'][:50]}{mark}")
        else:
            print("\nno newly-resolved markets (still open or not closed yet).")

    sc = pl.scorecard(data, edge_threshold=args.edge_threshold)
    print(f"\n=== PAPER-TRADING LEDGER ===")
    print(f"  forecasts logged : {sc['n_total']}")
    print(f"  open (waiting)   : {sc['n_open']}")
    print(f"  resolved         : {sc['n_resolved']}")

    print("\n  open positions:")
    for b in data["bets"]:
        if b["status"] != "open":
            continue
        tag = "NO BET" if b["side"] == "none" else f"BET {b['side'].upper()}"
        print(f"    [{tag:6}] model {b['model_prob']:.2f} vs mkt "
              f"{b['market_prob']:.2f} (edge {b['edge']:+.2f})  "
              f"closes {b['close_date']}  {b['question'][:40]}")

    if sc["n_resolved"] == 0:
        print("\n  Nothing resolved yet — no score to report. That's correct:")
        print("  we score only settled markets, strictly out-of-sample. Check")
        print("  back after the close dates above.\n")
        return

    print(f"\n  settled P&L      : {sc['net_pnl']:+.3f} u on {sc['staked']:.0f}u "
          f"staked  (ROI {sc['roi']:+.1%})")
    ev = sc["evaluation"]
    if ev:
        print(ev.format())
    if sc["n_resolved"] < 30:
        print(f"  NOTE: only {sc['n_resolved']} resolved — far too few to conclude")
        print("  anything. Skill/ROI here is mostly noise until N is in the")
        print("  dozens+. Keep logging and resolving before trusting the verdict.\n")


def cmd_forecast(args: argparse.Namespace) -> None:
    import math
    import random
    from . import forecast as fc

    rng = random.Random(args.seed)
    n = args.markets
    market_noise, skill_noise = 0.08, 0.05

    truth, market, outcome = [], [], []
    for _ in range(n):
        tp = min(0.95, max(0.05, rng.betavariate(1.1, 1.1)))
        mp = min(0.98, max(0.02, tp + rng.gauss(0, market_noise)))
        truth.append(tp)
        market.append(mp)
        outcome.append(1 if rng.random() < tp else 0)

    def logit(p): return math.log(p / (1 - p))
    def sig(x): return 1 / (1 + math.exp(-x))

    forecasters = {
        "market-follower": lambda i: min(0.99, max(0.01, market[i] + rng.gauss(0, 0.01))),
        "skilled (real edge)": lambda i: min(0.98, max(0.02, truth[i] + rng.gauss(0, skill_noise))),
        "random noise": lambda i: rng.random(),
        "overconfident": lambda i: sig(1.9 * logit(min(0.98, max(0.02, market[i])))),
    }

    print(f"\nForecaster scorecards on {n} synthetic markets (known ground truth).")
    print("Only a model that is genuinely MORE ACCURATE than the market price "
          "makes money.\n")
    for name, fn in forecasters.items():
        recs = [fc.ForecastRecord(market_id=str(i), forecast_prob=fn(i),
                                  market_prob=market[i], outcome=outcome[i])
                for i in range(n)]
        ev = fc.evaluate(recs, edge_threshold=args.edge_threshold, fee=args.fee)
        print(f"=== {name} ==={ev.format()}")
        # compact calibration read: do high-confidence buckets come true?
        hi = [b for b in ev.calibration if b.lo >= 0.7]
        if hi:
            mf = sum(b.mean_forecast * b.n for b in hi) / sum(b.n for b in hi)
            er = sum(b.empirical_rate * b.n for b in hi) / sum(b.n for b in hi)
            print(f"  calibration@70%+: said {mf:.0%}, happened {er:.0%}  "
                  f"({'well calibrated' if abs(mf - er) < 0.06 else 'MISCALIBRATED'})\n")

    print("Takeaway: 'skilled' wins because its Brier beats the market's (positive")
    print("skill) AND it's calibrated. The follower matches the market -> ~0 edge.")
    print("Noise and overconfidence can look busy but lose. Your LLM/ML model is")
    print("just another row here — it only counts if its skill score and ROI are")
    print("positive out-of-sample. That's the bar. Nothing else is 'a model that")
    print("works'.\n")


def cmd_kalshi(args: argparse.Namespace) -> None:
    from . import kalshi
    try:
        markets = kalshi.fetch_markets(series=args.series,
                                       min_liquidity=args.min_liquidity,
                                       max_pages=args.pages)
    except Exception as e:  # noqa: BLE001
        print(f"\ncould not reach Kalshi: {e}\n")
        return
    if not markets:
        print("\nno liquid priced markets found. Try a --series ticker "
              "(e.g. KXFED, KXPRES) — the default feed is mostly dead combos.\n")
        return
    markets.sort(key=lambda m: -m.vol24h)
    print(f"\n{len(markets)} live Kalshi markets"
          f"{f' in {args.series}' if args.series else ''} "
          "(by 24h volume). Prices are implied probabilities.\n")
    for m in markets[:args.limit]:
        mid = m.mid
        sp = m.spread
        print(f"  {(m.mid or 0):.2f}  {m.title[:52]}  {m.subtitle[:20]}")
        print(f"        bid/ask {m.yes_bid}/{m.yes_ask}  spread "
              f"{f'{sp*100:.1f}c' if sp is not None else '?'}  "
              f"vol24h {m.vol24h:.0f}  liq ${m.liquidity:.0f}")
    print("\n  Real prices, read only. Feed a model's estimate + these prices +")
    print("  outcomes into `forecast` to check for real edge.\n")


def cmd_pm(args: argparse.Namespace) -> None:
    from . import polymarket
    try:
        markets = polymarket.fetch_markets(limit=args.limit)
        event = polymarket.fetch_top_event()
    except Exception as e:  # noqa: BLE001
        print(f"\ncould not reach Polymarket: {e}\n")
        return
    print(f"\nLIVE Polymarket data — {len(markets)} active markets "
          "(public API, read only).\n")

    # 1) The drag reality: spread vs a sportsbook's vig
    spread, n = polymarket.median_spread(markets)
    print("1) Cost to trade (the drag)")
    if spread is not None:
        print(f"   median bid/ask spread on {n} liquid markets: "
              f"{spread * 100:.2f}c")
        print(f"   for contrast, a sportsbook's vig is ~4.5%. Far lower drag "
              "here — and no one limits you for winning.\n")
    else:
        print("   (no liquid markets found right now)\n")

    # 2) The 'safe bet' trap, live
    traps = polymarket.safe_bet_traps(markets, threshold=args.safe_threshold)
    print(f"2) 'Safe bet' spots priced above {args.safe_threshold:.0%} (live)")
    if traps:
        for t in traps[:6]:
            print(f"   {t['side']:3} @ {t['price']:.3f}  risk ${t['risk_to_win_1']}"
                  f" to win $1  (needs {t['required_winrate']:.1%} just to break "
                  f"even)  — {t['question'][:44]}")
        print("   On a ~zero-fee exchange these are roughly BREAK-EVEN, not")
        print("   profit — the payoff is already in the price. That's exactly")
        print("   'Case A' of `cli compound`, where the median roll still")
        print("   shrank to ~$43. Better than a sportsbook, still not a plan.\n")
    else:
        print("   none that liquid right now.\n")

    # 3) Multi-outcome arbitrage — with the liquidity honesty check
    print("3) Multi-outcome coherence / arbitrage (liquidity-aware)")
    arb = polymarket.event_arbitrage(event)
    print(f"   event: {arb['title'][:50]}  ({arb['n_legs']} outcomes)")
    print(f"   naive sum of all Yes asks : {arb['naive_yes_sum']}  "
          f"(a coherent market sums to ~1.0; wild values = stale/thin legs)")
    print(f"   {arb['n_illiquid_legs']} of {arb['n_legs']} legs are illiquid "
          f"mirages (no real size to fill)")
    print(f"   liquid legs only ({arb['n_liquid_legs']}): "
          f"Yes-sum {arb['liquid_yes_sum']}  <- where you can actually trade, "
          f"it's efficient (no arb)")
    print("   Lesson: a real arb needs real liquidity on EVERY leg. Screen")
    print("   prices on thin outcomes are not fills. This is the #1 way naive")
    print("   prediction-market 'arb' scanners fool themselves.\n")

    print("Bottom line: better arena than a sportsbook (tiny spread, no bans,")
    print("real API) — but the same law holds. Safe bets are break-even at best,")
    print("compounding can't create an edge, and apparent free money is usually")
    print("illiquid. A REAL edge here = forecasting/arb you can prove beats the")
    print("settle price. That's the thing worth building.\n")


def cmd_compound(args: argparse.Namespace) -> None:
    from . import bankroll, oddsmath
    decimal = (oddsmath.american_to_decimal(args.american) if args.american
               else args.decimal)
    implied = 1.0 / decimal
    print(f"\nThe '$100 compounds itself on favorites' plan")
    print(f"Favorite price: decimal {decimal:.3f} "
          f"(needs {implied:.1%} just to break even)")
    print(f"Staking {args.fraction:.0%} of the bankroll each bet — bets scale up "
          f"as the roll grows.\n")

    print("A) BEST CASE — pretend the book takes ZERO vig (true rate = the price)")
    best = bankroll.simulate(start=args.bankroll, decimal=decimal,
                             true_prob=implied, fraction=args.fraction,
                             bets=args.bets, runs=args.runs, seed=args.seed)
    print(best.format())

    print("B) REALISTIC — you pay the vig, so true rate is ~1.5 pts below the price")
    real = bankroll.simulate(start=args.bankroll, decimal=decimal,
                             true_prob=None, vig_drag=0.015,
                             fraction=args.fraction, bets=args.bets,
                             runs=args.runs, seed=args.seed)
    print(real.format())

    print("The punchline: even with ZERO vig (case A), the MEDIAN bankroll still")
    print("shrinks — log-growth is negative because heavy-favorite variance, when")
    print("you compound it, eats you alive. The average looks fine only because a")
    print("few lucky runs drag it up; you are almost never in those runs. Betting")
    print("a bigger slice as you grow makes the swing worse, not safer.")
    print("Compounding multiplies a REAL edge. It can't create one — and applied")
    print("to a -EV bet it just gets you to zero with more drama.\n")


def cmd_sports(args: argparse.Namespace) -> None:
    from . import oddsapi
    try:
        sports = oddsapi.list_sports(args.key)
    except Exception as e:  # noqa: BLE001 - surface the reason plainly
        print(f"\ncould not list sports: {e}\n")
        return
    active = [s for s in sports if s.get("active")]
    print(f"\n{len(active)} active sports/leagues (key -> title):\n")
    for s in active:
        print(f"  {s['key']:32} {s['title']}")
    print("\nUse one as: python -m betting_sim.cli live --sport <key>\n")


def cmd_live(args: argparse.Namespace) -> None:
    from . import lineshop, oddsapi
    try:
        games = oddsapi.fetch_games(args.sport, regions=args.regions,
                                    api_key=args.key)
    except Exception as e:  # noqa: BLE001
        print(f"\ncould not fetch odds: {e}\n")
        return
    if not games:
        print(f"\nno live h2h markets for '{args.sport}' right now.\n")
        return

    print(f"\n{len(games)} live games for {args.sport} — best line across books:\n")
    arbs = []
    for snap in games:
        h, a = snap.home_price, snap.away_price
        print(f"  {a.selection} @ {a.decimal:.2f} ({a.book})  vs  "
              f"{h.selection} @ {h.decimal:.2f} ({h.book})")
        arb = lineshop.find_arbitrage(snap.game_id, snap.book_quotes)
        if arb:
            arbs.append((snap, arb))

    if arbs:
        print(f"\n  *** {len(arbs)} ARBITRAGE opportunity(ies) — free money ***")
        for snap, arb in arbs:
            print(f"    {snap.away_price.selection} @ {arb.away_decimal:.2f} "
                  f"({arb.away_book}) + {snap.home_price.selection} @ "
                  f"{arb.home_decimal:.2f} ({arb.home_book}) -> "
                  f"{arb.profit_margin:+.2%} guaranteed "
                  f"(stake {arb.stake_home:.2f}/{arb.stake_away:.2f} per 1u)")
    else:
        print("\n  no arbitrage right now (normal — they're rare and fleeting).")
    print("\n  These are REAL prices. Still no bets placed — read only.\n")


def main() -> None:
    ap = argparse.ArgumentParser(prog="betting_sim",
                                 description="Paper-trading sim. No real money.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="run the headline strategy comparison")
    d.add_argument("--games", type=int, default=2000)
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--bankroll", type=float, default=100.0)
    d.add_argument("--min-grade", default="C", choices=list("ABCDF"))
    d.set_defaults(func=cmd_demo)

    g = sub.add_parser("grade", help="grade a single example market")
    g.add_argument("--seed", type=int, default=7)
    g.set_defaults(func=cmd_grade)

    s = sub.add_parser("shop", help="line shopping, soft-book value & arbitrage")
    s.add_argument("--games", type=int, default=6000)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--bankroll", type=float, default=100.0)
    s.add_argument("--book", default="fanduel",
                   help="the single book to compare against best-line shopping")
    s.set_defaults(func=cmd_shop)

    pa = sub.add_parser("paper",
                        help="paper-trading ledger: log forecasts, resolve, score")
    pa.add_argument("--resolve", action="store_true",
                    help="check open bets against Polymarket and settle closed ones")
    pa.add_argument("--edge-threshold", type=float, default=0.05)
    pa.set_defaults(func=cmd_paper)

    fo = sub.add_parser("forecast",
                        help="score forecasting models: skill vs market, calibration, ROI")
    fo.add_argument("--markets", type=int, default=3000)
    fo.add_argument("--seed", type=int, default=7)
    fo.add_argument("--edge-threshold", type=float, default=0.05,
                    help="min |forecast-market| gap to place a bet")
    fo.add_argument("--fee", type=float, default=0.0,
                    help="per-trade cost as fraction of $1 notional")
    fo.set_defaults(func=cmd_forecast)

    ks = sub.add_parser("kalshi", help="live Kalshi markets (no key); use --series")
    ks.add_argument("--series", default=None,
                    help="series ticker for liquid markets, e.g. KXFED, KXPRES")
    ks.add_argument("--min-liquidity", type=float, default=1000.0)
    ks.add_argument("--pages", type=int, default=5)
    ks.add_argument("--limit", type=int, default=15)
    ks.set_defaults(func=cmd_kalshi)

    pm = sub.add_parser("pm",
                        help="live Polymarket data: spread, safe-bet traps, arbs (no key)")
    pm.add_argument("--limit", type=int, default=100)
    pm.add_argument("--safe-threshold", type=float, default=0.95,
                    help="flag outcomes priced above this as 'safe bet' spots")
    pm.set_defaults(func=cmd_pm)

    cp = sub.add_parser("compound",
                        help="Monte-Carlo the '$100 grows itself on favorites' plan")
    cp.add_argument("--bankroll", type=float, default=100.0)
    cp.add_argument("--american", type=float, default=-800,
                    help="favorite price in American odds (e.g. -800)")
    cp.add_argument("--decimal", type=float, default=1.125,
                    help="favorite price in decimal (used if --american is 0)")
    cp.add_argument("--fraction", type=float, default=0.25,
                    help="share of bankroll staked each bet")
    cp.add_argument("--bets", type=int, default=200)
    cp.add_argument("--runs", type=int, default=20000)
    cp.add_argument("--seed", type=int, default=7)
    cp.set_defaults(func=cmd_compound)

    rt = sub.add_parser("realtest",
                        help="backtest on REAL free historical odds (no key)")
    rt.add_argument("--seasons", default="2324,2223,2122,2021",
                    help="comma-separated season codes, e.g. 2324,2223")
    rt.add_argument("--div", default="E0",
                    help="league: E0=EPL, SP1=La Liga, D1=Bundesliga, I1=Serie A")
    rt.add_argument("--csv", nargs="*",
                    help="local CSV path(s) instead of downloading")
    rt.add_argument("--book", default="Bet365",
                    help="single book to compare against best-line shopping")
    rt.set_defaults(func=cmd_realtest)

    sp = sub.add_parser("sports", help="list real sport keys (needs ODDS_API_KEY)")
    sp.add_argument("--key", default=None, help="Odds API key (or set ODDS_API_KEY)")
    sp.set_defaults(func=cmd_sports)

    lv = sub.add_parser("live", help="real current lines + arbs (needs ODDS_API_KEY)")
    lv.add_argument("--sport", default="upcoming",
                    help="sport key, e.g. basketball_nba (see `sports`)")
    lv.add_argument("--regions", default="us", help="us, uk, eu, au")
    lv.add_argument("--key", default=None, help="Odds API key (or set ODDS_API_KEY)")
    lv.set_defaults(func=cmd_live)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
