"""Scan EVERY market on the exchanges and rank what's worth forecasting.

'Investigate all bets' has a cost catch: pulling the markets is free (API), but
forecasting each one with an LLM + web search is not. There are thousands of
active markets; you cannot afford to research them all. So this module does the
free part — pull everything, normalize it, tag a category — and then RANKS them
by how likely a forecast is to be worth the spend:

  * skip the near-certain (price < 0.05 or > 0.95): no room for edge, the payoff
    is already in the price (the 'safe bet' trap).
  * prefer liquid markets: an edge you can't bet size on isn't worth finding.
  * prefer sooner resolution: faster feedback = faster learning.
  * (a real deployment would also down-rank pure-chance markets — exact crypto
    prices, coin-flips — where no research can help; we expose the category so a
    caller can filter those.)

The forecaster then works down the ranked list until the budget runs out. That's
how you 'investigate all bets' affordably: triage first, spend where it counts.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

GAMMA = "https://gamma-api.polymarket.com/markets"

# rough category tags from keywords in the question (a real system would use the
# exchange's own tags; this keeps it dependency-free and transparent).
_CATEGORY_KEYWORDS = {
    "sports": ["win the", "world cup", "nba", "nfl", "score", "goalscorer",
               "champion", "vs", "match", "golden", "playoff", "super bowl"],
    "politics": ["president", "election", "senate", "prime minister", "governor",
                 "nominee", "impeach", "cabinet", "head of state", "parliament"],
    "geopolitics": ["invade", "war", "ceasefire", "military", "troops", "strike",
                    "nuclear", "annex", "sanction"],
    "econ": ["fed", "rate", "gdp", "inflation", "cpi", "recession",
             "unemployment", "interest"],
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "solana", "price of",
               "market cap", "all-time high"],
}


@dataclass
class Candidate:
    platform: str
    market_id: str
    slug: str
    question: str
    category: str
    yes_price: float
    liquidity: float
    vol24h: float
    close_date: str
    priority: float


def _category(question: str) -> str:
    q = question.lower()
    for cat, kws in _CATEGORY_KEYWORDS.items():
        if any(k in q for k in kws):
            return cat
    return "other"


def _days_to_close(close_date: str) -> float | None:
    try:
        dt = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 86400.0
    except (ValueError, AttributeError):
        return None


def priority(yes_price: float, liquidity: float, days: float | None) -> float:
    """0 = don't bother, higher = forecast this first."""
    if yes_price is None or yes_price < 0.05 or yes_price > 0.95:
        return 0.0                       # no room for edge
    if liquidity < 5000:
        return 0.0                       # can't bet size on it
    import math
    liq_score = min(1.0, math.log10(liquidity) / 6.0)     # ~1.0 at $1M
    # sooner is better, but not already-closing; sweet spot days ~ 3..120
    if days is None or days <= 0:
        time_score = 0.2
    elif days < 3:
        time_score = 0.6
    elif days <= 120:
        time_score = 1.0
    else:
        time_score = max(0.2, 120.0 / days)
    return round(liq_score * time_score, 4)


def _fetch_page(offset: int, limit: int) -> list[dict]:
    params = {"closed": "false", "active": "true", "limit": limit,
              "offset": offset, "order": "volume24hr", "ascending": "false"}
    url = f"{GAMMA}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "betting_sim"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8", errors="replace"))


def scan(max_markets: int = 1500, page: int = 200) -> list[Candidate]:
    """Pull active Polymarket markets and return them ranked by priority."""
    out: list[Candidate] = []
    offset = 0
    while offset < max_markets:
        rows = _fetch_page(offset, page)
        if not rows:
            break
        for m in rows:
            try:
                outs = json.loads(m.get("outcomes", "[]"))
                prices = json.loads(m.get("outcomePrices", "[]"))
            except (ValueError, json.JSONDecodeError):
                continue
            if outs != ["Yes", "No"] or not prices:
                continue
            yes = float(prices[0])
            liq = float(m.get("liquidityNum") or 0)
            close = (m.get("endDate") or "")[:19]
            q = m.get("question", "")
            out.append(Candidate(
                platform="polymarket", market_id=str(m.get("id")),
                slug=m.get("slug", ""), question=q, category=_category(q),
                yes_price=yes, liquidity=liq,
                vol24h=float(m.get("volume24hr") or 0), close_date=close,
                priority=priority(yes, liq, _days_to_close(m.get("endDate") or "")),
            ))
        offset += page
    out.sort(key=lambda c: -c.priority)
    return out


def worth_forecasting(candidates: list[Candidate], budget: int) -> list[Candidate]:
    """The top `budget` markets with non-zero priority — where to spend forecasts."""
    return [c for c in candidates if c.priority > 0][:budget]
