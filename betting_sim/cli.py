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
