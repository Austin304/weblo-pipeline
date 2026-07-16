"""Correctness tests for the money math. If these pass, the numbers are real.

Run: python -m betting_sim.tests.test_oddsmath   (or with pytest)
"""
from __future__ import annotations

import math

from betting_sim import oddsmath as m


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=0, abs_tol=tol)


def test_american_decimal_roundtrip():
    assert approx(m.american_to_decimal(150), 2.5)
    assert approx(m.american_to_decimal(-200), 1.5)
    assert m.decimal_to_american(2.5) == 150
    assert m.decimal_to_american(1.5) == -200
    # even money
    assert approx(m.american_to_decimal(100), 2.0)
    assert m.decimal_to_american(2.0) == 100


def test_implied_probability():
    assert approx(m.decimal_to_implied(2.0), 0.5)
    assert approx(m.american_to_implied(-200), 200 / 300)
    assert approx(m.american_to_implied(150), 100 / 250)


def test_devig_sums_to_one_and_removes_hold():
    ih = m.american_to_implied(-200)   # 0.6667
    ia = m.american_to_implied(150)    # 0.4
    fair = m.devig([ih, ia])
    assert approx(sum(fair), 1.0)
    # vig is the overround above 1.0
    assert approx(m.market_vig([ih, ia]), (ih + ia) - 1.0)


def test_ev_zero_at_fair_price():
    # At a fair (de-vigged) price, EV per unit is exactly 0.
    p = 0.6
    fair_decimal = 1.0 / p
    assert approx(m.ev_per_unit(p, fair_decimal), 0.0)


def test_ev_negative_for_safe_favorite():
    # The whole thesis: a 95% shot at -2000 (decimal 1.05) is negative EV.
    ev = m.ev_per_unit(0.95, 1.05)
    assert ev < 0
    assert approx(ev, 0.95 * 1.05 - 1.0)


def test_edge_matches_prob_minus_implied():
    assert approx(m.edge(0.55, 2.0), 0.05)
    assert approx(m.edge(0.40, 2.0), -0.10)


def test_kelly_zero_when_no_edge():
    # No edge -> stake nothing.
    assert m.kelly_fraction(0.5, 2.0) == 0.0
    # Negative edge -> clamped to 0, never bet into it.
    assert m.kelly_fraction(0.4, 2.0) == 0.0
    # Positive edge -> positive fraction.
    assert m.kelly_fraction(0.6, 2.0) > 0


def test_kelly_known_value():
    # p=0.6, decimal=2.0 (b=1): f = (0.6*2 - 1)/1 = 0.2
    assert approx(m.kelly_fraction(0.6, 2.0), 0.2)


def test_clv_positive_when_you_beat_the_close():
    # Bet at 2.60 (implied .3846), closes at 2.40 (implied .4167): +CLV.
    assert m.clv(2.60, 2.40) > 0
    # Bet worse than close -> negative CLV.
    assert m.clv(2.40, 2.60) < 0


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
