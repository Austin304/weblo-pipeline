"""Paper bankroll — SQLite-backed, no real money ever touched.

Mirrors the repo's state pattern (one SQLite file, plain SQL). Records every
simulated bet, settles it against the real outcome, and reports the numbers
that actually matter: ROI, realised EV, win rate, closing-line value, and max
drawdown. If any of these are negative over a big sample, the strategy loses —
better to learn that here than on a live card.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import PlacedBet

SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL,
  game_id       TEXT NOT NULL,
  sport         TEXT,
  selection     TEXT NOT NULL,
  decimal_odds  REAL NOT NULL,
  stake         REAL NOT NULL,
  grade         TEXT,
  est_true_prob REAL,
  placed_at     TEXT NOT NULL,
  won           INTEGER,
  pnl           REAL,
  clv           REAL
);
CREATE INDEX IF NOT EXISTS idx_bets_run ON bets(run_id);
"""


@dataclass
class Report:
    strategy: str
    starting_bankroll: float
    ending_bankroll: float
    n_bets: int
    n_wins: int
    total_staked: float
    net_pnl: float
    roi: float              # net_pnl / total_staked
    win_rate: float
    avg_clv: float          # mean closing-line value (prob units)
    max_drawdown: float     # largest peak-to-trough dip in bankroll

    def format(self) -> str:
        sign = "+" if self.net_pnl >= 0 else ""
        verdict = ("EDGE: beating the closing line" if self.avg_clv > 0
                   else "NO EDGE: market is ahead of you")
        return (
            f"\n=== {self.strategy.upper()} ===\n"
            f"  bets placed     : {self.n_bets}\n"
            f"  win rate        : {self.win_rate:.1%}  ({self.n_wins}/{self.n_bets})\n"
            f"  total staked    : {self.total_staked:.2f} u\n"
            f"  net P&L         : {sign}{self.net_pnl:.2f} u\n"
            f"  ROI             : {self.roi:+.2%}   <- the bottom line\n"
            f"  avg CLV         : {self.avg_clv:+.3f}   <- {verdict}\n"
            f"  bankroll        : {self.starting_bankroll:.2f} -> {self.ending_bankroll:.2f} u\n"
            f"  max drawdown    : {self.max_drawdown:.2f} u\n"
        )


class Portfolio:
    def __init__(self, db_path: str | Path, starting_bankroll: float = 100.0):
        self.db_path = str(db_path)
        self.starting_bankroll = starting_bankroll
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def record(self, run_id: str, bet: PlacedBet) -> None:
        self._conn.execute(
            """INSERT INTO bets (run_id, game_id, sport, selection, decimal_odds,
                   stake, grade, est_true_prob, placed_at, won, pnl, clv)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, bet.game_id, bet.sport, bet.selection, bet.decimal,
             bet.stake, bet.grade, bet.est_true_prob,
             bet.placed_at.isoformat(),
             None if bet.won is None else int(bet.won),
             bet.pnl, bet.clv),
        )
        self._conn.commit()

    def report(self, run_id: str, strategy: str) -> Report:
        rows = self._conn.execute(
            "SELECT stake, won, pnl, clv FROM bets WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()

        n = len(rows)
        wins = sum(1 for r in rows if r["won"] == 1)
        staked = sum(r["stake"] for r in rows)
        net = sum((r["pnl"] or 0.0) for r in rows)
        clvs = [r["clv"] for r in rows if r["clv"] is not None]
        avg_clv = sum(clvs) / len(clvs) if clvs else 0.0

        # Walk the equity curve for max drawdown.
        bankroll = self.starting_bankroll
        peak = bankroll
        max_dd = 0.0
        for r in rows:
            bankroll += (r["pnl"] or 0.0)
            peak = max(peak, bankroll)
            max_dd = max(max_dd, peak - bankroll)

        return Report(
            strategy=strategy,
            starting_bankroll=self.starting_bankroll,
            ending_bankroll=self.starting_bankroll + net,
            n_bets=n,
            n_wins=wins,
            total_staked=staked,
            net_pnl=net,
            roi=(net / staked) if staked else 0.0,
            win_rate=(wins / n) if n else 0.0,
            avg_clv=avg_clv,
            max_drawdown=max_dd,
        )


def now() -> datetime:
    return datetime.now(timezone.utc)
