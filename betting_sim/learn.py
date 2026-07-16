"""The learning layer — improve future bets from resolved ones, honestly.

You can't fine-tune an LLM from a handful of settled markets. But you can learn
real, useful things from the paper-bet track record, without retraining anything:

  1. WHERE the edge is. Group resolved bets by category and measure skill/ROI per
     category. Maybe the model beats the market on sports and gets wrecked on
     crypto. Learning that = betting more where it works, less where it doesn't.

  2. Fix systematic miscalibration. If the model's 70%s only happen 60% of the
     time, it's overconfident, and that bias is correctable with Platt scaling
     (a standard recalibration: fit sigmoid(a*logit(p)+b) to the outcomes). Apply
     the learned correction to future forecasts and they get more honest.

  3. Remember its mistakes. Surface the biggest misses so they can be fed back as
     few-shot examples to the forecaster.

Everything here is gated on sample size. With < ~20 resolved bets these estimates
are noise, and the functions say so rather than pretending to have learned.
"""
from __future__ import annotations

import math

from . import forecast as fc
from .scanner import _category

MIN_SAMPLE = 20   # below this, "learning" is just noise; we refuse to conclude


def _resolved(data: dict) -> list[dict]:
    return [b for b in data["bets"] if b["status"] == "resolved"]


def by_category(data: dict) -> dict[str, dict]:
    """Skill vs market, ROI, and N per market category (resolved bets only)."""
    groups: dict[str, list[dict]] = {}
    for b in _resolved(data):
        groups.setdefault(_category(b["question"]), []).append(b)

    report = {}
    for cat, bets in groups.items():
        recs = [fc.ForecastRecord(market_id=b["market_id"],
                                  forecast_prob=b["model_prob"],
                                  market_prob=b["market_prob"],
                                  outcome=b["outcome"]) for b in bets]
        ev = fc.evaluate(recs)
        staked = sum(b["stake"] for b in bets if b["side"] != "none")
        net = sum(b["pnl"] for b in bets if b["pnl"] is not None)
        report[cat] = {
            "n": len(bets), "skill": round(ev.skill_score, 4),
            "roi": round(net / staked, 4) if staked else 0.0,
            "net": round(net, 4), "enough": len(bets) >= MIN_SAMPLE,
        }
    return report


def _logit(p: float) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x < -30:
        return 1e-13
    if x > 30:
        return 1 - 1e-13
    return 1 / (1 + math.exp(-x))


def fit_calibration(data: dict, iters: int = 2000, lr: float = 0.05):
    """Platt scaling: learn (a, b) so sigmoid(a*logit(p)+b) is better calibrated.

    Returns (a, b, trustworthy). Identity (1, 0) until there's enough data; a
    caller should only apply the correction when `trustworthy` is True.
    """
    resolved = _resolved(data)
    if len(resolved) < MIN_SAMPLE:
        return 1.0, 0.0, False
    xs = [_logit(b["model_prob"]) for b in resolved]
    ys = [float(b["outcome"]) for b in resolved]
    a, b = 1.0, 0.0
    n = len(xs)
    for _ in range(iters):
        ga = gb = 0.0
        for x, y in zip(xs, ys):
            pred = _sigmoid(a * x + b)
            err = pred - y
            ga += err * x
            gb += err
        a -= lr * ga / n
        b -= lr * gb / n
    return round(a, 4), round(b, 4), True


def apply_correction(prob: float, a: float, b: float) -> float:
    """Map a raw forecast through the learned recalibration."""
    return _sigmoid(a * _logit(prob) + b)


def biggest_misses(data: dict, k: int = 5) -> list[dict]:
    """Resolved bets where the model was most wrong (for few-shot feedback)."""
    scored = [(abs(b["model_prob"] - b["outcome"]), b) for b in _resolved(data)]
    scored.sort(key=lambda t: -t[0])
    return [b for _, b in scored[:k]]


def focus(data: dict) -> list[str]:
    """Plain-language recommendations on where to bet more / less."""
    cats = by_category(data)
    recs = []
    for cat, r in sorted(cats.items(), key=lambda kv: -kv[1]["skill"]):
        if not r["enough"]:
            recs.append(f"{cat}: only {r['n']} resolved — keep collecting, no call yet")
        elif r["skill"] > 0 and r["roi"] > 0:
            recs.append(f"{cat}: EDGE (skill {r['skill']:+.3f}, ROI {r['roi']:+.1%}) — bet more here")
        else:
            recs.append(f"{cat}: no edge (skill {r['skill']:+.3f}) — stop or shrink toward market")
    return recs
