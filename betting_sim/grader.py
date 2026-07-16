"""The grading system — the part you actually want to tune.

A grader looks at a market snapshot, forms a probability estimate for each
side, and grades the resulting bet. The grade blends:

  * EDGE  (est_true_prob - implied_prob)  -- the only thing that makes money
  * SAFETY (raw win probability)          -- how 'locked in' the outcome is
  * VIG penalty                           -- the house's headwind on this market

Two strategies ship so you can see the difference for yourself:

  "favorite"  -- your original idea: grade almost entirely on SAFETY, using the
                 market's own number as the probability. By construction this
                 has NO edge (est_true_prob == implied), so every grade is a
                 negative-EV bet dressed up as an A. This is the control group.

  "value"     -- grade on EDGE, computed from an INDEPENDENT probability
                 estimate. This can only score well when your estimate
                 disagrees with the market in your favor. With the market
                 estimator it grades everything F, which is correct: if your
                 model == the market, you have nothing.

The probability estimator is injected, so you can drop in your own model later
(backtest.py wires one in for synthetic games).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import oddsmath
from .models import Grade, Snapshot

# A prob estimator maps a snapshot to P(home wins). value in (0, 1).
ProbEstimator = Callable[[Snapshot], float]


def market_estimator(snap: Snapshot) -> float:
    """The naive estimate: trust the de-vigged market completely.

    Using this means est_true_prob == implied fair prob, so edge is ~0 and EV
    is negative by exactly the vig. It models the bettor who believes the line
    is the truth — which is why the 'favorite' strategy loses.
    """
    imp_home = oddsmath.decimal_to_implied(snap.home_price.decimal)
    imp_away = oddsmath.decimal_to_implied(snap.away_price.decimal)
    fair_home, _ = oddsmath.devig([imp_home, imp_away])
    return fair_home


@dataclass
class Rubric:
    """Weights and gates that define a strategy. Tune these."""
    name: str
    w_edge: float          # weight on edge in the score
    w_safety: float        # weight on raw win probability
    w_vig: float           # penalty weight on market vig
    min_prob: float        # skip bets whose est win prob is below this
    max_prob: float        # skip bets so short the price can't cover a loss
    min_edge: float        # skip bets with less edge than this (value gate)
    require_positive_ev: bool  # if True, never grade a -EV bet above F
    min_decimal: float = 1.01  # skip prices too short to be worth risking capital
    staking: str = "kelly"     # "kelly" (edge-sized) or "flat" (fixed units)
    flat_units: float = 1.0    # units per bet when staking == "flat"

    def stake_units(self, decimal: float, true_prob: float) -> float:
        """Stake for one bet, in units.

        "flat"  : a fixed stake every time — what the naive 'bet $1 on the safe
                  side' bettor actually does. It fires on every qualified bet,
                  so the vig + favorite-longshot bleed shows up in the P&L.
        "kelly" : quarter-Kelly, which sizes off edge and correctly refuses to
                  stake anything on a zero-edge bet (returns ~0).
        """
        if self.staking == "flat":
            return self.flat_units
        f = oddsmath.kelly_fraction(true_prob, decimal)
        return round(0.25 * f, 4)


# Two shipped rubrics. FAVORITE is the control; VALUE is the real target.
FAVORITE = Rubric(
    name="favorite",
    w_edge=0.0, w_safety=1.0, w_vig=0.0,
    min_prob=0.80, max_prob=0.995, min_edge=-1.0,
    require_positive_ev=False,
    min_decimal=1.10,   # risk at most ~10 to win 1; no one grinds shorter than this
    staking="flat", flat_units=1.0,
)
VALUE = Rubric(
    name="value",
    w_edge=1.0, w_safety=0.15, w_vig=0.5,
    min_prob=0.02, max_prob=0.995, min_edge=0.02,
    require_positive_ev=True,
    staking="kelly",
)

RUBRICS = {r.name: r for r in (FAVORITE, VALUE)}


def _letter(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def _grade_side(snap: Snapshot, side: str, rubric: Rubric,
                estimator: ProbEstimator) -> Grade:
    price = snap.home_price if side == "home" else snap.away_price
    decimal = price.decimal

    p_home = estimator(snap)
    true_prob = p_home if side == "home" else 1.0 - p_home

    imp_home = oddsmath.decimal_to_implied(snap.home_price.decimal)
    imp_away = oddsmath.decimal_to_implied(snap.away_price.decimal)
    vig = oddsmath.market_vig([imp_home, imp_away])

    ev = oddsmath.ev_per_unit(true_prob, decimal)
    edge = oddsmath.edge(true_prob, decimal)

    reasons: list[str] = []
    disqualified = False

    if true_prob < rubric.min_prob:
        reasons.append(f"win prob {true_prob:.1%} below floor {rubric.min_prob:.0%}")
        disqualified = True
    if true_prob > rubric.max_prob:
        reasons.append(f"win prob {true_prob:.1%} too short to cover a loss")
        disqualified = True
    if decimal < rubric.min_decimal:
        reasons.append(f"price {decimal:.2f} below floor {rubric.min_decimal:.2f} "
                       f"— too little to win vs the stake at risk")
        disqualified = True
    if edge < rubric.min_edge:
        reasons.append(f"edge {edge:+.1%} below required {rubric.min_edge:+.0%}")
        disqualified = True
    if rubric.require_positive_ev and ev <= 0:
        reasons.append(f"EV {ev:+.3f}/unit is not positive — market is ahead of you")
        disqualified = True

    # Score components, each roughly 0..100 before weighting.
    edge_pts = max(-100.0, min(100.0, edge * 1000.0))   # +10% edge -> +100
    safety_pts = true_prob * 100.0
    vig_pts = vig * 100.0                                # penalty

    raw = (rubric.w_edge * edge_pts
           + rubric.w_safety * safety_pts
           - rubric.w_vig * vig_pts)
    denom = (rubric.w_edge + rubric.w_safety) or 1.0
    score = raw / denom
    score = max(0.0, min(100.0, score))
    if disqualified:
        score = 0.0

    reasons.append(f"EV {ev:+.3f}/unit, edge {edge:+.1%}, vig {vig:.1%}, "
                   f"win prob {true_prob:.1%} @ decimal {decimal:.2f}")

    stake = 0.0 if disqualified else rubric.stake_units(decimal, true_prob)

    return Grade(
        letter=_letter(score),
        score=round(score, 1),
        selection=side,
        decimal=decimal,
        stake=stake,
        est_true_prob=round(true_prob, 4),
        est_edge=round(edge, 4),
        reasons=reasons,
    )


def grade_snapshot(snap: Snapshot, rubric: Rubric,
                   estimator: ProbEstimator = market_estimator) -> Grade:
    """Grade both sides of a market and return the better bet."""
    home = _grade_side(snap, "home", rubric, estimator)
    away = _grade_side(snap, "away", rubric, estimator)
    return home if home.score >= away.score else away
