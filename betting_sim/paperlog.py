"""Paper-trading ledger — the honest way to find out if a model has edge.

The rules of proper learning, enforced by design:

  1. A forecast is logged WITH A TIMESTAMP BEFORE the outcome exists. The ledger
     is a JSON file committed to git, so the commit history is tamper-proof
     proof we didn't peek — you cannot retroactively 'predict' a settled market.
  2. Nothing is scored until it actually resolves. Skill/ROI are computed only
     on resolved bets — strictly out-of-sample.
  3. We record EVERY assessment, not just the bets. Even 'I agreed with the
     market, no bet' is data: it feeds calibration (when the model says 55%,
     does it happen 55% of the time?).
  4. Conclusions wait for sample size. A handful of resolved bets proves
     nothing; the scorecard prints the N and a warning until it's meaningful.

The model behind the forecasts is pluggable (an LLM+web-search run, a stats
model, or a human). The ledger doesn't care who forecasts — it only checks,
honestly, whether those forecasts beat the market over time.
"""
from __future__ import annotations

import json
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import forecast as fc

LEDGER_PATH = Path(__file__).resolve().parent / "paperbets.json"
GAMMA = "https://gamma-api.polymarket.com/markets"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load(path: Path = LEDGER_PATH) -> dict:
    if Path(path).is_file():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {"created": _now(), "bets": []}


def save(data: dict, path: Path = LEDGER_PATH) -> None:
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def add_forecast(data: dict, *, platform: str, market_id: str, slug: str,
                 question: str, close_date: str, model_prob: float,
                 market_prob: float, rationale: str,
                 edge_threshold: float = 0.05, stake: float = 1.0) -> dict:
    """Log one forecast. Bets when |model - market| >= threshold, else records a
    no-bet assessment (still used for calibration). Refuses to double-log a
    market that already has an open forecast."""
    for b in data["bets"]:
        if b["market_id"] == market_id and b["status"] == "open":
            return b  # already tracking this one
    edge = round(model_prob - market_prob, 4)
    side = ("yes" if edge >= edge_threshold else
            "no" if edge <= -edge_threshold else "none")
    rec = {
        "id": uuid.uuid4().hex[:10],
        "logged_at": _now(),
        "platform": platform, "market_id": str(market_id), "slug": slug,
        "question": question, "close_date": close_date,
        "model_prob": round(model_prob, 4), "market_prob": round(market_prob, 4),
        "edge": edge, "side": side, "stake": stake if side != "none" else 0.0,
        "rationale": rationale,
        "status": "open", "outcome": None, "pnl": None, "resolved_at": None,
    }
    data["bets"].append(rec)
    return rec


def _fetch_market(market_id: str) -> dict | None:
    url = f"{GAMMA}?id={market_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "betting_sim"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            rows = json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None
    return rows[0] if rows else None


def _pnl(side: str, market_prob: float, outcome: int) -> float:
    if side == "yes":
        return (1.0 - market_prob) if outcome == 1 else -market_prob
    if side == "no":
        return market_prob if outcome == 0 else -(1.0 - market_prob)
    return 0.0


def resolve(data: dict) -> list[dict]:
    """Check every open bet against Polymarket; settle the ones that closed.
    Returns the list of newly-resolved records."""
    newly = []
    for b in data["bets"]:
        if b["status"] != "open":
            continue
        m = _fetch_market(b["market_id"])
        if not m or not m.get("closed"):
            continue
        try:
            prices = json.loads(m.get("outcomePrices", "[]"))
            outcome = 1 if float(prices[0]) > 0.5 else 0
        except (ValueError, IndexError, TypeError, json.JSONDecodeError):
            continue
        b["outcome"] = outcome
        b["pnl"] = round(_pnl(b["side"], b["market_prob"], outcome), 4)
        b["status"] = "resolved"
        b["resolved_at"] = _now()
        newly.append(b)
    return newly


def scorecard(data: dict, *, edge_threshold: float = 0.05) -> dict:
    """Score the RESOLVED bets, strictly out-of-sample. Returns a summary dict
    plus the forecast.Evaluation for calibration/skill."""
    resolved = [b for b in data["bets"] if b["status"] == "resolved"]
    records = [fc.ForecastRecord(
        market_id=b["market_id"], forecast_prob=b["model_prob"],
        market_prob=b["market_prob"], outcome=b["outcome"],
        question=b["question"]) for b in resolved]
    ev = fc.evaluate(records, edge_threshold=edge_threshold) if records else None
    open_bets = [b for b in data["bets"] if b["status"] == "open"]
    net = sum(b["pnl"] for b in resolved if b["pnl"] is not None)
    staked = sum(b["stake"] for b in resolved if b["side"] != "none")
    return {
        "n_total": len(data["bets"]),
        "n_open": len(open_bets),
        "n_resolved": len(resolved),
        "net_pnl": round(net, 4),
        "staked": round(staked, 4),
        "roi": round(net / staked, 4) if staked else 0.0,
        "evaluation": ev,
    }
