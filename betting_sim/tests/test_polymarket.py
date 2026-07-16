"""Tests for the Polymarket analysis helpers (offline, dict fixtures).

Run: python -m betting_sim.tests.test_polymarket
"""
from __future__ import annotations

from betting_sim import polymarket


def _mkt(q, yes, bid, ask, liq, vol=0.0, group=None):
    return {
        "question": q, "groupItemTitle": group,
        "outcomePrices": f'["{yes}", "{1 - yes:.4f}"]',
        "bestBid": bid, "bestAsk": ask,
        "spread": None if bid is None or ask is None else round(ask - bid, 4),
        "liquidityNum": liq, "volume24hr": vol,
    }


def test_market_parse():
    m = polymarket._market(_mkt("Will X?", 0.60, 0.599, 0.601, 50000))
    assert m.question == "Will X?"
    assert abs(m.yes_price - 0.60) < 1e-9
    assert m.bid == 0.599 and m.ask == 0.601
    assert m.liquidity == 50000


def test_median_spread_only_counts_liquid():
    markets = [
        polymarket._market(_mkt("liquid", 0.5, 0.499, 0.501, 50000)),   # spread .002
        polymarket._market(_mkt("thin", 0.5, 0.40, 0.60, 100)),         # excluded
    ]
    spread, n = polymarket.median_spread(markets, min_liquidity=10000)
    assert n == 1
    assert abs(spread - 0.002) < 1e-9


def test_safe_bet_trap_flags_near_certain_side():
    # Yes at 0.02 -> No is the 0.98 'safe' side.
    m = polymarket._market(_mkt("Longshot?", 0.02, 0.019, 0.021, 50000))
    traps = polymarket.safe_bet_traps([m], threshold=0.95)
    assert any(t["side"] == "No" and t["price"] >= 0.95 for t in traps)
    trap = next(t for t in traps if t["side"] == "No")
    # risk 0.979 to win 0.021 -> ~46.6 to win 1
    assert trap["risk_to_win_1"] > 40


def test_event_arbitrage_liquidity_split():
    event = {"title": "Field", "markets": [
        _mkt("A", 0.50, 0.499, 0.501, 50000, group="A"),
        _mkt("B", 0.50, 0.499, 0.501, 50000, group="B"),
        _mkt("C", 0.001, 0.0, 0.001, 50, group="C"),   # illiquid mirage
    ]}
    arb = polymarket.event_arbitrage(event, min_leg_liquidity=5000)
    assert arb["n_legs"] == 3
    assert arb["n_liquid_legs"] == 2
    assert arb["n_illiquid_legs"] == 1
    # liquid-only sum ~ 0.501 + 0.501 = 1.002 (efficient two-way)
    assert abs(arb["liquid_yes_sum"] - 1.002) < 1e-6


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
