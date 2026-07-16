"""Plain data structures passed between the sim's stages.

Deliberately dumb containers. All the logic lives in oddsmath / grader /
portfolio; these just carry state so the pipeline reads top-to-bottom.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class MarketPrice:
    """One side's price at one moment, in decimal odds."""
    selection: str          # e.g. "home", "away", team name
    decimal: float


@dataclass
class Snapshot:
    """The two-way market for one game at one point in time.

    `minutes_left` and `lead` describe the in-game state so the grader can
    reason about how 'locked in' the outcome is. In pregame snapshots these
    can be None.
    """
    game_id: str
    sport: str
    taken_at: datetime
    home_price: MarketPrice
    away_price: MarketPrice
    minutes_left: Optional[float] = None   # game minutes remaining
    lead: Optional[float] = None           # home margin (negative = away ahead)


@dataclass
class Game:
    """A full game: the snapshots we saw plus how it actually ended.

    `winner` is "home" or "away". `close_home_decimal` / `close_away_decimal`
    are the last prices before kickoff-of-settlement — the closing line we
    grade CLV against. In synthetic mode these all come from synth.py; with
    real data they come from the odds API.
    """
    game_id: str
    sport: str
    snapshots: list[Snapshot]
    winner: str
    close_home_decimal: float
    close_away_decimal: float
    true_home_prob: Optional[float] = None  # only known in synthetic mode


@dataclass
class Grade:
    """A grader's verdict on a single candidate bet."""
    letter: str             # A / B / C / D / F
    score: float            # 0..100, higher = better bet by the strategy
    selection: str          # "home" or "away"
    decimal: float          # price we'd take
    stake: float            # units the sizing rule wants on it
    est_true_prob: float    # the probability estimate the grade used
    est_edge: float         # est_true_prob - implied  (the +EV signal)
    reasons: list[str] = field(default_factory=list)


@dataclass
class PlacedBet:
    """A paper bet, before and after settlement."""
    game_id: str
    sport: str
    selection: str
    decimal: float
    stake: float
    grade: str
    placed_at: datetime
    est_true_prob: float
    # filled in at settlement:
    won: Optional[bool] = None
    pnl: Optional[float] = None          # profit (win) or -stake (loss)
    clv: Optional[float] = None          # vs de-vigged closing line
