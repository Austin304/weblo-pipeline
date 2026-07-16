"""Monte-Carlo a compounding bankroll — the '$100 grows itself' plan.

This tests the exact idea: start small, bet heavy favorites, and stake more as
the bankroll grows (a fraction of the current roll each time, so bets scale up
with capital). We run thousands of parallel "seasons" and look at where the
bankroll actually ends up.

The one thing everyone misses: with a growing (compounding) stake, the number
that governs your fate is NOT the average profit per bet — it's the *log-growth
rate*. Averages are dragged upward by a few lucky runs that never happen to you.
The median run follows the log-growth rate, and for a high-variance bet like a
heavy favorite, log-growth can be NEGATIVE even when the average edge is zero.
Translation: you can have a break-even bet and still go broke by compounding it,
purely from variance. Betting bigger as you grow makes that worse, not better.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import mean, median


@dataclass
class CompoundStats:
    start: float
    decimal: float
    implied: float           # win rate the price needs just to break even
    true_prob: float         # win rate we assume the favorite really has
    fraction: float          # fraction of current bankroll staked each bet
    bets: int
    runs: int
    ev_per_bet: float        # arithmetic edge per unit staked (the 'average')
    log_growth: float        # expected log-growth per bet (governs the median)
    ruin_rate: float         # fraction of runs that fell below the ruin line
    median_end: float
    mean_end: float
    p_profit: float          # fraction of runs that ended above the start
    p10: float               # 10th percentile ending bankroll
    p90: float               # 90th percentile ending bankroll

    def format(self) -> str:
        drift = ("break-even" if abs(self.ev_per_bet) < 1e-6
                 else ("+EV" if self.ev_per_bet > 0 else "-EV"))
        med_verdict = ("GROWS" if self.log_growth > 0 else "SHRINKS TO ZERO")
        return (
            f"\n  price {self.decimal:.3f} needs {self.implied:.1%} to break even; "
            f"assuming true win rate {self.true_prob:.1%} ({drift})\n"
            f"  staking {self.fraction:.0%} of the roll each bet, "
            f"{self.bets} bets/run, {self.runs} runs\n"
            f"    avg edge per bet    : {self.ev_per_bet:+.3%}   (the tempting number)\n"
            f"    log-growth per bet  : {self.log_growth:+.4f}   -> the typical run {med_verdict}\n"
            f"    ruin rate           : {self.ruin_rate:.1%}  (fell below 10% of start)\n"
            f"    median ending roll  : {self.median_end:8.2f}   (from {self.start:.0f})\n"
            f"    mean ending roll    : {self.mean_end:8.2f}   (skewed up by lucky runs)\n"
            f"    ended in profit     : {self.p_profit:.1%} of runs\n"
            f"    10th–90th pctile    : {self.p10:.2f}  …  {self.p90:.2f}\n"
        )


def log_growth_rate(true_prob: float, decimal: float, fraction: float) -> float:
    """Expected log-growth per bet. Positive -> the median roll grows; negative
    -> it decays toward zero no matter how good the average looks."""
    b = decimal - 1.0
    win_mult = 1.0 + fraction * b
    lose_mult = 1.0 - fraction
    if win_mult <= 0 or lose_mult <= 0:
        return float("-inf")
    return true_prob * math.log(win_mult) + (1.0 - true_prob) * math.log(lose_mult)


def simulate(*, start: float = 100.0, decimal: float = 1.125,
             true_prob: float | None = None, vig_drag: float = 0.015,
             fraction: float = 0.25, bets: int = 200, runs: int = 20000,
             seed: int = 7, ruin_frac: float = 0.10) -> CompoundStats:
    """Run the compounding experiment.

    decimal   : the favorite's price (1.125 == -800 American).
    true_prob : the favorite's real win rate. If None, we assume the market is
                efficient and set it to the price's implied prob MINUS vig_drag
                (you pay the vig, so your true rate is a touch below break-even).
    fraction  : share of the current bankroll staked each bet (this is the
                'bet bigger as it grows' part).
    """
    implied = 1.0 / decimal
    if true_prob is None:
        true_prob = implied - vig_drag
    rng = random.Random(seed)
    ruin_line = start * ruin_frac

    ends: list[float] = []
    ruined = 0
    for _ in range(runs):
        roll = start
        hit_ruin = False
        for _ in range(bets):
            stake = roll * fraction
            if rng.random() < true_prob:
                roll += stake * (decimal - 1.0)
            else:
                roll -= stake
            if roll <= ruin_line:
                hit_ruin = True
                break
        ends.append(roll)
        ruined += int(hit_ruin)

    ends_sorted = sorted(ends)
    def pct(p: float) -> float:
        return ends_sorted[min(len(ends_sorted) - 1, int(p * len(ends_sorted)))]

    return CompoundStats(
        start=start, decimal=decimal, implied=implied, true_prob=true_prob,
        fraction=fraction, bets=bets, runs=runs,
        ev_per_bet=true_prob * decimal - 1.0,
        log_growth=log_growth_rate(true_prob, decimal, fraction),
        ruin_rate=ruined / runs,
        median_end=median(ends), mean_end=mean(ends),
        p_profit=sum(1 for e in ends if e > start) / runs,
        p10=pct(0.10), p90=pct(0.90),
    )
