"""Guards for the travel experiment's distance, time-zone and rest arithmetic."""
from datetime import datetime

import pandas as pd
import pytest

from backend.analysis.football_weather import STADIUMS
from backend.scripts.travel_experiment import (ET, miles, rest_days, travel_rows,
                                               utc_offset_hours)


def test_miles_seattle_to_new_jersey():
    assert miles(STADIUMS["SEA"], STADIUMS["NYG"]) == pytest.approx(2400, rel=0.02)
    assert miles(STADIUMS["NYG"], STADIUMS["NYJ"]) == 0.0


def test_arizona_keeps_one_offset_while_the_rest_shift():
    october = datetime(2025, 10, 5, 13, tzinfo=ET)
    december = datetime(2025, 12, 7, 13, tzinfo=ET)
    assert utc_offset_hours("America/Phoenix", october) == utc_offset_hours("America/Phoenix", december) == -7
    assert utc_offset_hours("America/Los_Angeles", october) == -7     # PDT: same as Arizona
    assert utc_offset_hours("America/Los_Angeles", december) == -8    # PST: an hour apart


def _frame(rows):
    cols = ["game_id", "season", "season_type", "game_date", "home_team", "away_team",
            "start_time", "location", "result", "spread_line"]
    return pd.DataFrame(rows, columns=cols)


def test_a_west_coast_team_at_a_1pm_eastern_start():
    f = _frame([("g1", 2025, "REG", "2025-10-05", "NE", "SEA", "10/5/25, 13:00:00", "Home", 3.0, 2.5),
                ("g2", 2025, "REG", "2025-10-12", "NE", "SEA", "10/12/25, 20:20:00", "Home", 3.0, 2.5)])
    early, night = travel_rows(f)
    assert (early.tz_east, early.body_clock) == (3.0, 1.0)       # 10:00 in Seattle
    assert night.body_clock == 0.0                                # 17:20 in Seattle


def test_neutral_sites_are_excluded_and_la_maps_to_lar():
    f = _frame([("g1", 2025, "REG", "2025-10-05", "LA", "SF", "10/5/25, 16:05:00", "Home", 3.0, 2.5),
                ("g2", 2025, "REG", "2025-10-12", "JAX", "LA", "10/12/25, 09:30:00", "Neutral", 3.0, 2.5)])
    (row,) = travel_rows(f)
    assert row.game_id == "g1"
    assert row.distance == pytest.approx(miles(STADIUMS["SF"], STADIUMS["LAR"]) / 1000)


def test_rest_counts_days_since_the_previous_game_this_season():
    f = _frame([("a", 2025, "REG", "2025-09-07", "KC", "BUF", "x", "Home", 0, 0),
                ("b", 2025, "REG", "2025-09-11", "KC", "DEN", "x", "Home", 0, 0),
                ("c", 2025, "REG", "2025-09-21", "BUF", "KC", "x", "Home", 0, 0)])
    rest = rest_days(f)
    assert rest[("b", "KC")] == 4
    assert rest[("c", "KC")] == 10
    assert rest[("c", "BUF")] == 14
    assert ("a", "KC") not in rest                                 # first game: unknown
