"""Tests for the football-data.co.uk real-odds loader and 3-way helpers.

Uses a tiny inline CSV so it runs offline. Run:
    python -m betting_sim.tests.test_realdata
"""
from __future__ import annotations

import math

from betting_sim import realdata

# Minimal CSV with the columns the loader reads: result + two books'
# opening odds and Pinnacle closing odds.
CSV = (
    "Date,HomeTeam,AwayTeam,FTR,"
    "B365H,B365D,B365A,PSH,PSD,PSA,PSCH,PSCD,PSCA\n"
    # Home big favorite; home won.
    "01/01/2024,Alpha,Beta,H,1.40,4.50,8.00,1.42,4.60,8.20,1.38,4.70,9.00\n"
    # Two books that disagree enough to arb across the best of each outcome.
    "02/01/2024,Gamma,Delta,A,4.00,4.00,2.10,2.10,4.00,4.00,2.05,4.10,4.05\n"
    # Junk row (no result) should be skipped.
    ",,,,,,,,,,,,\n"
)


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=0, abs_tol=tol)


def test_parse_skips_junk_and_reads_books():
    matches = realdata.parse_csv(CSV)
    assert len(matches) == 2                      # junk row dropped
    m = matches[0]
    assert m.home == "Alpha" and m.away == "Beta" and m.result == "H"
    assert set(m.open_odds) == {"Bet365", "Pinnacle"}
    assert approx(m.open_odds["Bet365"]["H"], 1.40)
    assert "Pinnacle" in m.close_odds


def test_favorite_is_shortest_outcome():
    m = realdata.parse_csv(CSV)[0]
    assert realdata.favorite(m) == "H"            # 1.40/1.42 is the short side


def test_best_price_takes_max_across_books():
    m = realdata.parse_csv(CSV)[0]
    price, book = realdata.best_price(m, "A")     # away: 8.00 vs 8.20
    assert approx(price, 8.20) and book == "Pinnacle"


def test_pinnacle_fair_close_devigs_to_one():
    m = realdata.parse_csv(CSV)[0]
    fair = realdata.pinnacle_fair_close(m)
    assert fair is not None
    assert approx(sum(fair.values()), 1.0)


def test_arbitrage_math():
    matches = realdata.parse_csv(CSV)
    # Row 2: best H=4.00, best D=4.00, best A=4.00 -> 0.25*3=0.75 < 1 -> arb.
    arb = realdata.find_arbitrage(matches[1])
    assert arb is not None and arb > 0
    assert approx(arb, 1.0 / 0.75 - 1.0, tol=1e-6)
    # Row 1 has normal vig -> no arb.
    assert realdata.find_arbitrage(matches[0]) is None


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
