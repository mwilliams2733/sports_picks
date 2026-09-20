"""Guards for the odds-only runner.

The whole reason this script exists is that a window welds a 10-second odds
fetch to a 25-minute player-stats collection, so a time-sensitive price
fetch waits on rosters nobody needs today. The dangerous outcome is
therefore the obvious one: quietly doing the slow part anyway.
"""
import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, StrategyModel, Team

from backend.time_utils import et_today

TODAY = et_today()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "odds.db"
    s = get_session(get_engine(str(path)))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="A", abbreviation="A", sport="mlb"),
               Team(id=2, name="B", abbreviation="B", sport="mlb"),
               Team(id=3, name="C", abbreviation="C", sport="ncaaf"),
               Team(id=4, name="D", abbreviation="D", sport="ncaaf")])
    s.flush()
    s.add_all([
        Game(id=1, sport="mlb", season="2026", date=TODAY, status="scheduled",
             home_team_id=1, away_team_id=2),
        Game(id=2, sport="ncaaf", season="2026", date=TODAY, status="scheduled",
             home_team_id=3, away_team_id=4),
    ])
    s.add(StrategyModel(id=1, name="ensemble", config_json="{}",
                        is_active=True, strategy_type="game"))
    s.commit()
    s.close()
    return str(path)


@pytest.fixture()
def mod(monkeypatch):
    import backend.scripts.fetch_odds_now as m
    monkeypatch.setattr(m, "load_config", lambda _p: {
        "database_path": ":memory:", "odds_api_key": "k",
        "odds_budget": {"monthly_limit": 20000, "daily_target": 600, "reserve": 2000},
        "seasons": {"mlb": {"start": "01-01", "end": "12-31"},
                    "ncaaf": {"start": "01-01", "end": "12-31"},
                    "nba": {"start": "10-22", "end": "06-20"}},
    })
    monkeypatch.setattr(m, "fetch_and_store_odds", AsyncMock(return_value=11))
    monkeypatch.setattr(m, "fetch_and_store_props", AsyncMock(return_value=5))
    monkeypatch.setattr(m, "generate_and_store_picks", MagicMock(return_value=7))
    monkeypatch.setattr(m, "run_prop_pipeline",
                        AsyncMock(return_value={"picks_generated": 2}))
    monkeypatch.setattr(m, "fetch_pitcher_scores_for_date",
                        AsyncMock(return_value={}))
    monkeypatch.setattr(m, "get_credit_summary",
                        lambda *a, **k: {"daily_used": 1, "monthly_used": 2,
                                         "monthly_limit": 20000})
    return m


def test_odds_are_fetched_for_the_requested_sports(db, mod):
    mod.run(db, sports=("mlb",))
    mod.fetch_and_store_odds.assert_awaited_once()
    assert mod.fetch_and_store_odds.await_args.args[1] == ["mlb"]


def test_the_slow_player_stats_path_is_skipped_by_default(db, mod):
    """The entire point. Props are what take 25 minutes."""
    mod.run(db, sports=("mlb",))
    mod.fetch_and_store_props.assert_not_awaited()
    mod.run_prop_pipeline.assert_not_awaited()


def test_props_can_be_opted_into(db, mod):
    mod.run(db, sports=("mlb",), with_props=True)
    mod.fetch_and_store_props.assert_awaited_once()
    mod.run_prop_pipeline.assert_awaited_once()


def test_game_picks_are_generated(db, mod):
    summary = mod.run(db, sports=("mlb",))
    mod.generate_and_store_picks.assert_called_once()
    assert summary["picks"] == 7


def test_picks_can_be_skipped(db, mod):
    mod.run(db, sports=("mlb",), with_picks=False)
    mod.generate_and_store_picks.assert_not_called()


def test_mlb_gets_pitcher_scores(db, mod):
    """Without them every MLB pick is priced on a neutral starter."""
    mod.run(db, sports=("mlb",))
    mod.fetch_pitcher_scores_for_date.assert_awaited_once()


def test_a_non_mlb_sport_does_not_fetch_pitchers(db, mod):
    mod.run(db, sports=("ncaaf",))
    mod.fetch_pitcher_scores_for_date.assert_not_awaited()


def test_a_pitcher_fetch_failure_does_not_stop_the_picks(db, mod, monkeypatch):
    monkeypatch.setattr(mod, "fetch_pitcher_scores_for_date",
                        AsyncMock(side_effect=RuntimeError("espn down")))
    summary = mod.run(db, sports=("mlb",))
    mod.generate_and_store_picks.assert_called_once()
    assert summary["picks"] == 7


def test_without_a_sport_it_uses_the_in_season_ones(db, mod):
    mod.run(db)
    assert mod.fetch_and_store_odds.await_args.args[1] == ["ncaaf", "mlb"]


def test_no_api_key_means_no_odds_call(db, mod, monkeypatch):
    monkeypatch.setattr(mod, "load_config", lambda _p: {
        "seasons": {"mlb": {"start": "01-01", "end": "12-31"}}})
    summary = mod.run(db, sports=("mlb",))
    mod.fetch_and_store_odds.assert_not_awaited()
    assert summary["odds"] == 0


def test_it_refuses_a_db_path_that_does_not_exist(tmp_path, mod):
    with pytest.raises(FileNotFoundError):
        mod.run(str(tmp_path / "nope.db"))


# --------------------------------------------------------------------------
# An empty pitcher map used to be indistinguishable from "no MLB games".
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skipped_games_are_counted_and_reported(monkeypatch, caplog):
    """Returning {} silently is how this went unnoticed for months."""
    import logging

    import backend.pipeline.scheduler as sch

    class _Collector:
        async def fetch_schedule(self, target_date):
            return [{"home_team": None, "away_team": None,
                     "home_probable_pitcher_id": 1,
                     "away_probable_pitcher_id": 2}]
        async def close(self):
            return None

    monkeypatch.setattr("backend.collectors.mlb_stats.MLBStatsCollector",
                        _Collector)
    with caplog.at_level(logging.WARNING):
        out = await sch.fetch_pitcher_scores_for_date(TODAY)

    assert out == {}
    assert "unidentifiable" in caplog.text


def test_pick_generation_is_scoped_to_the_requested_sports(db, mod):
    """--sport mlb must not re-pick ncaaf as a side effect."""
    mod.run(db, sports=("mlb",))
    assert mod.generate_and_store_picks.call_args.kwargs["sports"] == ("mlb",)
