"""Synthetic games so the whole sim runs with zero API keys or installs.

The point of synthetic data is not to cheat — it's to build a world whose
truth we KNOW, so we can check whether a strategy's grades actually track EV.
Real odds data can't do that: you never know the true probability, only the
outcome.

Two things are modelled honestly:

1. A book vig (overround) on every market. The house headwind.

2. The FAVORITE-LONGSHOT BIAS — a real, decades-documented market effect:
   heavy favorites are priced SHORTER than their true chance (you overpay to
   back them) and longshots LONGER (they're underpriced). This is exactly why
   'grind the safe favorite' bleeds money: you're systematically buying the
   most overpriced side of the market.

The closing line is modelled as sharper (less vig, less noise) than the
in-game decision line, so closing-line value means something.

`Game.true_home_prob` is the ground truth. Only synth mode has it; a grader's
estimator must EARN its edge, it doesn't get to peek (except the demo's
'skilled model', which stands in for a real predictive model you'd build).
"""
from __future__ import annotations

import random

from . import oddsmath
from .models import BookQuote, Game, MarketPrice, Snapshot
from .portfolio import now

SPORTS = ["nba", "nfl", "soccer", "mlb", "nhl"]

# (book, typical vig). Pinnacle is the sharp, low-margin book; the rest are
# softer retail books with fatter margins and noisier prices — which is exactly
# where line-shopping value and the occasional arb come from.
BOOKS = [
    ("pinnacle", 0.022),
    ("draftkings", 0.045),
    ("fanduel", 0.050),
    ("betmgm", 0.055),
    ("caesars", 0.048),
    ("bovada", 0.060),
]


def _price_from_prob(prob: float, vig: float) -> float:
    """Turn a fair probability into a decimal price with vig added on.

    We inflate each side's implied probability by `vig`, so a two-way market
    sums to ~1 + vig (the overround / hold). Returns the decimal price for
    `prob`. Realistic overrounds: a sharp book ~2-2.5%, retail books ~4.5-6%.
    """
    implied = prob * (1.0 + vig)
    implied = min(0.999, max(0.001, implied))
    return 1.0 / implied


def _favorite_longshot_shade(true_prob: float, strength: float) -> float:
    """Shift the market's perceived prob away from true toward the extremes.

    Favorites (true_prob > .5) get shaded UP (overpriced to back); longshots
    get shaded DOWN. `strength` scales the effect. Result stays in (0,1).
    """
    centered = true_prob - 0.5
    shaded = true_prob + strength * centered * (1.0 - abs(centered) * 2.0)
    return min(0.98, max(0.02, shaded))


def make_game(rng: random.Random, *, vig: float, fl_bias: float,
              close_vig: float) -> Game:
    """One synthetic in-game situation with a known true probability."""
    sport = rng.choice(SPORTS)
    game_id = f"{sport}-{rng.randint(100000, 999999)}"

    # A late-game situation: usually one side is well ahead, so true win probs
    # skew toward the extremes (this is the 'bet late' regime the strategy
    # targets). Beta(0.6,0.6) is U-shaped -> lots of near-locked games.
    true_home_prob = rng.betavariate(0.6, 0.6)
    true_home_prob = min(0.97, max(0.03, true_home_prob))

    # In-game decision line: shaded by favorite-longshot bias + noise + vig.
    market_prob = _favorite_longshot_shade(true_home_prob, fl_bias)
    market_prob = min(0.98, max(0.02,
                                market_prob + rng.gauss(0, 0.02)))
    home_dec = _price_from_prob(market_prob, vig)
    away_dec = _price_from_prob(1.0 - market_prob, vig)

    # Closing line: sharper — near-true prob, thinner vig, less noise.
    close_prob = min(0.98, max(0.02, true_home_prob + rng.gauss(0, 0.01)))
    close_home = _price_from_prob(close_prob, close_vig)
    close_away = _price_from_prob(1.0 - close_prob, close_vig)

    # Flavor state consistent with the probability (bigger lead = safer).
    lead = round((true_home_prob - 0.5) * 40.0)   # ~ +/- 20
    minutes_left = round(rng.uniform(1.0, 8.0), 1)

    snap = Snapshot(
        game_id=game_id, sport=sport, taken_at=now(),
        home_price=MarketPrice("home", round(home_dec, 3)),
        away_price=MarketPrice("away", round(away_dec, 3)),
        minutes_left=minutes_left, lead=lead,
    )

    winner = "home" if rng.random() < true_home_prob else "away"

    return Game(
        game_id=game_id, sport=sport, snapshots=[snap], winner=winner,
        close_home_decimal=round(close_home, 3),
        close_away_decimal=round(close_away, 3),
        true_home_prob=round(true_home_prob, 4),
    )


