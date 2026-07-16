"""Tests for the learning layer (offline).

Run: python -m betting_sim.tests.test_learn
"""
from __future__ import annotations

from betting_sim import learn


def _resolved(model, market, outcome, question="Will Spain win the World Cup?"):
    return {"market_id": "x", "model_prob": model, "market_prob": market,
            "outcome": outcome, "side": "yes" if model > market else "no",
            "stake": 1.0, "pnl": 0.0, "status": "resolved", "question": question}


def test_calibration_inactive_below_min_sample():
    data = {"bets": [_resolved(0.9, 0.6, 1) for _ in range(5)]}
    a, b, trust = learn.fit_calibration(data)
    assert (a, b, trust) == (1.0, 0.0, False)


def test_calibration_shrinks_overconfident_model():
    # Symmetric overconfidence: says 0.9 but happens 60%, says 0.1 but happens 40%.
    bets = []
    for _ in range(12):
        bets.append(_resolved(0.9, 0.5, 1))
    for _ in range(8):
        bets.append(_resolved(0.9, 0.5, 0))     # 0.9 -> 60% realized
    for _ in range(8):
        bets.append(_resolved(0.1, 0.5, 1))
    for _ in range(12):
        bets.append(_resolved(0.1, 0.5, 0))     # 0.1 -> 40% realized
    data = {"bets": bets}
    a, b, trust = learn.fit_calibration(data)
    assert trust is True
    corrected = learn.apply_correction(0.9, a, b)
    assert 0.5 < corrected < 0.9                 # pulled in toward reality


def test_by_category_groups_and_scores():
    data = {"bets": [
        _resolved(0.8, 0.6, 1, "Will France win the match?"),   # sports, correct
        _resolved(0.3, 0.6, 0, "Will the Fed cut rates?"),      # econ, correct
    ]}
    rep = learn.by_category(data)
    assert "sports" in rep and "econ" in rep
    assert rep["sports"]["n"] == 1 and rep["econ"]["n"] == 1
    assert all(not r["enough"] for r in rep.values())          # tiny samples


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
