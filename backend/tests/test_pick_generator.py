from datetime import date, datetime
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.models import Base, Game, Team, StrategyModel, PickModel
from backend.analysis.variants.value_only import ValueOnlyStrategy

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


def test_one_bad_game_does_not_discard_the_batch(db_engine, db_session, monkeypatch):
    """A strategy.predict() exception on one game must not lose picks from
    the other games in the same run. Before this guard, an unhandled
    exception from predict() propagated past session.commit(), discarding
    every pick generated in the loop so far."""
    from backend.models import Odds

    def _seed_game(game_id: int, home_id: int, away_id: int):
        home = Team(id=home_id, name=f"H{home_id}", abbreviation=f"H{home_id}", sport="nba")
        away = Team(id=away_id, name=f"A{away_id}", abbreviation=f"A{away_id}", sport="nba")
        db_session.add_all([home, away])
        db_session.flush()
        g = Game(id=game_id, sport="nba", season="2026", date=date(2026, 3, 1),
                  home_team_id=home_id, away_team_id=away_id, status="scheduled")
        db_session.add(g)
        db_session.flush()
        db_session.add(Odds(game_id=game_id, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
            spread_home=0.0, spread_away=0.0, over_under=0.0,
            timestamp=datetime(2026, 3, 1, 18, 0)))

    Base.metadata.create_all(db_engine)
    _seed_game(1, 1, 2)
    _seed_game(2, 3, 4)
    _seed_game(3, 5, 6)
    s = StrategyModel(id=1, name="value_only", config_json='{"min_edge": 0.1}', is_active=True)
    db_session.add(s)
    db_session.commit()

    real_predict = ValueOnlyStrategy.predict

    def _predict_raising_on_game_2(self, game):
        if game.game_id == 2:
            raise ValueError("simulated bad game (e.g. invalid odds)")
        return real_predict(self, game)

    monkeypatch.setattr(ValueOnlyStrategy, "predict", _predict_raising_on_game_2)

    count = generate_and_store_picks(db_session, strategy_id=1, target_date=date(2026, 3, 1))

    picks = db_session.query(PickModel).all()
    game_ids_with_picks = {p.game_id for p in picks}
    assert 2 not in game_ids_with_picks, "the failing game should not have produced a pick"
    assert 1 in game_ids_with_picks or 3 in game_ids_with_picks, (
        "games 1 and 3 should still have produced picks despite game 2 raising"
    )
    assert count == len(picks)


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


def test_pick_generator_routes_mma_to_combat_even_when_strategy_is_sport_specific():
    """An MMA game must use CombatSportsStrategy even if the active strategy
    in the DB is named 'sport_specific'. Otherwise team-based strategies
    silently produce no picks for combat events."""
    from datetime import date as _date, datetime, timezone
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, Odds, EloRating, StrategyModel, PickModel
    from backend.pipeline.pick_generator import generate_and_store_picks

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="Fighter A", abbreviation="A", sport="mma")
    away = Team(id=2, name="Fighter B", abbreviation="B", sport="mma")
    session.add_all([home, away]); session.flush()
    session.add_all([
        EloRating(team_id=1, sport="mma", rating=1500),
        EloRating(team_id=2, sport="mma", rating=1800),  # B much stronger
    ])
    session.flush()
    session.add(Game(id=1, sport="mma", season="2026", date=_date(2026, 4, 29),
                     home_team_id=1, away_team_id=2, status="scheduled"))
    session.add(Odds(game_id=1, bookmaker="dk", moneyline_home=+200, moneyline_away=-250,
                     spread_home=0.0, spread_away=0.0, over_under=0.0,
                     timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)))
    # The strategy name is "sport_specific" — the team-sport strategy. The dispatch
    # must still route the MMA game to CombatSportsStrategy.
    session.add(StrategyModel(id=1, name="sport_specific", config_json='{"min_edge": 2.0}', is_active=True))
    session.commit()

    n = generate_and_store_picks(session, strategy_id=1, target_date=_date(2026, 4, 29))
    picks = session.query(PickModel).filter(PickModel.game_id == 1).all()
    # Must have at least one pick; must be moneyline (combat doesn't emit spreads/totals).
    # If routing failed (i.e., it ran SportSpecificStrategy on empty TeamStats), no picks would generate.
    assert n >= 1 or len(picks) >= 1, "Combat dispatch must work even with non-combat strategy name"
    assert all(p.pick_type == "moneyline" for p in picks)


