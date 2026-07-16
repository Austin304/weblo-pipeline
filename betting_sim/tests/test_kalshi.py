"""Tests for the Kalshi adapter's parsing (offline, dict fixtures).

Run: python -m betting_sim.tests.test_kalshi
"""
from __future__ import annotations

from betting_sim import kalshi


def test_market_parses_dollar_string_fields():
    raw = {
        "ticker": "KXFED-25", "title": "Fed cuts in Jan?",
        "yes_sub_title": "Yes", "yes_bid_dollars": "0.6300",
        "yes_ask_dollars": "0.6500", "last_price_dollars": "0.6400",
        "volume_24h_fp": "12345.0", "liquidity_dollars": "50000.0",
        "status": "open", "close_time": "2025-01-29T00:00:00Z",
    }
    m = kalshi._market(raw)
    assert m.ticker == "KXFED-25"
    assert abs(m.yes_bid - 0.63) < 1e-9 and abs(m.yes_ask - 0.65) < 1e-9
    assert abs(m.mid - 0.64) < 1e-9
    assert abs(m.spread - 0.02) < 1e-9
    assert m.liquidity == 50000.0 and m.vol24h == 12345.0


def test_mid_falls_back_to_last_when_no_book():
    raw = {"ticker": "X", "last_price_dollars": "0.30",
           "yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.0000"}
    m = kalshi._market(raw)
    assert abs(m.mid - 0.30) < 1e-9


def test_missing_prices_are_none():
    m = kalshi._market({"ticker": "X"})
    assert m.yes_bid is None and m.yes_ask is None and m.last is None
    assert m.liquidity == 0.0


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
