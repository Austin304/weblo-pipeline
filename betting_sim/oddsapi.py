"""Optional real-data path: pull live odds from The Odds API.

This is NOT needed to run the sim — the demo uses synthetic data. It's here so
that when you want to grade real markets, there's a clean adapter that maps a
provider's payload into our Snapshot model.

The Odds API (the-odds-api.com) has a free tier for current odds; historical
odds (needed for a real backtest) are a paid tier. Set the key in the env:

    export ODDS_API_KEY=...    # then: from betting_sim.oddsapi import fetch_current

We keep this dependency-light (uses `requests`, already in requirements.txt)
and never place bets — it only reads prices. There is no order-placement code
anywhere in this package, by design.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .models import MarketPrice, Snapshot

BASE = "https://api.the-odds-api.com/v4"


def fetch_current(sport_key: str, *, regions: str = "us",
                  api_key: str | None = None) -> list[Snapshot]:
    """Fetch current head-to-head (moneyline) markets for a sport.

    Returns one Snapshot per game using the first bookmaker's h2h prices.
    Raises RuntimeError if no key is configured. Import of `requests` is lazy
    so the rest of the package runs with pure stdlib.
    """
    key = api_key or os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError(
            "no ODDS_API_KEY set. The demo does not need one — it uses "
            "synthetic data. Set the key only for the real-data path.")

    import requests  # lazy: keep stdlib-only for the core sim

    url = f"{BASE}/sports/{sport_key}/odds"
    resp = requests.get(url, params={
        "apiKey": key, "regions": regions, "markets": "h2h",
        "oddsFormat": "decimal",
    }, timeout=30)
    resp.raise_for_status()

    snapshots: list[Snapshot] = []
    for game in resp.json():
        books = game.get("bookmakers") or []
        if not books:
            continue
        h2h = next((m for m in books[0].get("markets", [])
                    if m.get("key") == "h2h"), None)
        if not h2h or len(h2h.get("outcomes", [])) < 2:
            continue
        home_name = game.get("home_team")
        outcomes = {o["name"]: o["price"] for o in h2h["outcomes"]}
        away_name = next((n for n in outcomes if n != home_name), None)
        if home_name not in outcomes or away_name is None:
            continue
        snapshots.append(Snapshot(
            game_id=game.get("id", ""),
            sport=sport_key,
            taken_at=datetime.now(timezone.utc),
            home_price=MarketPrice(home_name, float(outcomes[home_name])),
            away_price=MarketPrice(away_name, float(outcomes[away_name])),
        ))
    return snapshots
