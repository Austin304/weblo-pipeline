"""Live Kalshi market data via the public API — no key, read only.

Kalshi is a US-regulated (CFTC) prediction-market exchange, which makes it the
sensible place for a US builder to actually deploy a forecasting edge: low fees,
a real trading API, and — critically — it does not ban you for winning.

This reads market data only (no trading). Note the API's default market feed is
front-loaded with thousands of illiquid multi-leg sports combos; the liquid
markets (politics, econ, Fed, weather) live under specific SERIES tickers, so
pass `series` to get something tradable. Prices come as string dollar fields
('0.6300' == 63c == a 63% implied probability).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

BASE = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass
class KMarket:
    ticker: str
    title: str
    subtitle: str
    yes_bid: float | None
    yes_ask: float | None
    last: float | None
    vol24h: float
    liquidity: float
    status: str
    close_time: str

    @property
    def mid(self) -> float | None:
        if self.yes_bid is not None and self.yes_ask is not None and self.yes_ask > 0:
            return (self.yes_bid + self.yes_ask) / 2.0
        return self.last

    @property
    def spread(self) -> float | None:
        if self.yes_bid is not None and self.yes_ask is not None:
            return self.yes_ask - self.yes_bid
        return None


def _f(v) -> float | None:
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _market(m: dict) -> KMarket:
    return KMarket(
        ticker=m.get("ticker", ""),
        title=m.get("title", ""),
        subtitle=m.get("yes_sub_title") or "",
        yes_bid=_f(m.get("yes_bid_dollars")),
        yes_ask=_f(m.get("yes_ask_dollars")),
        last=_f(m.get("last_price_dollars")),
        vol24h=_f(m.get("volume_24h_fp")) or 0.0,
        liquidity=_f(m.get("liquidity_dollars")) or 0.0,
        status=m.get("status", ""),
        close_time=m.get("close_time", ""),
    )


def fetch_markets(*, series: str | None = None, limit: int = 1000,
                  max_pages: int = 5, min_liquidity: float = 0.0) -> list[KMarket]:
    """Fetch open markets, optionally within a series ticker, keeping only those
    with a usable price and at least `min_liquidity` of resting liquidity."""
    out: list[KMarket] = []
    cursor = None
    for _ in range(max_pages):
        params = {"limit": limit, "status": "open"}
        if series:
            params["series_ticker"] = series
        if cursor:
            params["cursor"] = cursor
        url = f"{BASE}/markets?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "betting_sim"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        rows = data.get("markets", [])
        for m in rows:
            km = _market(m)
            has_price = (km.last is not None and 0.0 < km.last < 1.0) or \
                        (km.yes_ask is not None and 0.0 < km.yes_ask < 1.0)
            if has_price and km.liquidity >= min_liquidity:
                out.append(km)
        cursor = data.get("cursor")
        if not cursor or not rows:
            break
    return out
