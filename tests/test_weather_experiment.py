"""Guards for the weather experiment's parsing and source precedence."""
import math

import pandas as pd
import pytest

from backend.scripts.weather_experiment import (dome_teams, has_precip,
                                                parse_temp, parse_wind,
                                                weather_by_game)


@pytest.mark.parametrize("text,wind", [
    ("Rain Temp: 54° F, Humidity: 72%, Wind: N 12 mph", 12.0),
    ("Cloudy Temp: 74° F, Humidity: 95%, Wind: South 4 mph", 4.0),
    ("Temp: 40° F, Wind: Calm", 0.0),
    ("Sunny", None),
    (None, None),
])
def test_parse_wind(text, wind):
    assert parse_wind(text) == wind


def test_parse_temp_reads_below_zero():
    assert parse_temp("Snow Temp: -3° F, Wind: N 5 mph") == -3.0


@pytest.mark.parametrize("text,wet", [
    ("Light Rain Temp: 60° F", True), ("Snow Flurries Temp: 32° F", True),
    ("Drizzle Temp: 46° F", True), ("Sunny Temp: 70° F", False),
    ("Partly Cloudy, brain freeze", False), (None, False)])
def test_has_precip(text, wet):
    assert has_precip(text) is wet


def test_columns_win_and_text_fills_the_gaps():
    frame = pd.DataFrame({
        "game_id": ["a", "b"], "roof": ["outdoors", "outdoors"],
        "temp": [50.0, math.nan], "wind": [9.0, math.nan],
        "weather": ["Temp: 40° F, Wind: N 20 mph", "Temp: 33° F, Wind: W 16 mph"]})
    w = weather_by_game(frame)
    assert (w["a"].wind, w["a"].temp, w["a"].from_column) == (9.0, 50.0, True)
    assert (w["b"].wind, w["b"].temp, w["b"].from_column) == (16.0, 33.0, False)


def test_dome_team_is_a_majority_of_roofed_home_games():
    frame = pd.DataFrame({
        "game_id": list("abcde"), "season": [2025] * 5, "season_type": ["REG"] * 5,
        "home_team": ["DET", "DET", "DET", "GB", "GB"],
        "roof": ["dome", "dome", "outdoors", "outdoors", "dome"]})
    assert dome_teams(frame) == {(2025, "DET")}
