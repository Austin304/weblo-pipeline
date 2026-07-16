"""Tests for the forecast evaluation harness.

Run: python -m betting_sim.tests.test_forecast
"""
from __future__ import annotations

import math
import random

from betting_sim import forecast as fc


def approx(a, b, tol=1e-9):
    return math.isclose(a, b, rel_tol=0, abs_tol=tol)


def _rec(f, m, o):
    return fc.ForecastRecord(market_id="x", forecast_prob=f, market_prob=m, outcome=o)


def test_brier_zero_for_perfect_and_quarter_for_coinflip():
    perfect = [_rec(1.0, 0.5, 1), _rec(0.0, 0.5, 0)]
    assert approx(fc.brier(perfect, "forecast_prob"), 0.0)
    flip = [_rec(0.5, 0.5, 1), _rec(0.5, 0.5, 0)]
    assert approx(fc.brier(flip, "forecast_prob"), 0.25)


def test_skill_positive_when_model_beats_market():
    # Model nails it, market is off -> model Brier lower -> positive skill.
    recs = [_rec(0.9, 0.6, 1), _rec(0.1, 0.4, 0), _rec(0.9, 0.6, 1), _rec(0.1, 0.4, 0)]
    ev = fc.evaluate(recs, edge_threshold=0.05)
    assert ev.brier_forecast < ev.brier_market
    assert ev.skill_score > 0


def test_follower_places_no_bets_and_no_edge():
    # Forecast == market everywhere -> nothing clears the edge threshold.
    rng = random.Random(1)
    recs = [_rec(p := rng.random(), p, int(rng.random() < p)) for _ in range(200)]
    ev = fc.evaluate(recs, edge_threshold=0.05)
    assert ev.n_bets == 0
    assert approx(ev.roi, 0.0)
    assert approx(ev.skill_score, 0.0, tol=1e-9)


def test_bet_pnl_directions():
    # Model likes Yes (forecast>market), Yes happens -> profit = 1 - market.
    ev = fc.evaluate([_rec(0.9, 0.6, 1)], edge_threshold=0.05)
    assert ev.n_bets == 1 and approx(ev.net, 1.0 - 0.6)
    # Model likes No (forecast<market), No happens -> profit = market.
    ev2 = fc.evaluate([_rec(0.2, 0.6, 0)], edge_threshold=0.05)
    assert ev2.n_bets == 1 and approx(ev2.net, 0.6)
    # Model likes Yes but No happens -> lose the stake (= market price).
    ev3 = fc.evaluate([_rec(0.9, 0.6, 0)], edge_threshold=0.05)
    assert approx(ev3.net, -0.6)


def test_edge_selected_positive_roi_with_true_edge():
    # A forecaster that knows truth better than a noisy market should net +ROI.
    rng = random.Random(3)
    recs = []
    for _ in range(4000):
        tp = min(0.9, max(0.1, rng.random()))
        market = min(0.95, max(0.05, tp + rng.gauss(0, 0.10)))
        forecast = min(0.95, max(0.05, tp + rng.gauss(0, 0.03)))
        recs.append(_rec(forecast, market, int(rng.random() < tp)))
    ev = fc.evaluate(recs, edge_threshold=0.05)
    assert ev.skill_score > 0
    assert ev.roi > 0


def test_calibration_bins_track_reality():
    rng = random.Random(5)
    # Perfectly calibrated: outcome drawn at exactly the forecast prob.
    recs = [_rec(p := rng.random(), p, int(rng.random() < p)) for _ in range(8000)]
    bins = fc.calibration(recs, nbins=5)
    for b in bins:
        assert abs(b.mean_forecast - b.empirical_rate) < 0.06


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
