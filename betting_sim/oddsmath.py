"""Odds conversions and the money math the whole simulator rests on.

Everything here is pure functions over floats/ints — no I/O, no state — so it
is trivially testable (see tests/test_oddsmath.py). If any of this is wrong,
every downstream number lies, so this is the file to trust the least until the
tests pass.

Vocabulary
----------
american   : the +150 / -200 style US price.
decimal    : the 2.50 style price. profit-on-win = stake * (decimal - 1).
implied    : the probability baked into a price, INCLUDING the book's vig.
             it is deliberately > the true chance; that gap is the house edge.
fair       : implied probability with the vig removed (de-vigged). our best
             read of what the book actually thinks will happen.
edge       : your_true_prob - implied_prob. positive edge == +EV. this is the
             only thing that makes money. "safe favorite" is NOT edge.
"""
from __future__ import annotations


# --- american <-> decimal ------------------------------------------------

def american_to_decimal(american: float) -> float:
    """+150 -> 2.50, -200 -> 1.50. american of 0 is undefined."""
    if american == 0:
        raise ValueError("american odds of 0 are undefined")
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def decimal_to_american(decimal: float) -> float:
    """2.50 -> +150, 1.50 -> -200. decimal must be > 1."""
    if decimal <= 1.0:
        raise ValueError(f"decimal odds must be > 1, got {decimal}")
    if decimal >= 2.0:
        return round((decimal - 1.0) * 100.0)
    return round(-100.0 / (decimal - 1.0))


# --- implied probability -------------------------------------------------

def decimal_to_implied(decimal: float) -> float:
    """Probability baked into a decimal price (includes vig). 2.50 -> 0.40."""
    if decimal <= 1.0:
        raise ValueError(f"decimal odds must be > 1, got {decimal}")
    return 1.0 / decimal


def american_to_implied(american: float) -> float:
    return decimal_to_implied(american_to_decimal(american))


# --- de-vigging a two-way (or n-way) market ------------------------------

def devig(implied_probs: list[float]) -> list[float]:
    """Strip the vig from a market by normalising implied probs to sum to 1.

    This is the proportional / multiplicative method: fair_i = imp_i / sum.
    For a two-way market (e.g. moneyline home vs away) that is the standard
    quick de-vig. It assumes the vig is spread proportionally across outcomes,
    which is close enough for grading and keeps us honest about what the book
    really thinks.

    Returns fair probabilities that sum to 1.0.
    """
    total = sum(implied_probs)
    if total <= 0:
        raise ValueError("implied probabilities must be positive")
    return [p / total for p in implied_probs]


def market_vig(implied_probs: list[float]) -> float:
    """The book's hold on a market: how far implied probs sum above 1.0.

    A two-way market summing to 1.05 has a 5% overround (the '5% vig'). This
    is the money the book skims regardless of outcome — the headwind every
    bet fights before it can be +EV.
    """
    return sum(implied_probs) - 1.0


# --- expected value & sizing --------------------------------------------

def ev_per_unit(true_prob: float, decimal: float) -> float:
    """Expected profit per 1 unit staked, given YOUR true probability estimate.

    EV = p * (decimal - 1) - (1 - p)  ==  p * decimal - 1

    Positive only when true_prob > implied_prob (i.e. you have edge). This is
    the single number that decides whether a bet is worth making. A 95%-safe
    favorite at -2000 (decimal 1.05) has EV = 0.95 * 1.05 - 1 = -0.0025 per
    unit: you lose a quarter-cent per dollar every time you fire it.
    """
    return true_prob * decimal - 1.0


def edge(true_prob: float, decimal: float) -> float:
    """true_prob minus the price's implied prob. The raw +EV signal."""
    return true_prob - decimal_to_implied(decimal)


def kelly_fraction(true_prob: float, decimal: float) -> float:
    """Fraction of bankroll Kelly says to stake. Clamped at 0 (never bet -EV).

    f* = (p * decimal - 1) / (decimal - 1)

    Full Kelly is aggressive and assumes your probability estimate is exact —
    which it never is — so callers should scale this down (quarter-Kelly is a
    common, saner default). Returns 0.0 for any non-positive-edge bet.
    """
    b = decimal - 1.0
    if b <= 0:
        return 0.0
    f = (true_prob * decimal - 1.0) / b
    return max(0.0, f)


# --- closing line value --------------------------------------------------

def clv(bet_decimal: float, close_decimal: float) -> float:
    """Closing Line Value: did you beat the price the market settled at?

    Measured in probability terms: (implied_at_close - implied_at_bet). If you
    bet at 2.60 (implied 0.385) and it closed at 2.40 (implied 0.417), you got
    a longer price than the final market — CLV = +0.032. Positive CLV over
    many bets is the strongest evidence you have a real, repeatable edge,
    because the closing line is the sharpest price the market ever shows.

    Note: for a fair comparison, close_decimal should be the DE-VIGGED closing
    price. See backtest.py, which de-vigs the closing market before calling in.
    """
    return decimal_to_implied(close_decimal) - decimal_to_implied(bet_decimal)
