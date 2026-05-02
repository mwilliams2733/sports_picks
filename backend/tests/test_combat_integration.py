"""End-to-end combat-sports smoke test: fighters + Elo + history + odds → moneyline pick."""
from datetime import date as _date, datetime, timezone

from backend.database import get_engine, get_session
from backend.models import Base, Team, Game, Odds, EloRating, StrategyModel, PickModel
from backend.pipeline.pick_generator import generate_and_store_picks


def test_mma_pipeline_generates_pick_for_clear_favorite():
    """Full path: a heavily favored fighter (Elo gap + recent-win history)
    against a mispriced book line should surface a moneyline pick."""
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        home = Team(id=1, name="Khabib Nurmagomedov", abbreviation="NURMAGOMEDOV", sport="mma")
        away = Team(id=2, name="Conor McGregor", abbreviation="MCGREGOR", sport="mma")
        session.add_all([home, away])
        session.flush()

        # Seed 5 prior fights — Khabib (home) wins all 5, populating
        # _build_fighter_stats with recent_form_score=1.0 + opponent_avg_elo
        # from McGregor's pre-fight Elo.
        for i in range(5):
            session.add(Game(
                sport="mma", season="2025", date=_date(2025, 1 + i, 15),
                home_team_id=1, away_team_id=2,
                home_score=1, away_score=0, status="final",
            ))
        session.flush()

        upcoming = Game(
            id=999, sport="mma", season="2026", date=_date(2026, 4, 29),
            home_team_id=1, away_team_id=2, status="scheduled",
            start_time=datetime(2026, 4, 29, 22, 0, tzinfo=timezone.utc),
        )
        session.add(upcoming); session.flush()

        # Khabib has a 250-point Elo gap; book undersells him at -150.
        session.add_all([
            EloRating(team_id=1, sport="mma", rating=1750.0),
            EloRating(team_id=2, sport="mma", rating=1500.0),
            Odds(
                game_id=999, bookmaker="dk",
                moneyline_home=-150, moneyline_away=+130,
                spread_home=0.0, spread_away=0.0, over_under=0.0,
                timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
            ),
            StrategyModel(
                id=1, name="combat_sports",
                config_json='{"min_edge": 3.0}', is_active=True,
            ),
        ])
        session.commit()

        n = generate_and_store_picks(
            session, strategy_id=1, target_date=_date(2026, 4, 29),
        )
        assert n >= 1

        picks = session.query(PickModel).filter(PickModel.game_id == 999).all()
        # No spread or total picks for combat sports — only moneyline.
        assert all(p.pick_type == "moneyline" for p in picks)
        # The favored side (HOME) should be the one picked, given the edge.
        assert any("HOME" in p.pick_value for p in picks)
    finally:
        session.close()


def test_mma_pipeline_skips_when_no_odds_available():
    """End-to-end gate: a scheduled MMA fight with no odds row produces no
    picks. Confirms the strategy's own `if not game.odds: return []` guard
    is reached through the pick-generator's combat routing."""
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        home = Team(id=1, name="A", abbreviation="A", sport="mma")
        away = Team(id=2, name="B", abbreviation="B", sport="mma")
        session.add_all([home, away])
        session.flush()
        session.add(Game(
            id=42, sport="mma", season="2026", date=_date(2026, 4, 29),
            home_team_id=1, away_team_id=2, status="scheduled",
            start_time=datetime(2026, 4, 29, 22, 0, tzinfo=timezone.utc),
        ))
        session.add_all([
            EloRating(team_id=1, sport="mma", rating=1700.0),
            EloRating(team_id=2, sport="mma", rating=1500.0),
            StrategyModel(
                id=1, name="combat_sports",
                config_json='{"min_edge": 3.0}', is_active=True,
            ),
            # NOTE: deliberately no Odds row.
        ])
        session.commit()

        generate_and_store_picks(
            session, strategy_id=1, target_date=_date(2026, 4, 29),
        )
        picks = session.query(PickModel).filter(PickModel.game_id == 42).all()
        assert picks == []
    finally:
        session.close()
