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

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
