"""Live Polymarket data via the public Gamma API — no key, read only.

Polymarket is a prediction-market EXCHANGE: you trade Yes/No shares that pay $1
if the outcome happens, priced 0..1 (a 0.60 share = the market's 60% estimate).
That structure changes the money math vs a sportsbook:

  * Cost to trade is the SPREAD (often ~0.1c on liquid markets), not a ~4.5%
    vig. Much lower drag. (Polymarket may also charge fees; we measure the
    directly-observable spread and treat it as the floor on cost.)
  * No one limits you for winning — it's an exchange.

But the same laws still bite:

  * A "safe" share at 0.98 (risk 98c to win 2c) is the -800 favorite again.
    On a ~zero-fee exchange it's roughly BREAK-EVEN rather than a guaranteed
    loss — which is exactly 'Case A' of `cli compound`, and we already saw that
    the median bankroll still shrinks when you compound it. Better, not safe.
  * Displayed prices on illiquid legs are a MIRAGE. A 60-team field whose long
    shots all show a 0.001 ask looks like a free arb; there's no size there, so
    you can't actually fill it. Real arb needs real liquidity, not a screen
    price. This module flags that instead of pretending.

Nothing here places a trade.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from statistics import median

BASE = "https://gamma-api.polymarket.com"


@dataclass
class PMMarket:
    question: str
    group_title: str
    yes_price: float | None      # mid, from outcomePrices[0]
    bid: float | None            # best bid for the Yes share
    ask: float | None            # best ask for the Yes share
    spread: float | None         # ask - bid (the round-trip cost floor)
    liquidity: float             # $ resting in the book
    vol24h: float                # $ traded last 24h


def _get(path: str, params: dict) -> list | dict:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "betting_sim"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _market(m: dict) -> PMMarket:
    yes = None
    try:
        yes = float(json.loads(m["outcomePrices"])[0])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return PMMarket(
        question=m.get("question", ""),
        group_title=m.get("groupItemTitle") or m.get("question", ""),
        yes_price=yes, bid=_f(m.get("bestBid")), ask=_f(m.get("bestAsk")),
        spread=_f(m.get("spread")),
        liquidity=_f(m.get("liquidityNum")) or 0.0,
        vol24h=_f(m.get("volume24hr")) or 0.0,
    )


def fetch_markets(limit: int = 100) -> list[PMMarket]:
    """Most-traded active binary markets right now (by 24h volume)."""
    rows = _get("/markets", {"closed": "false", "limit": limit,
                             "order": "volume24hr", "ascending": "false"})
    return [_market(m) for m in rows]


def fetch_top_event() -> dict:
    """The highest-24h-volume active event (a group of related markets)."""
    rows = _get("/events", {"closed": "false", "limit": 1,
                            "order": "volume24hr", "ascending": "false"})
    return rows[0] if rows else {}


# --- analysis ------------------------------------------------------------

def median_spread(markets: list[PMMarket], min_liquidity: float = 10000.0):
    """Median bid/ask spread among genuinely liquid markets (cost floor)."""
    spreads = [m.spread for m in markets
               if m.spread is not None and m.liquidity >= min_liquidity]
    return median(spreads) if spreads else None, len(spreads)


def safe_bet_traps(markets: list[PMMarket], threshold: float = 0.95,
                   min_liquidity: float = 10000.0) -> list[dict]:
    """Near-certain shares — the 'safe bet' spots — with their real risk/reward.

    Buying a Yes share at price p risks p to win (1-p). On an efficient, fee-
    free market that is break-even (EV 0), NOT a profit: the payoff is already
    in the price. We surface the ones people are tempted by and show the math.
    """
    out = []
    for m in markets:
        p = m.ask if m.ask is not None else m.yes_price
        if p is None or m.liquidity < min_liquidity:
            continue
        # consider whichever side is the 'safe' (near-1) side
        for side, price in (("Yes", p), ("No", None if p is None else 1.0 - p)):
            if price is None or price < threshold or price >= 0.999:
                continue
            risk = price
            reward = 1.0 - price
            out.append({
                "question": m.question, "side": side, "price": price,
                "risk_to_win_1": round(risk / reward, 1),  # risk $X to win $1
                "required_winrate": price,                  # break-even prob
                "liquidity": m.liquidity,
            })
    out.sort(key=lambda d: -d["price"])
    return out


def event_arbitrage(event: dict, min_leg_liquidity: float = 5000.0) -> dict:
    """For a mutually-exclusive event group, do the Yes prices cohere to ~1?

    Sums the best-ask price of each leg. Under 1.0 *looks* like a buy-the-field
    arb — but only counts if every cheap leg has real liquidity. We report the
    naive sum AND the liquid-only picture, so illiquid mirages are visible.
    """
    legs = []
    for m in event.get("markets", []):
        mk = _market(m)
        ask = mk.ask if mk.ask is not None else mk.yes_price
        if ask is None:
            continue
        legs.append((mk.group_title, ask, mk.liquidity))

    naive_sum = sum(a for _, a, _ in legs)
    liquid_legs = [(t, a, liq) for t, a, liq in legs if liq >= min_leg_liquidity]
    illiquid = len(legs) - len(liquid_legs)
    return {
        "title": event.get("title", ""),
        "n_legs": len(legs),
        "naive_yes_sum": round(naive_sum, 4),
        "n_liquid_legs": len(liquid_legs),
        "n_illiquid_legs": illiquid,
        "liquid_yes_sum": round(sum(a for _, a, _ in liquid_legs), 4),
    }
