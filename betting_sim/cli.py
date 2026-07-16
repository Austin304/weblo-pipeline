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

from . import grader, synth
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

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
