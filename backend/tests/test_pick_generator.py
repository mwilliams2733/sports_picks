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


def test_pick_generator_routes_mma_games_to_combat_strategy():
    """For sport=mma with seeded fighter Elo, the strategy used must be CombatSportsStrategy.
    The favorite fighter (higher Elo) at plus money should produce a moneyline pick."""
    from datetime import date as _date, datetime, timezone
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, Odds, EloRating, StrategyModel, PickModel
    from backend.pipeline.pick_generator import generate_and_store_picks

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="Conor McGregor", abbreviation="MCGREGOR", sport="mma")
    away = Team(id=2, name="Khabib Nurmagomedov", abbreviation="NURMAGOMEDOV", sport="mma")
    session.add_all([home, away]); session.flush()
    upcoming = Game(id=1, sport="mma", season="2026", date=_date(2026, 4, 29),
                     home_team_id=1, away_team_id=2, status="scheduled")
    session.add(upcoming); session.flush()
    session.add_all([
        EloRating(team_id=1, sport="mma", rating=1500),
        EloRating(team_id=2, sport="mma", rating=1800),  # away much stronger
        Odds(game_id=1, bookmaker="dk", moneyline_home=+200, moneyline_away=-250,
             spread_home=0.0, spread_away=0.0, over_under=0.0,
             timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)),
        StrategyModel(id=1, name="combat_sports", config_json='{"min_edge": 2.0}', is_active=True),
    ])
    session.commit()

    n = generate_and_store_picks(session, strategy_id=1, target_date=_date(2026, 4, 29))
    assert n >= 1
    picks = session.query(PickModel).filter(PickModel.game_id == 1).all()
    assert all(p.pick_type == "moneyline" for p in picks), \
        "Combat sports must not generate spread or over_under picks"
    # Away (Khabib) is the much stronger fighter at -250 — strategy should pick AWAY ML
    assert any("AWAY" in p.pick_value for p in picks)


def test_build_fighter_stats_returns_neutral_for_debut_fighter():
    """A fighter with no past fights should get fights_count=0, recent_form_score=0.5."""
    from datetime import date as _date
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, EloRating
    from backend.pipeline.pick_generator import _build_fighter_stats

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    fighter = Team(id=1, name="Rookie", abbreviation="RKE", sport="mma")
    session.add(fighter)
    session.flush()  # ensure team is persisted before EloRating FK insert
    session.add(EloRating(team_id=1, sport="mma", rating=1500.0))
    session.commit()

    fs = _build_fighter_stats(session, fighter_id=1, sport="mma", before_date=_date(2026, 4, 29))
    assert fs.fights_count == 0
    assert fs.recent_form_score == 0.5
    assert fs.opponent_avg_elo is None
    assert fs.days_since_last_fight is None


def test_build_fighter_stats_aggregates_recent_form():
    """A fighter with 3 wins and 2 losses in last 5 should have form_score=0.6."""
    from datetime import date as _date
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, EloRating
    from backend.pipeline.pick_generator import _build_fighter_stats

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    f1 = Team(id=1, name="Fighter1", abbreviation="F1", sport="mma")
    f2 = Team(id=2, name="Fighter2", abbreviation="F2", sport="mma")
    session.add_all([f1, f2])
    session.flush()  # ensure teams are persisted before EloRating FK insert
    session.add_all([EloRating(team_id=1, sport="mma", rating=1500.0),
                     EloRating(team_id=2, sport="mma", rating=1500.0)])
    session.flush()
    # 5 fights, fighter 1 wins 3
    outcomes = [(1, 0), (1, 0), (0, 1), (1, 0), (0, 1)]  # (home_score, away_score)
    for i, (h, a) in enumerate(outcomes):
        session.add(Game(sport="mma", season="2025", date=_date(2025, 1 + i, 15),
                         home_team_id=1, away_team_id=2,
                         home_score=h, away_score=a, status="final"))
    session.commit()

    fs = _build_fighter_stats(session, fighter_id=1, sport="mma", before_date=_date(2026, 4, 29))
    assert fs.fights_count == 5
    assert fs.recent_form_score == 0.6  # 3 wins of 5
    assert fs.opponent_avg_elo == 1500.0
    assert fs.days_since_last_fight is not None
