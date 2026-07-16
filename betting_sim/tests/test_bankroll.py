"""Tests for the compounding-bankroll math.

Run: python -m betting_sim.tests.test_bankroll
"""
from __future__ import annotations

import math

from betting_sim import bankroll


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=0, abs_tol=tol)


def test_breakeven_bet_still_has_negative_log_growth():
    # -800 favorite (decimal 1.125), true rate = implied (zero vig), staking 25%.
    # The average edge is 0, but compounding variance makes log-growth NEGATIVE.
    p = 1.0 / 1.125
    g = bankroll.log_growth_rate(p, 1.125, 0.25)
    assert g < 0
    assert approx(g, -0.0046, tol=5e-4)


def test_flat_no_bet_has_zero_growth():
    # Staking nothing never grows or shrinks.
    assert bankroll.log_growth_rate(0.9, 1.125, 0.0) == 0.0


def test_real_edge_gives_positive_log_growth_at_small_fraction():
    # If the favorite truly wins MORE than the price implies, a small stake
    # compounds upward.
    p = 1.0 / 1.125 + 0.03      # 3 points of real edge
    assert bankroll.log_growth_rate(p, 1.125, 0.05) > 0


def test_oversized_stake_can_ruin_even_with_edge():
    # Even with an edge, betting too large a fraction drives log-growth negative
    # (over-betting Kelly). Here a huge fraction on a heavy favorite.
    p = 1.0 / 1.125 + 0.02
    assert bankroll.log_growth_rate(p, 1.125, 0.9) < 0


def test_simulate_reports_negative_median_drift_for_breakeven():
    p = 1.0 / 1.125
    stats = bankroll.simulate(start=100.0, decimal=1.125, true_prob=p,
                              fraction=0.25, bets=200, runs=3000, seed=1)
    assert approx(stats.ev_per_bet, 0.0, tol=1e-9)   # break-even average
    assert stats.log_growth < 0
    assert stats.median_end < stats.start            # typical run loses


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
