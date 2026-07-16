"""The honest scorecard for a forecasting model — does it actually beat the market?

This is the most important file for the "build an ML model" plan, because it
answers the only question that matters: **is your forecaster more accurate than
the market price, in a way that makes money?** A model that's 80% accurate is
worthless if the market is already 82% — the market price is itself a strong
aggregated model (crowd + smart money that already read the news).

So we never trust a model's confidence. We score it three ways, all out-of-sample:

  1. BRIER SKILL SCORE vs the market. Brier = mean((prob - outcome)^2), lower is
     better. Skill = 1 - brier_model / brier_market. Positive means your
     forecasts are genuinely more accurate than the market's price. Zero or
     negative means you have no edge, no matter how good the model 'feels'.

  2. CALIBRATION. When you say 70%, does it happen ~70% of the time? A model can
     look accurate and still be miscalibrated (overconfident), which quietly
     bleeds money. We bin predictions and compare to reality.

  3. REALIZED ROI on edge-selected bets. Only bet when the model disagrees with
     the market by more than a threshold; settle against the real outcome; net
     it out after fees. This is the money-truth.

Feed it records of (your prob, market prob, what actually happened). If the
skill score isn't positive and the ROI isn't positive out-of-sample, you do not
have an edge — full stop. Better to learn that here than with real money.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ForecastRecord:
    market_id: str
    forecast_prob: float     # your model's P(yes), 0..1
    market_prob: float       # the market's price for yes, 0..1
    outcome: int             # what happened: 1 = yes, 0 = no
    question: str = ""


@dataclass
class CalibrationBin:
    lo: float
    hi: float
    n: int
    mean_forecast: float
    empirical_rate: float    # fraction that actually happened


@dataclass
class Evaluation:
    n: int
    brier_forecast: float
    brier_market: float
    skill_score: float       # 1 - bf/bm; > 0 == more accurate than the market
    logloss_forecast: float
    logloss_market: float
    calibration: list[CalibrationBin]
    n_bets: int
    hit_rate: float
    roi: float               # net profit / total staked, after fees
    avg_edge: float          # mean |forecast - market| on the bets taken
    net: float

    def format(self) -> str:
        edge = "HAS EDGE" if self.skill_score > 0 and self.roi > 0 else "NO EDGE"
        return (
            f"\n  records         : {self.n}\n"
            f"  Brier (model)   : {self.brier_forecast:.4f}   (lower is better)\n"
            f"  Brier (market)  : {self.brier_market:.4f}   <- the bar to beat\n"
            f"  skill vs market : {self.skill_score:+.3f}   "
            f"{'more accurate than the market' if self.skill_score > 0 else 'NOT better than the market'}\n"
            f"  log-loss m/mkt  : {self.logloss_forecast:.4f} / {self.logloss_market:.4f}\n"
            f"  bets taken      : {self.n_bets}  (edge over threshold)\n"
            f"  hit rate        : {self.hit_rate:.1%}\n"
            f"  avg edge claimed: {self.avg_edge:+.3f}\n"
            f"  realized ROI    : {self.roi:+.2%}   <- the money-truth\n"
            f"  verdict         : {edge}\n"
        )


def brier(records: list[ForecastRecord], which: str) -> float:
    if not records:
        return float("nan")
    return sum((getattr(r, which) - r.outcome) ** 2 for r in records) / len(records)


def _clamp(p: float, eps: float = 1e-6) -> float:
    return min(1.0 - eps, max(eps, p))


def logloss(records: list[ForecastRecord], which: str) -> float:
    if not records:
        return float("nan")
    total = 0.0
    for r in records:
        p = _clamp(getattr(r, which))
        total += -(r.outcome * math.log(p) + (1 - r.outcome) * math.log(1 - p))
    return total / len(records)


def calibration(records: list[ForecastRecord], nbins: int = 10) -> list[CalibrationBin]:
    bins: list[CalibrationBin] = []
    for i in range(nbins):
        lo, hi = i / nbins, (i + 1) / nbins
        chunk = [r for r in records
                 if (r.forecast_prob >= lo and
                     (r.forecast_prob < hi or (i == nbins - 1 and r.forecast_prob <= hi)))]
        if not chunk:
            continue
        bins.append(CalibrationBin(
            lo=lo, hi=hi, n=len(chunk),
            mean_forecast=sum(r.forecast_prob for r in chunk) / len(chunk),
            empirical_rate=sum(r.outcome for r in chunk) / len(chunk),
        ))
    return bins


def evaluate(records: list[ForecastRecord], *, edge_threshold: float = 0.05,
             fee: float = 0.0, nbins: int = 10) -> Evaluation:
    """Score a forecaster. `edge_threshold` = min |forecast - market| to bet;
    `fee` = per-trade cost as a fraction of the $1 notional (Polymarket ~0,
    Kalshi small). Bets buy the side the forecast favors at the market price and
    settle against the real outcome."""
    n_bets = wins = 0
    staked = net = edge_sum = 0.0
    for r in records:
        diff = r.forecast_prob - r.market_prob
        if abs(diff) < edge_threshold:
            continue
        n_bets += 1
        edge_sum += abs(diff)
        staked += 1.0
        if diff > 0:                       # model thinks Yes is underpriced -> buy Yes
            pnl = (1.0 - r.market_prob) if r.outcome == 1 else -r.market_prob
            wins += int(r.outcome == 1)
        else:                              # model thinks No is underpriced -> buy No
            pnl = r.market_prob if r.outcome == 0 else -(1.0 - r.market_prob)
            wins += int(r.outcome == 0)
        net += pnl - fee

    bf, bm = brier(records, "forecast_prob"), brier(records, "market_prob")
    return Evaluation(
        n=len(records), brier_forecast=bf, brier_market=bm,
        skill_score=(1.0 - bf / bm) if bm > 0 else 0.0,
        logloss_forecast=logloss(records, "forecast_prob"),
        logloss_market=logloss(records, "market_prob"),
        calibration=calibration(records, nbins),
        n_bets=n_bets, hit_rate=(wins / n_bets) if n_bets else 0.0,
        roi=(net / staked) if staked else 0.0,
        avg_edge=(edge_sum / n_bets) if n_bets else 0.0,
        net=net,
    )
