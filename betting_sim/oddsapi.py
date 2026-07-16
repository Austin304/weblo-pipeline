"""Real odds via The Odds API — the live-data path (still no bet placement).

The demo runs on synthetic data; this is how you point the shopping/arb tools at
REAL current sportsbook prices. It only READS odds. There is no order-placement
code in this package, by design.

The Odds API (the-odds-api.com):
  * free tier: current odds across many books (enough for `cli live`)
  * historical odds (needed for a real backtest with outcomes) is a paid tier

Set your key once:
    export ODDS_API_KEY=xxxxxxxx

Then:
    python -m betting_sim.cli sports          # list sport keys
    python -m betting_sim.cli live --sport basketball_nba

We keep `requests` a lazy import so the rest of the package stays pure stdlib.
"""
from __future__ import annotations

import os

from . import lineshop
from .models import BookQuote, Snapshot

BASE = "https://api.the-odds-api.com/v4"


def _key(explicit: str | None) -> str:
    key = explicit or os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError(
            "no ODDS_API_KEY set. Get a free key at the-odds-api.com, then "
            "`export ODDS_API_KEY=...` (the demo does not need one).")
    return key


def list_sports(api_key: str | None = None) -> list[dict]:
    """Return the sports/leagues currently offered (key + human title)."""
    import requests  # lazy: keep stdlib-only for the core sim

    resp = requests.get(f"{BASE}/sports/",
                        params={"apiKey": _key(api_key)}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_games(sport_key: str, *, regions: str = "us",
                api_key: str | None = None) -> list[Snapshot]:
    """Fetch current moneyline (h2h) markets for a sport, ALL books per game.

    Each returned Snapshot carries every book's price in `book_quotes`, with its
    home/away price pre-set to the best line across books (so it's ready for
    lineshop / find_arbitrage). Returns [] if the sport has no live markets.
    """
    import requests  # lazy

    resp = requests.get(f"{BASE}/sports/{sport_key}/odds", params={
        "apiKey": _key(api_key), "regions": regions, "markets": "h2h",
        "oddsFormat": "decimal",
    }, timeout=30)
    resp.raise_for_status()

    snapshots: list[Snapshot] = []
    for game in resp.json():
        home = game.get("home_team")
        away = game.get("away_team")
        if not home or not away:
            continue

        quotes: list[BookQuote] = []
        for bk in game.get("bookmakers", []):
            h2h = next((m for m in bk.get("markets", [])
                        if m.get("key") == "h2h"), None)
            if not h2h:
                continue
            prices = {o["name"]: o["price"] for o in h2h.get("outcomes", [])}
            if home not in prices or away not in prices:
                continue
            quotes.append(BookQuote(
                book=bk.get("title") or bk.get("key", "?"),
                home_decimal=float(prices[home]),
                away_decimal=float(prices[away]),
            ))
        if not quotes:
            continue

        snap = lineshop.best_line(game.get("id", ""), sport_key, quotes)
        # relabel selections with real team names for readable output
        snap.home_price.selection = home
        snap.away_price.selection = away
        snapshots.append(snap)

    return snapshots
