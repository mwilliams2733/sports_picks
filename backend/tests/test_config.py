from backend.config import load_config, is_sport_in_season
from datetime import date

def test_load_config(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text('odds_api_key: "k"\ndatabase_path: "d.db"\nseasons:\n  nba:\n    start: "10-22"\n    end: "06-20"\nodds_budget:\n  monthly_limit: 500\n  pause_at: 450\n')
    cfg = load_config(str(f))
    assert cfg["odds_api_key"] == "k"

def test_is_sport_in_season_nba_december():
    seasons = {"nba": {"start": "10-22", "end": "06-20"}}
    assert is_sport_in_season("nba", seasons, date(2026, 12, 15)) is True

def test_is_sport_in_season_nba_august():
    seasons = {"nba": {"start": "10-22", "end": "06-20"}}
    assert is_sport_in_season("nba", seasons, date(2026, 8, 15)) is False
