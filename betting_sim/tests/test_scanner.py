"""Tests for the market scanner's triage logic (offline).

Run: python -m betting_sim.tests.test_scanner
"""
from __future__ import annotations

from betting_sim import scanner
from betting_sim.scanner import Candidate


def test_category_keywords():
    assert scanner._category("Will Spain win the World Cup?") == "sports"
    assert scanner._category("Will the US invade Iran before 2027?") == "geopolitics"
    assert scanner._category("Will the Fed cut interest rates?") == "econ"
    assert scanner._category("Will Bitcoin hit $100k?") == "crypto"
    assert scanner._category("Who will be the next president?") == "politics"
    assert scanner._category("Will it rain frogs?") == "other"


def test_priority_gates_near_certain_and_illiquid():
    # near-certain -> no room for edge -> priority 0
    assert scanner.priority(0.98, 1_000_000, 30) == 0.0
    assert scanner.priority(0.02, 1_000_000, 30) == 0.0
    # illiquid -> can't bet size -> priority 0
    assert scanner.priority(0.50, 1000, 30) == 0.0
    # liquid, mid-price, near-term -> positive
    assert scanner.priority(0.50, 1_000_000, 30) > 0.0


def test_priority_prefers_sooner_resolution():
    soon = scanner.priority(0.5, 500_000, 30)
    far = scanner.priority(0.5, 500_000, 3000)
    assert soon > far


def test_worth_forecasting_filters_and_caps():
    cands = [
        Candidate("pm", "1", "s", "q", "sports", 0.5, 1e6, 0, "", priority=0.9),
        Candidate("pm", "2", "s", "q", "other", 0.99, 1e6, 0, "", priority=0.0),
        Candidate("pm", "3", "s", "q", "econ", 0.4, 1e6, 0, "", priority=0.7),
    ]
    worth = scanner.worth_forecasting(cands, budget=5)
    assert len(worth) == 2                      # the zero-priority one is dropped
    assert all(c.priority > 0 for c in worth)
    assert len(scanner.worth_forecasting(cands, budget=1)) == 1  # budget cap


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