def make_games(n: int, *, seed: int = 7, vig: float = 0.05,
               fl_bias: float = 0.20, close_vig: float = 0.02) -> list[Game]:
    """A season's worth of synthetic late-game spots."""
    rng = random.Random(seed)
    return [make_game(rng, vig=vig, fl_bias=fl_bias, close_vig=close_vig)
            for _ in range(n)]


def make_multibook_game(rng: random.Random, *, fl_bias: float,
                        close_vig: float, prob_noise: float = 0.007) -> Game:
    """Like make_game, but every book prices the game independently.

    Each book adds its own noise around the (bias-shaded) market probability and
    its own vig. The game's default snapshot is set to the BEST line across
    books, and all book quotes are retained so lineshop can compare/arb them.
    """
    sport = rng.choice(SPORTS)
    game_id = f"{sport}-{rng.randint(100000, 999999)}"

    true_home_prob = rng.betavariate(0.6, 0.6)
    true_home_prob = min(0.97, max(0.03, true_home_prob))
    market_prob = _favorite_longshot_shade(true_home_prob, fl_bias)

    quotes: list[BookQuote] = []
    for name, vig in BOOKS:
        bp = min(0.98, max(0.02, market_prob + rng.gauss(0, prob_noise)))
        quotes.append(BookQuote(
            book=name,
            home_decimal=round(_price_from_prob(bp, vig), 3),
            away_decimal=round(_price_from_prob(1.0 - bp, vig), 3),
        ))

    best_home = max(q.home_decimal for q in quotes)
    best_away = max(q.away_decimal for q in quotes)
    home_book = next(q.book for q in quotes if q.home_decimal == best_home)
    away_book = next(q.book for q in quotes if q.away_decimal == best_away)

    close_prob = min(0.98, max(0.02, true_home_prob + rng.gauss(0, 0.01)))
    lead = round((true_home_prob - 0.5) * 40.0)
    minutes_left = round(rng.uniform(1.0, 8.0), 1)

    snap = Snapshot(
        game_id=game_id, sport=sport, taken_at=now(),
        home_price=MarketPrice("home", best_home, home_book),
        away_price=MarketPrice("away", best_away, away_book),
        minutes_left=minutes_left, lead=lead, book_quotes=quotes,
    )
    winner = "home" if rng.random() < true_home_prob else "away"
    return Game(
        game_id=game_id, sport=sport, snapshots=[snap], winner=winner,
        close_home_decimal=round(_price_from_prob(close_prob, close_vig), 3),
        close_away_decimal=round(_price_from_prob(1.0 - close_prob, close_vig), 3),
        true_home_prob=round(true_home_prob, 4),
    )


def make_multibook_games(n: int, *, seed: int = 7, fl_bias: float = 0.20,
                         close_vig: float = 0.02) -> list[Game]:
    """A season of games, each priced across several sportsbooks."""
    rng = random.Random(seed)
    return [make_multibook_game(rng, fl_bias=fl_bias, close_vig=close_vig)
            for _ in range(n)]


def skilled_model(noise: float = 0.03, seed: int = 11):
    """A stand-in for a REAL predictive model you'd build.

    It estimates true prob better than the biased market (small noise around
    ground truth). This is the only thing that produces a genuine edge — and
    in the real world you have to build it and prove it. Returns an estimator
    closure keyed off the game's hidden true prob, resolved via a registry the
    backtest populates.
    """
    rng = random.Random(seed)
    registry: dict[str, float] = {}

    def estimator(snap: Snapshot) -> float:
        true_p = registry.get(snap.game_id)
        if true_p is None:
            # No ground truth available -> no basis for an edge, stay neutral
            # by echoing the de-vigged market (grades everything F, correctly).
            ih = oddsmath.decimal_to_implied(snap.home_price.decimal)
            ia = oddsmath.decimal_to_implied(snap.away_price.decimal)
            return oddsmath.devig([ih, ia])[0]
        est = true_p + rng.gauss(0, noise)
        return min(0.98, max(0.02, est))

    estimator.registry = registry  # type: ignore[attr-defined]
    return estimator
