"""Tests for the paper-trading ledger (offline; no network).

Run: python -m betting_sim.tests.test_paperlog
"""
from __future__ import annotations

import math

from betting_sim import paperlog as pl


def approx(a, b, tol=1e-9):
    return math.isclose(a, b, rel_tol=0, abs_tol=tol)


def _fresh():
    return {"created": "2026-01-01", "bets": []}


def _add(data, mid, model, market):
    return pl.add_forecast(
        data, platform="polymarket", market_id=mid, slug=f"s-{mid}",
        question=f"Q {mid}?", close_date="2026-12-31",
        model_prob=model, market_prob=market, rationale="test",
        edge_threshold=0.05)


def test_bet_side_from_edge():
    data = _fresh()
    assert _add(data, "1", 0.70, 0.60)["side"] == "yes"   # +0.10 edge
    assert _add(data, "2", 0.40, 0.60)["side"] == "no"    # -0.20 edge
    assert _add(data, "3", 0.61, 0.60)["side"] == "none"  # +0.01, below thresh
    assert data["bets"][2]["stake"] == 0.0


def test_no_double_log_of_open_market():
    data = _fresh()
    _add(data, "9", 0.7, 0.6)
    _add(data, "9", 0.8, 0.6)
    assert len([b for b in data["bets"] if b["market_id"] == "9"]) == 1


def test_pnl_directions():
    # bet YES, resolves YES -> win (1 - price)
    assert approx(pl._pnl("yes", 0.60, 1), 0.40)
    # bet YES, resolves NO -> lose the price
    assert approx(pl._pnl("yes", 0.60, 0), -0.60)
    # bet NO, resolves NO -> win the price
    assert approx(pl._pnl("no", 0.60, 0), 0.60)
    # bet NO, resolves YES -> lose (1 - price)
    assert approx(pl._pnl("no", 0.60, 1), -0.40)
    # no bet -> zero either way
    assert pl._pnl("none", 0.6, 1) == 0.0


def test_scorecard_scores_only_resolved():
    data = _fresh()
    _add(data, "1", 0.40, 0.60)   # bet NO
    _add(data, "2", 0.70, 0.60)   # bet YES
    # resolve them by hand (simulating a settlement)
    data["bets"][0].update(status="resolved", outcome=0,
                           pnl=pl._pnl("no", 0.60, 0))   # NO wins: +0.60
    data["bets"][1].update(status="resolved", outcome=0,
                           pnl=pl._pnl("yes", 0.60, 0))  # YES loses: -0.60
    sc = pl.scorecard(data)
    assert sc["n_resolved"] == 2
    assert approx(sc["net_pnl"], 0.0)     # +0.60 - 0.60
    assert sc["evaluation"] is not None


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
