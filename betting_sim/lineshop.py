"""Line shopping, sharp consensus, and arbitrage across multiple sportsbooks.

This is where a retail bettor's most reliable, real edges actually live:

1. BEST LINE — the same bet pays differently at different books. Always taking
   the highest decimal price on the side you want is free EV on every bet. It
   raises your payout without changing your risk, and it directly improves CLV.

2. SHARP CONSENSUS — one book is noisy; the median de-vigged probability across
   many books is a much better estimate of the true chance. If a single book's
   price implies a LOWER probability than the consensus (i.e. it's offering a
   longer price than the crowd thinks fair), betting that book is +EV — without
   you needing your own predictive model. You're just betting the soft book.

3. ARBITRAGE — when the best price on EACH side sums to under 100% implied, you
   can stake both sides and lock in profit regardless of outcome. Rare, small,
   and books limit you for it, but it's real and risk-free when it appears.

None of this needs a crystal ball. It needs prices from several books and the
discipline to always take the best one. That's why line shopping is the first
thing to build, not the last.
"""
from __future__ import annotations

import dataclasses
from statistics import median

from . import oddsmath
from .models import ArbOpportunity, BookQuote, Game, MarketPrice, Snapshot


def best_line(game_id: str, sport: str, quotes: list[BookQuote],
              taken_at=None) -> Snapshot:
    """Collapse many books into the single best price available on each side.

    Home takes the highest home decimal across books; away the highest away
    decimal — possibly from two DIFFERENT books. Provenance is kept on each
    MarketPrice.book so you know where to actually place it.
    """
    if not quotes:
        raise ValueError("no quotes to shop")
    best_home = max(quotes, key=lambda q: q.home_decimal)
    best_away = max(quotes, key=lambda q: q.away_decimal)
    return Snapshot(
        game_id=game_id, sport=sport, taken_at=taken_at,
        home_price=MarketPrice("home", best_home.home_decimal, best_home.book),
        away_price=MarketPrice("away", best_away.away_decimal, best_away.book),
        book_quotes=list(quotes),
    )


def consensus_prob(quotes: list[BookQuote]) -> float:
    """Median de-vigged P(home) across books — a robust 'sharp' truth estimate.

    Each book is de-vigged on its own, then we take the median so one soft or
    stale book can't drag the estimate. This is the number to price value
    against: a price that beats consensus is a price worth taking.
    """
    fairs = []
    for q in quotes:
        ih = oddsmath.decimal_to_implied(q.home_decimal)
        ia = oddsmath.decimal_to_implied(q.away_decimal)
        fairs.append(oddsmath.devig([ih, ia])[0])
    return median(fairs)


def consensus_estimator(quotes_by_game: dict[str, list[BookQuote]]):
    """Grader estimator that scores a bet against the multi-book consensus.

    Reads the consensus for the snapshot's game, so the grader's 'edge' becomes
    'how much this price beats what the rest of the market thinks fair'. Falls
    back to the snapshot's own de-vig if the game isn't in the map.
    """
    def estimator(snap: Snapshot) -> float:
        quotes = quotes_by_game.get(snap.game_id) or snap.book_quotes
        if quotes:
            return consensus_prob(quotes)
        ih = oddsmath.decimal_to_implied(snap.home_price.decimal)
        ia = oddsmath.decimal_to_implied(snap.away_price.decimal)
        return oddsmath.devig([ih, ia])[0]
    return estimator


def find_arbitrage(game_id: str, quotes: list[BookQuote],
                   total_stake: float = 1.0) -> ArbOpportunity | None:
    """Detect a two-way arb from the best price on each side. None if no arb.

    implied_sum = 1/best_home_dec + 1/best_away_dec. If it's < 1, staking each
    side in proportion to its implied prob equalises the payout and guarantees
    profit = total_stake * (1/implied_sum - 1), whichever team wins.
    """
    if not quotes:
        return None
    bh = max(quotes, key=lambda q: q.home_decimal)
    ba = max(quotes, key=lambda q: q.away_decimal)
    dh, da = bh.home_decimal, ba.away_decimal
    implied_sum = 1.0 / dh + 1.0 / da
    if implied_sum >= 1.0:
        return None  # vig not beaten — no free money here

    stake_home = total_stake * (1.0 / dh) / implied_sum
    stake_away = total_stake * (1.0 / da) / implied_sum
    guaranteed_return = total_stake / implied_sum  # same on either outcome
    return ArbOpportunity(
        game_id=game_id,
        home_book=bh.book, home_decimal=dh,
        away_book=ba.book, away_decimal=da,
        implied_sum=implied_sum,
        profit_margin=(1.0 / implied_sum) - 1.0,
        stake_home=round(stake_home, 4),
        stake_away=round(stake_away, 4),
        guaranteed_profit=round(guaranteed_return - total_stake, 4),
    )


# --- repricing helpers: view the SAME game at different books ------------

def apply_snapshot(game: Game, snap: Snapshot) -> Game:
    """Return a copy of `game` whose only snapshot is `snap`."""
    return dataclasses.replace(game, snapshots=[snap])


def reprice_best(game: Game) -> Game:
    """Copy of `game` priced at the best line across its books."""
    src = game.snapshots[0]
    shopped = best_line(game.game_id, game.sport, src.book_quotes, src.taken_at)
    shopped = dataclasses.replace(shopped, minutes_left=src.minutes_left,
                                  lead=src.lead)
    return apply_snapshot(game, shopped)


def reprice_book(game: Game, book: str) -> Game:
    """Copy of `game` priced at a single named book (models NOT shopping)."""
    src = game.snapshots[0]
    q = next((q for q in src.book_quotes if q.book == book), None)
    if q is None:
        return game
    snap = dataclasses.replace(
        src,
        home_price=MarketPrice("home", q.home_decimal, q.book),
        away_price=MarketPrice("away", q.away_decimal, q.book),
    )
    return apply_snapshot(game, snap)
