from datetime import date, datetime
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.models import Base, Game, Team, StrategyModel, PickModel

def test_generate_picks_stores_to_db(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    t1 = Team(id=1, name="Boston Celtics", abbreviation="BOS", sport="nba")
    t2 = Team(id=2, name="LA Lakers", abbreviation="LAL", sport="nba")
    g = Game(id=1, sport="nba", season="2025-26", date=date.today(),
             home_team_id=1, away_team_id=2, status="scheduled")
    s = StrategyModel(id=1, name="ensemble", config_json='{"min_edge": 0.1, "k_factor": 20, "lookback": 10}',
                      is_active=True)
    db_session.add_all([t1, t2, g, s])
    db_session.commit()
    count = generate_and_store_picks(db_session, strategy_id=1)
    assert isinstance(count, int)
    stored = db_session.query(PickModel).all()
    assert isinstance(stored, list)


def test_build_game_data_attaches_pitcher_score_for_mlb():
    """When pitcher_scores contains an entry for an MLB game, both stats objects
    should pick up pitcher_skill_score from it."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, EloRating
    from backend.pipeline.pick_generator import _build_game_data
    from datetime import date as _date

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="BOS", abbreviation="BOS", sport="mlb")
    away = Team(id=2, name="NYY", abbreviation="NYY", sport="mlb")
    game = Game(id=10, sport="mlb", season="2026", date=_date(2026, 4, 29),
                home_team_id=1, away_team_id=2, status="scheduled")
    session.add_all([home, away, game])
    session.flush()  # ensure teams are visible before EloRating FK insert
    session.add_all([EloRating(team_id=1, sport="mlb", rating=1500),
                     EloRating(team_id=2, sport="mlb", rating=1500)])
    session.commit()

    pitcher_scores = {10: {"home": 0.78, "away": 0.41}}
    gd = _build_game_data(session, game, pitcher_scores=pitcher_scores)
    assert gd.home_stats.pitcher_skill_score == 0.78
    assert gd.away_stats.pitcher_skill_score == 0.41
    session.close()


def test_build_game_data_omits_pitcher_score_for_non_mlb():
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, EloRating
    from backend.pipeline.pick_generator import _build_game_data
    from datetime import date as _date

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="BOS", abbreviation="BOS", sport="nba")
    away = Team(id=2, name="LAL", abbreviation="LAL", sport="nba")
    game = Game(id=10, sport="nba", season="2025-26", date=_date(2026, 4, 29),
                home_team_id=1, away_team_id=2, status="scheduled")
    session.add_all([home, away, game])
    session.flush()  # ensure teams are visible before EloRating FK insert
    session.add_all([EloRating(team_id=1, sport="nba", rating=1500),
                     EloRating(team_id=2, sport="nba", rating=1500)])
    session.commit()

    gd = _build_game_data(session, game, pitcher_scores={10: {"home": 0.9, "away": 0.1}})
    assert gd.home_stats.pitcher_skill_score is None  # ignored for non-MLB
    assert gd.away_stats.pitcher_skill_score is None
    session.close()
