import pytest
from datetime import datetime, timezone

from backend.collectors.player_stats.collector import PlayerStatsCollector
from backend.collectors.player_stats.base import PlayerStatsSource
from backend.models import Base, PlayerStat, Team
from backend.database import get_engine, get_session


class FakeSourceA(PlayerStatsSource):
    name = "source_a"

    async def fetch_season_averages(self, sport, team_abbr):
        return [{"player_name": "Player A", "points": 20.0, "rebounds": 5.0,
                 "assists": 3.0, "minutes": 30.0, "threes": 1.0,
                 "steals": 1.0, "blocks": 0.5, "turnovers": 2.0}]

    async def fetch_recent_games(self, sport, player_name, n=5):
        return [{"game_date": "2026-03-10", "points": 25.0, "rebounds": 6.0,
                 "assists": 4.0, "minutes": 32.0, "threes": 2.0,
                 "steals": 1.0, "blocks": 1.0, "turnovers": 1.0}]

    async def is_available(self):
        return True


class FakeSourceB(PlayerStatsSource):
    name = "source_b"

    async def fetch_season_averages(self, sport, team_abbr):
        return [{"player_name": "Player B", "points": 15.0, "rebounds": 4.0,
                 "assists": 2.0, "minutes": 25.0, "threes": 0.5,
                 "steals": 0.5, "blocks": 0.0, "turnovers": 1.5}]

    async def fetch_recent_games(self, sport, player_name, n=5):
        return []

    async def is_available(self):
        return True


class FailingSource(PlayerStatsSource):
    name = "failing"

    async def fetch_season_averages(self, sport, team_abbr):
        raise ConnectionError("API down")

    async def fetch_recent_games(self, sport, player_name, n=5):
        raise ConnectionError("API down")

    async def is_available(self):
        return False


@pytest.fixture
def db_session():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    team = Team(name="Test Team", abbreviation="TST", sport="nba")
    session.add(team)
    session.commit()
    yield session
    session.close()


@pytest.mark.asyncio
async def test_primary_source_used():
    collector = PlayerStatsCollector({"nba": [FakeSourceA(), FakeSourceB()]})
    result, source_name = await collector.fetch_player_stats("nba", "TST")
    assert len(result) == 1
    assert result[0]["player_name"] == "Player A"
    assert source_name == "source_a"


@pytest.mark.asyncio
async def test_fallback_on_failure():
    collector = PlayerStatsCollector({"nba": [FailingSource(), FakeSourceB()]})
    result, source_name = await collector.fetch_player_stats("nba", "TST")
    assert len(result) == 1
    assert result[0]["player_name"] == "Player B"
    assert source_name == "source_b"


@pytest.mark.asyncio
async def test_all_sources_fail():
    collector = PlayerStatsCollector({"nba": [FailingSource()]})
    result, source_name = await collector.fetch_player_stats("nba", "TST")
    assert result == []
    assert source_name is None


def test_store_stats(db_session):
    collector = PlayerStatsCollector({})
    stats = [{"player_name": "Player A", "points": 20.0, "rebounds": 5.0,
              "assists": 3.0, "minutes": 30.0, "threes": 1.0,
              "steals": 1.0, "blocks": 0.5, "turnovers": 2.0}]
    team = db_session.query(Team).first()
    count = collector.store_stats(db_session, stats, "season_avg", team.id, "nba", "source_a")
    assert count == 1
    row = db_session.query(PlayerStat).first()
    assert row.player_name == "Player A"
    assert row.points == 20.0
    assert row.source == "source_a"


def test_store_stats_upsert(db_session):
    collector = PlayerStatsCollector({})
    team = db_session.query(Team).first()
    stats = [{"player_name": "Player A", "points": 20.0}]
    collector.store_stats(db_session, stats, "season_avg", team.id, "nba", "source_a")
    stats2 = [{"player_name": "Player A", "points": 22.0}]
    collector.store_stats(db_session, stats2, "season_avg", team.id, "nba", "source_a")
    assert db_session.query(PlayerStat).count() == 1
    assert db_session.query(PlayerStat).first().points == 22.0


def test_store_stats_persists_receptions(db_session):
    """receptions must be persisted on insert -- it maps player_receptions
    props and both prop_analyzer.py and grader.py depend on it existing."""
    collector = PlayerStatsCollector({})
    team = db_session.query(Team).first()
    stats = [{"player_name": "Player A", "receptions": 7.0}]
    count = collector.store_stats(db_session, stats, "season_avg", team.id, "nfl", "source_a")
    assert count == 1
    row = db_session.query(PlayerStat).first()
    assert row.receptions == 7.0


def test_store_stats_updates_receptions(db_session):
    """receptions must also flow through the update (upsert) branch."""
    collector = PlayerStatsCollector({})
    team = db_session.query(Team).first()
    stats = [{"player_name": "Player A", "receptions": 5.0}]
    collector.store_stats(db_session, stats, "season_avg", team.id, "nfl", "source_a")
    stats2 = [{"player_name": "Player A", "receptions": 9.0}]
    collector.store_stats(db_session, stats2, "season_avg", team.id, "nfl", "source_a")
    assert db_session.query(PlayerStat).count() == 1
    assert db_session.query(PlayerStat).first().receptions == 9.0


def test_normalize_name():
    collector = PlayerStatsCollector({})
    assert collector._normalize_name("LeBron James") == "LeBron James"
    assert collector._normalize_name("James, LeBron") == "LeBron James"
    assert collector._normalize_name("  LeBron James  ") == "LeBron James"
    assert collector._normalize_name("") == ""
