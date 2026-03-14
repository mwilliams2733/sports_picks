from datetime import date, datetime, timezone
from backend.models import Base, PlayerStat, Team
from backend.database import get_engine, get_session

def _setup():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    team = Team(name="Boston Celtics", abbreviation="BOS", sport="nba")
    session.add(team)
    session.commit()
    return session, team

def test_player_stat_creation():
    session, team = _setup()
    stat = PlayerStat(
        player_name="Jayson Tatum", team_id=team.id, sport="nba",
        stat_type="season_avg", points=27.5, rebounds=8.1, assists=4.7,
        threes=2.8, minutes=36.2, steals=1.1, blocks=0.7, turnovers=2.9,
        source="nba_api", fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(stat)
    session.commit()
    row = session.query(PlayerStat).first()
    assert row.player_name == "Jayson Tatum"
    assert row.points == 27.5
    assert row.stat_type == "season_avg"
    assert row.source == "nba_api"
    assert row.is_stale is False
    session.close()

def test_player_stat_game_log():
    session, team = _setup()
    stat = PlayerStat(
        player_name="Jayson Tatum", team_id=team.id, sport="nba",
        stat_type="game_log", game_date=date(2026, 3, 10),
        points=32.0, rebounds=9.0, assists=5.0, minutes=38.0,
        source="nba_api", fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(stat)
    session.commit()
    row = session.query(PlayerStat).first()
    assert row.game_date == date(2026, 3, 10)
    assert row.stat_type == "game_log"
    session.close()

def test_player_stat_football_fields():
    session, team = _setup()
    team.sport = "nfl"
    team.abbreviation = "KC"
    team.name = "Kansas City Chiefs"
    session.commit()
    stat = PlayerStat(
        player_name="Patrick Mahomes", team_id=team.id, sport="nfl",
        stat_type="season_avg", pass_yards=285.3, touchdowns=2.1,
        rush_yards=25.4, source="espn", fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(stat)
    session.commit()
    row = session.query(PlayerStat).first()
    assert row.pass_yards == 285.3
    assert row.touchdowns == 2.1
    assert row.points is None
    session.close()

def test_player_stat_upsert():
    session, team = _setup()
    stat1 = PlayerStat(
        player_name="Jayson Tatum", team_id=team.id, sport="nba",
        stat_type="season_avg", game_date=None, points=27.5,
        source="nba_api", fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(stat1)
    session.commit()
    existing = session.query(PlayerStat).filter_by(
        player_name="Jayson Tatum", sport="nba", stat_type="season_avg", game_date=None
    ).first()
    assert existing is not None
    existing.points = 28.0
    session.commit()
    assert session.query(PlayerStat).count() == 1
    assert session.query(PlayerStat).first().points == 28.0
    session.close()

def test_strategy_model_has_strategy_type():
    from backend.models import StrategyModel
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    strat = StrategyModel(name="prop_value", config_json="{}", strategy_type="prop")
    session.add(strat)
    session.commit()
    row = session.query(StrategyModel).first()
    assert row.strategy_type == "prop"
    strat2 = StrategyModel(name="ensemble", config_json="{}")
    session.add(strat2)
    session.commit()
    row2 = session.query(StrategyModel).filter_by(name="ensemble").first()
    assert row2.strategy_type == "game"
    session.close()
