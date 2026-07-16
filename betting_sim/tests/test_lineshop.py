"""Tests for line shopping, consensus, and arbitrage math.

Run: python -m betting_sim.tests.test_lineshop
"""
from __future__ import annotations

import math

from betting_sim import lineshop
from betting_sim.models import BookQuote


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=0, abs_tol=tol)


def test_best_line_picks_max_price_per_side():
    quotes = [
        BookQuote("a", home_decimal=1.90, away_decimal=2.00),
        BookQuote("b", home_decimal=2.05, away_decimal=1.85),
        BookQuote("c", home_decimal=1.95, away_decimal=2.10),
    ]
    snap = lineshop.best_line("g1", "nba", quotes)
    assert snap.home_price.decimal == 2.05
    assert snap.home_price.book == "b"
    assert snap.away_price.decimal == 2.10
    assert snap.away_price.book == "c"


def test_consensus_is_median_devigged_prob():
    # Three books, symmetric -> consensus home prob ~0.5.
    quotes = [BookQuote(f"b{i}", 2.0, 2.0) for i in range(3)]
    assert approx(lineshop.consensus_prob(quotes), 0.5)


def test_no_arbitrage_when_market_holds_vig():
    # A single vigged book can never arb against itself.
    quotes = [BookQuote("a", 1.90, 1.90)]  # implies 0.526+0.526 = 1.052
    assert lineshop.find_arbitrage("g", quotes) is None


def test_arbitrage_detected_and_profit_is_guaranteed():
    # Book A loves away, book B loves home -> best of each side can arb.
    # home best 2.10 (imp .476) + away best 2.10 (imp .476) = .952 < 1.
    quotes = [
        BookQuote("A", home_decimal=2.10, away_decimal=1.80),
        BookQuote("B", home_decimal=1.80, away_decimal=2.10),
    ]
    arb = lineshop.find_arbitrage("g", quotes, total_stake=1.0)
    assert arb is not None
    assert arb.home_decimal == 2.10 and arb.away_decimal == 2.10
    assert arb.implied_sum < 1.0
    # Payout is identical whichever side wins == the guarantee.
    ret_if_home = arb.stake_home * arb.home_decimal
    ret_if_away = arb.stake_away * arb.away_decimal
    assert approx(ret_if_home, ret_if_away, tol=1e-6)
    # And that return exceeds the 1.0 total staked.
    assert ret_if_home > 1.0
    assert approx(arb.guaranteed_profit, ret_if_home - 1.0, tol=1e-4)


def test_best_line_ev_beats_single_book():
    # Shopping never lowers the price you get, so EV can only improve.
    quotes = [
        BookQuote("a", 1.90, 2.00),
        BookQuote("b", 2.05, 1.85),
    ]
    snap = lineshop.best_line("g", "nba", quotes)
    # best home price 2.05 >= every book's home price
    assert all(snap.home_price.decimal >= q.home_decimal for q in quotes)


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
