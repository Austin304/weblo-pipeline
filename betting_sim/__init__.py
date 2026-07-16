"""betting_sim — a paper-trading harness for testing betting strategies.

NO REAL MONEY. This package grades hypothetical bets, places them on a paper
bankroll, settles them against outcomes, and reports whether the strategy beats
the closing line. Prove an edge here before risking a cent.

Entry point: `python -m betting_sim.cli demo`
"""

__all__ = ["oddsmath", "models", "grader", "portfolio", "synth", "backtest",
           "lineshop", "oddsapi", "realdata", "bankroll", "polymarket",
           "forecast", "kalshi", "llmforecast", "paperlog",
           "scanner", "learn"]
