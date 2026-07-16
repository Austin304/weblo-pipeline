"""Run a strategy over a set of games and report whether it actually made money.

Flow per game:
  1. take the in-game decision snapshot
  2. grade both sides -> pick the bet the rubric likes
  3. if it clears the grade/stake gate, place a PAPER bet
  4. settle against the real outcome
  5. score CLV against the DE-VIGGED closing line
  6. record it in the portfolio

The output that matters is ROI and average CLV. A high win rate with negative
ROI/CLV is the 'safe favorite' trap made visible.
"""
from __future__ import annotations

import uuid

from . import oddsmath
from .grader import ProbEstimator, Rubric, grade_snapshot, market_estimator
from .models import Game, PlacedBet
from .portfolio import Portfolio, Report, now

# Minimum grade to actually fire a bet.
MIN_LETTER = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}


def _fair_close_decimal(game: Game, selection: str) -> float:
    """De-vig the closing market and return the fair decimal for `selection`."""
    ih = oddsmath.decimal_to_implied(game.close_home_decimal)
    ia = oddsmath.decimal_to_implied(game.close_away_decimal)
    fair_home, fair_away = oddsmath.devig([ih, ia])
    fair = fair_home if selection == "home" else fair_away
    return 1.0 / fair


def run(games: list[Game], rubric: Rubric, portfolio: Portfolio, *,
        estimator: ProbEstimator = market_estimator,
        min_grade: str = "C", stake_unit: float = 1.0) -> Report:
    """Backtest `rubric` over `games`. Returns a Report. stake_unit scales the
    Kelly fraction into currency units (1.0 == the '$1 test')."""
    run_id = uuid.uuid4().hex[:12]
    floor = MIN_LETTER[min_grade]

    for game in games:
        snap = game.snapshots[0]
        grade = grade_snapshot(snap, rubric, estimator)

        if MIN_LETTER[grade.letter] < floor or grade.stake <= 0:
            continue

        # Size: Kelly fraction (already in grade.stake) * bankroll unit. Floor
        # at a tiny non-zero stake so a graded bet always registers on the card.
        stake = max(0.01, round(grade.stake * stake_unit, 4))
        won = (game.winner == grade.selection)
        pnl = stake * (grade.decimal - 1.0) if won else -stake
        clv = oddsmath.clv(grade.decimal,
                           _fair_close_decimal(game, grade.selection))

        bet = PlacedBet(
            game_id=game.game_id, sport=game.sport, selection=grade.selection,
            decimal=grade.decimal, stake=stake, grade=grade.letter,
            placed_at=now(), est_true_prob=grade.est_true_prob,
            won=won, pnl=round(pnl, 4), clv=round(clv, 4),
        )
        portfolio.record(run_id, bet)

    return portfolio.report(run_id, rubric.name)
