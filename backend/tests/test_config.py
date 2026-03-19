from backend.config import load_config, is_sport_in_season
from datetime import date

def test_load_config(tmp_path, monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    f = tmp_path / "c.yaml"
    f.write_text('database_path: "d.db"\nseasons:\n  nba:\n    start: "10-22"\n    end: "06-20"\nodds_budget:\n  monthly_limit: 20000\n  daily_target: 600\n  reserve: 2000\n')
    cfg = load_config(str(f))
    assert cfg["database_path"] == "d.db"
    assert cfg["odds_budget"]["monthly_limit"] == 20000

def test_load_config_reads_odds_api_key_from_env(tmp_path, monkeypatch):
    f = tmp_path / "c.yaml"
    f.write_text('database_path: "d.db"\nseasons:\n  nba:\n    start: "10-22"\n    end: "06-20"\nodds_budget:\n  monthly_limit: 20000\n  daily_target: 600\n  reserve: 2000\n')
    monkeypatch.setenv("ODDS_API_KEY", "env_test_key")
    cfg = load_config(str(f))
    assert cfg["odds_api_key"] == "env_test_key"

def test_load_config_no_env_key_returns_none(tmp_path, monkeypatch):
    f = tmp_path / "c.yaml"
    f.write_text('database_path: "d.db"\nseasons:\n  nba:\n    start: "10-22"\n    end: "06-20"\nodds_budget:\n  monthly_limit: 20000\n  daily_target: 600\n  reserve: 2000\n')
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    cfg = load_config(str(f))
    assert cfg.get("odds_api_key") is None

def test_is_sport_in_season_nba_december():
    seasons = {"nba": {"start": "10-22", "end": "06-20"}}
    assert is_sport_in_season("nba", seasons, date(2026, 12, 15)) is True

def test_is_sport_in_season_nba_august():
    seasons = {"nba": {"start": "10-22", "end": "06-20"}}
    assert is_sport_in_season("nba", seasons, date(2026, 8, 15)) is False
