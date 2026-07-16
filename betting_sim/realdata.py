"""Real historical odds from football-data.co.uk — free, no key, no scraping.

Their CSVs give, per match: the final result plus DECIMAL odds from ~6 books
(Bet365, Betway, Interwetten, Pinnacle, William Hill, VC), both OPENING and
CLOSING, for a 3-way market (home / draw / away). That's real multi-book prices
+ real closing lines + real outcomes — everything a genuine backtest needs.

This is real data, so we finally get to check the strategy against reality
instead of a synthetic world. Two honest limits to keep in mind:

  * These are PRE-MATCH odds, not in-game/late odds. So this validates the
    market's behaviour (favorite pricing, line-shopping value, closing-line
    value) for real — but it can't replay the exact "bet the safe favorite in
    the 88th minute" spot, which needs in-game odds history (paid/rare).
  * Soccer is 3-way. All the math below de-vigs and shops across three
    outcomes; oddsmath.devig already handles n outcomes.

Nothing here places a bet. Read only.
"""
from __future__ import annotations

import csv
import io
import urllib.request
from dataclasses import dataclass
from statistics import median

from . import oddsmath

# (display name, CSV column prefix). Opening cols are prefix+H/D/A;
# closing cols are prefix+"C"+H/D/A (e.g. PSH open, PSCH close for Pinnacle).
BOOKS = [
    ("Bet365", "B365"),
    ("Betway", "BW"),
    ("Interwetten", "IW"),
    ("Pinnacle", "PS"),
    ("William Hill", "WH"),
    ("VC Bet", "VC"),
]
OUTCOMES = ("H", "D", "A")  # home, draw, away


@dataclass
class Match:
    date: str
    home: str
    away: str
    result: str                       # "H" | "D" | "A"
    open_odds: dict[str, dict[str, float]]   # book -> {H,D,A}
    close_odds: dict[str, dict[str, float]]  # book -> {H,D,A}


def _row_odds(row: dict, prefix: str) -> dict[str, float] | None:
    """Pull one book's H/D/A odds from a row, or None if any is missing."""
    out = {}
    for oc in OUTCOMES:
        raw = row.get(f"{prefix}{oc}", "")
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None
        if val <= 1.0:
            return None
        out[oc] = val
    return out


def parse_csv(text: str) -> list[Match]:
    matches: list[Match] = []
    for row in csv.DictReader(io.StringIO(text)):
        result = (row.get("FTR") or "").strip()
        if result not in OUTCOMES:
            continue
        open_odds, close_odds = {}, {}
        for name, pfx in BOOKS:
            o = _row_odds(row, pfx)
            if o:
                open_odds[name] = o
            c = _row_odds(row, pfx + "C")
            if c:
                close_odds[name] = c
        if not open_odds:
            continue
        matches.append(Match(
            date=(row.get("Date") or "").strip(),
            home=(row.get("HomeTeam") or "").strip(),
            away=(row.get("AwayTeam") or "").strip(),
            result=result, open_odds=open_odds, close_odds=close_odds,
        ))
    return matches


def load(source: str) -> list[Match]:
    """Load matches from a local path or an http(s) URL (stdlib only)."""
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=60) as r:  # noqa: S310
            text = r.read().decode("utf-8", errors="replace")
    else:
        with open(source, encoding="utf-8", errors="replace") as f:
            text = f.read()
    return parse_csv(text)


def season_url(season: str = "2324", div: str = "E0") -> str:
    """Build a football-data.co.uk CSV URL. div E0=EPL, SP1=La Liga, etc."""
    return f"https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"


# --- shopping / consensus / arb on the 3-way market ---------------------

def best_price(m: Match, outcome: str, *, closing: bool = False):
    """Highest available price for an outcome across books -> (price, book)."""
    src = m.close_odds if closing else m.open_odds
    best, book = 0.0, None
    for name, odds in src.items():
        if odds[outcome] > best:
            best, book = odds[outcome], name
    return best, book


def consensus_fair(m: Match, *, closing: bool = False) -> dict[str, float]:
    """Median de-vigged probability per outcome across books (sharp estimate)."""
    per = {oc: [] for oc in OUTCOMES}
    src = m.close_odds if closing else m.open_odds
    for odds in src.values():
        fair = oddsmath.devig([1.0 / odds[oc] for oc in OUTCOMES])
        for oc, p in zip(OUTCOMES, fair):
            per[oc].append(p)
    return {oc: median(v) for oc, v in per.items() if v}


def pinnacle_fair_close(m: Match) -> dict[str, float] | None:
    """De-vigged Pinnacle CLOSING probs — the sharpest fair line we have."""
    odds = m.close_odds.get("Pinnacle")
    if not odds:
        return None
    fair = oddsmath.devig([1.0 / odds[oc] for oc in OUTCOMES])
    return dict(zip(OUTCOMES, fair))


def favorite(m: Match) -> str:
    """The outcome the market makes shortest (by average opening price)."""
    avg = {}
    for oc in OUTCOMES:
        prices = [o[oc] for o in m.open_odds.values()]
        avg[oc] = sum(prices) / len(prices)
    return min(OUTCOMES, key=lambda oc: avg[oc])


def find_arbitrage(m: Match) -> float | None:
    """3-way arb margin if best price on each outcome sums under 100%, else None."""
    s = sum(1.0 / best_price(m, oc)[0] for oc in OUTCOMES)
    return (1.0 / s - 1.0) if s < 1.0 else None