def test_recalibrated_threshold_changes_confidence_tier():
    """Part B load-bearing test: a CalibrationHistory row must reach
    calculate_confidence through pick_generator -> Strategy.thresholds, and
    demonstrably move the tier of a generated pick.

    Two structurally identical games (same team stats, elo, odds) are run
    through the same "value_only" strategy on different dates, so each gets
    its own pick without interference. The first run happens before any
    CalibrationHistory rows exist (baseline tier, from DEFAULT_THRESHOLDS).
    The second run happens after inserting a CalibrationHistory row that
    lowers the threshold for the tier just above the baseline down to (at
    most) the observed edge_pct, which -- if and only if the wiring works --
    must bump the second game's pick to that higher tier.
    """
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, TeamStat, EloRating, Odds, StrategyModel, PickModel, CalibrationHistory
    from backend.pipeline.pick_generator import generate_and_store_picks
    from backend.analysis.confidence import DEFAULT_THRESHOLDS
    from datetime import date as _date, datetime as _datetime, timezone as _timezone

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    def _seed_game(game_id: int, home_id: int, away_id: int, game_date):
        home = Team(id=home_id, name=f"H{home_id}", abbreviation=f"H{home_id}", sport="nba")
        away = Team(id=away_id, name=f"A{away_id}", abbreviation=f"A{away_id}", sport="nba")
        session.add_all([home, away])
        session.flush()
        game = Game(id=game_id, sport="nba", season="2026", date=game_date,
                    home_team_id=home_id, away_team_id=away_id, status="scheduled")
        session.add(game)
        session.flush()
        session.add_all([
            TeamStat(team_id=home_id, game_id=game_id, stat_type="point_diff", value=2.0),
            TeamStat(team_id=away_id, game_id=game_id, stat_type="point_diff", value=0.0),
            TeamStat(team_id=home_id, game_id=game_id, stat_type="offensive_rating", value=103.0),
            TeamStat(team_id=home_id, game_id=game_id, stat_type="defensive_rating", value=100.0),
            TeamStat(team_id=away_id, game_id=game_id, stat_type="offensive_rating", value=100.0),
            TeamStat(team_id=away_id, game_id=game_id, stat_type="defensive_rating", value=100.0),
            EloRating(team_id=home_id, sport="nba", rating=1530.0),
            EloRating(team_id=away_id, sport="nba", rating=1500.0),
            Odds(game_id=game_id, bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                 spread_home=0.0, spread_away=0.0, over_under=0.0,
                 timestamp=_datetime(2026, 3, 1, 18, 0, tzinfo=_timezone.utc)),
        ])

    date1 = _date(2026, 3, 1)
    date2 = _date(2026, 3, 2)
    _seed_game(1, 1, 2, date1)
    _seed_game(2, 3, 4, date2)
    session.add(StrategyModel(id=1, name="value_only", config_json='{"min_edge": 0.1}', is_active=True))
    session.commit()

    # Baseline run: no CalibrationHistory rows -> DEFAULT_THRESHOLDS apply.
    n1 = generate_and_store_picks(session, strategy_id=1, target_date=date1)
    assert n1 >= 1
    pick1 = session.query(PickModel).filter(PickModel.game_id == 1).first()
    assert pick1 is not None
    tier_before = pick1.confidence
    edge = pick1.edge_pct

    higher_tiers = sorted(t for t in DEFAULT_THRESHOLDS if t > tier_before)
    assert higher_tiers, (
        f"test setup produced edge_pct={edge} at the top tier ({tier_before}); "
        "cannot demonstrate an increase"
    )
    target_tier = higher_tiers[0]

    # pick1.edge_pct is rounded to 1 decimal for storage; the underlying edge
    # calculate_confidence actually compares against is unrounded, so use a
    # threshold a bit below the displayed value to guarantee the comparison
    # still holds regardless of rounding direction.
    session.add(CalibrationHistory(
        date=date1, sport="nba", confidence_tier=target_tier,
        predicted_win_rate=0.6, actual_win_rate=0.6, sample_size=50,
        old_threshold=DEFAULT_THRESHOLDS[target_tier], new_threshold=edge - 0.1,
    ))
    session.commit()

    # Second run: identical inputs, but the recalibrated threshold is now in play.
    n2 = generate_and_store_picks(session, strategy_id=1, target_date=date2)
    assert n2 >= 1
    pick2 = session.query(PickModel).filter(PickModel.game_id == 2).first()
    assert pick2 is not None

    assert pick2.confidence == target_tier
    assert pick2.confidence > tier_before, (
        "Inserting a CalibrationHistory row did not change the resulting pick's "
        "confidence tier -- thresholds are not reaching calculate_confidence."
    )
