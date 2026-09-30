"""End-to-end combat-sports smoke test: fighters + Elo + history + odds → moneyline pick."""
import logging
from datetime import date as _date, datetime, timezone

from backend.database import get_engine, get_session
from backend.models import (Base, Team, Game, Odds, EloRating, StrategyModel,
                            PickModel, PickResult, PaperPick, UserProfile)
from backend.data_types import Pick
from backend.pipeline import pick_generator
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.pipeline.scheduler import grade_pending_picks
from backend.pipeline.paper_settlement import grade_paper_picks


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

        # Fixed historical date; skip_started is about bettability, which is
        # not what this test is measuring.
        n = generate_and_store_picks(
            session, strategy_id=1, target_date=_date(2026, 4, 29),
            skip_started=False,
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
            skip_started=False,
        )
        picks = session.query(PickModel).filter(PickModel.game_id == 42).all()
        assert picks == []
    finally:
        session.close()


# --- Fix 2: totals can never exist for a combat sport -----------------------

def test_generator_drops_a_combat_totals_pick_and_logs_warning(monkeypatch, caplog):
    """Even if some strategy hands back an over_under pick for a combat
    game, generate_and_store_picks must drop it and log a WARNING rather
    than store it.

    CombatSportsStrategy itself never emits one (see
    test_combat_sports_strategy.py), so a test that only exercises the real
    strategy would pass even if this generator-level guard were deleted.
    Monkeypatching predict() proves the guard, not the strategy's restraint,
    which is the point: the brief requires every strategy to be covered, not
    just the one this repo ships.
    """
    def _fake_predict(self, game):
        return [Pick(game_id=game.game_id, pick_type="over_under",
                     pick_value="Over 0", confidence=5, edge_pct=50.0,
                     model_probability=0.99, implied_probability=0.5,
                     odds_at_pick=-110)]

    monkeypatch.setattr(pick_generator.CombatSportsStrategy, "predict", _fake_predict)

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        home = Team(id=1, name="A", abbreviation="A", sport="mma")
        away = Team(id=2, name="B", abbreviation="B", sport="mma")
        session.add_all([home, away])
        session.flush()
        session.add(Game(
            id=77, sport="mma", season="2026", date=_date(2026, 4, 29),
            home_team_id=1, away_team_id=2, status="scheduled",
            start_time=datetime(2026, 4, 29, 22, 0, tzinfo=timezone.utc),
        ))
        session.add_all([
            EloRating(team_id=1, sport="mma", rating=1700.0),
            EloRating(team_id=2, sport="mma", rating=1500.0),
            Odds(game_id=77, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
                spread_home=0.0, spread_away=0.0, over_under=0.0,
                timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)),
            StrategyModel(id=1, name="combat_sports",
                         config_json='{"min_edge": 3.0}', is_active=True),
        ])
        session.commit()

        with caplog.at_level(logging.WARNING):
            n = generate_and_store_picks(
                session, strategy_id=1, target_date=_date(2026, 4, 29),
                skip_started=False)

        assert n == 0
        assert session.query(PickModel).filter(PickModel.game_id == 77).count() == 0
        assert any("over_under" in rec.message and "combat" in rec.message.lower()
                  for rec in caplog.records), \
            "expected a WARNING naming the dropped pick type"
    finally:
        session.close()


# --- Fix 3: combat sports are picked by the combat model, never ensemble ----

def test_combat_routes_to_combat_sports_and_team_sport_routes_to_ensemble():
    """With both strategies present, an mma game's picks carry the
    combat_sports strategy id and an nfl game's carry ensemble's -- even
    though `generate_and_store_picks` was called with ensemble's
    strategy_id. This is also the Fix 3(c) wiring test: the stored pick's
    strategy_id must be the combat row's, reached through the real
    generate_and_store_picks entry point, not a mock of it.
    """
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        fighter_a = Team(id=1, name="Fighter A", abbreviation="FIGHTERA", sport="mma")
        fighter_b = Team(id=2, name="Fighter B", abbreviation="FIGHTERB", sport="mma")
        team_a = Team(id=3, name="Team A", abbreviation="TEAMA", sport="nfl")
        team_b = Team(id=4, name="Team B", abbreviation="TEAMB", sport="nfl")
        session.add_all([fighter_a, fighter_b, team_a, team_b])
        session.flush()

        for i in range(5):
            session.add(Game(
                sport="mma", season="2025", date=_date(2025, 1 + i, 15),
                home_team_id=1, away_team_id=2,
                home_score=1, away_score=0, status="final",
            ))
        session.flush()

        mma_game = Game(
            id=901, sport="mma", season="2026", date=_date(2026, 4, 29),
            home_team_id=1, away_team_id=2, status="scheduled",
            start_time=datetime(2026, 4, 29, 22, 0, tzinfo=timezone.utc),
        )
        nfl_game = Game(
            id=902, sport="nfl", season="2026", date=_date(2026, 4, 29),
            home_team_id=3, away_team_id=4, status="scheduled",
            start_time=datetime(2026, 4, 29, 22, 0, tzinfo=timezone.utc),
        )
        session.add_all([mma_game, nfl_game])
        session.flush()

        session.add_all([
            EloRating(team_id=1, sport="mma", rating=1750.0),
            EloRating(team_id=2, sport="mma", rating=1500.0),
            EloRating(team_id=3, sport="nfl", rating=1750.0),
            EloRating(team_id=4, sport="nfl", rating=1500.0),
            Odds(game_id=901, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
                spread_home=0.0, spread_away=0.0, over_under=0.0,
                timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)),
            Odds(game_id=902, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
                spread_home=-3.0, spread_away=3.0, over_under=45.0,
                spread_home_price=-110, spread_away_price=-110,
                over_price=-110, under_price=-110,
                timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)),
            StrategyModel(id=1, name="ensemble", config_json='{"min_edge": 3.0}', is_active=True),
            StrategyModel(id=2, name="combat_sports", config_json='{"min_edge": 3.0}', is_active=True),
        ])
        session.commit()

        # strategy_id=1 is `ensemble` -- the strategy the scheduler passes
        # for every sport. Combat routing must not depend on the caller
        # passing the combat_sports id.
        generate_and_store_picks(
            session, strategy_id=1, target_date=_date(2026, 4, 29),
            skip_started=False)

        mma_picks = session.query(PickModel).filter(PickModel.game_id == 901).all()
        nfl_picks = session.query(PickModel).filter(PickModel.game_id == 902).all()
        assert mma_picks, "expected at least one mma pick"
        assert all(p.strategy_id == 2 for p in mma_picks), \
            "mma picks must be stored under the combat_sports strategy id"
        assert all(p.pick_type == "moneyline" for p in mma_picks)
        assert nfl_picks, "expected at least one nfl pick"
        assert all(p.strategy_id == 1 for p in nfl_picks), \
            "nfl picks must be stored under ensemble's strategy id"
    finally:
        session.close()


def test_ensure_combat_sports_row_is_idempotent():
    """Fix 3(b): running the ensure-step twice creates exactly one row."""
    from backend.database import run_migrations

    engine = get_engine(":memory:")
    run_migrations(engine)
    run_migrations(engine)
    session = get_session(engine)
    try:
        rows = session.query(StrategyModel).filter(
            StrategyModel.name == "combat_sports").all()
        assert len(rows) == 1
    finally:
        session.close()


# --- Fix round 1, item 1: sport pass-through has real callers wired up -----

def test_grade_pending_picks_voids_a_stored_combat_total_as_push():
    """grade_pending_picks (backend/pipeline/scheduler.py) is the real
    production grading entry for strategy PickModel rows. It must pass the
    game's sport into grade_pick so a stored mma "Over 0" pick against a 1/0
    score settles as a push -- never the phantom win the original bug
    produced."""
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        home = Team(id=1, name="Fighter A", abbreviation="A", sport="mma")
        away = Team(id=2, name="Fighter B", abbreviation="B", sport="mma")
        session.add_all([home, away])
        session.flush()
        session.add(Game(
            id=1, sport="mma", season="2026", date=_date(2026, 3, 21),
            home_team_id=1, away_team_id=2, home_score=1, away_score=0,
            status="final",
        ))
        session.add(StrategyModel(id=1, name="combat_sports",
                                  config_json='{"min_edge": 3.0}', is_active=False))
        session.flush()
        session.add(PickModel(
            id=1, game_id=1, strategy_id=1, pick_type="over_under",
            pick_value="Over 0", confidence=5, edge_pct=50.0, odds_at_pick=-110,
        ))
        session.commit()

        grade_pending_picks(session)

        result = session.query(PickResult).filter_by(pick_id=1).one()
        assert (result.result, result.payout) == ("push", 0.0), \
            "a stored combat total must never be graded a win by the real entry"
    finally:
        session.close()


def test_grade_paper_picks_voids_a_combat_total_as_push():
    """grade_paper_picks (backend/pipeline/paper_settlement.py) is the other
    production grading path -- user-placed paper bets. Same guard, same
    result, through its own real entry point."""
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        home = Team(id=1, name="Fighter A", abbreviation="A", sport="mma")
        away = Team(id=2, name="Fighter B", abbreviation="B", sport="mma")
        session.add_all([home, away, UserProfile(id=1, name="p")])
        session.flush()
        session.add(Game(
            id=1, sport="mma", season="2026", date=_date(2026, 3, 21),
            home_team_id=1, away_team_id=2, home_score=1, away_score=0,
            status="final",
        ))
        session.flush()
        session.add(PaperPick(
            id=1, user_id=1, game_id=1, pick_type="over_under",
            pick_value="Over 0", odds=-110, stake=1.0,
        ))
        session.commit()

        graded = grade_paper_picks(session)

        assert len(graded) == 1
        pick = session.query(PaperPick).filter_by(id=1).one()
        assert (pick.result, pick.payout) == ("push", 0.0), \
            "a stored combat total must never be graded a win by the paper path"
    finally:
        session.close()


# --- Fix round 1, item 2: combat idempotence key ----------------------------

def test_generate_and_store_picks_does_not_duplicate_a_combat_moneyline():
    """Idempotence's `already`-picks lookup must check BOTH the caller's
    strategy_id and combat_sports' id, because a combat pick is stored
    under combat_sports' id regardless of which id the caller passed. If it
    only checked the caller's id, every window run would re-insert the
    same combat moneyline -- the same failure as the 18x prop-duplicate
    incident (backend/scripts/dedupe_prop_picks.py)."""
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        home = Team(id=1, name="Khabib Nurmagomedov", abbreviation="NURMAGOMEDOV", sport="mma")
        away = Team(id=2, name="Conor McGregor", abbreviation="MCGREGOR", sport="mma")
        session.add_all([home, away])
        session.flush()
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
        session.add(upcoming)
        session.flush()
        session.add_all([
            EloRating(team_id=1, sport="mma", rating=1750.0),
            EloRating(team_id=2, sport="mma", rating=1500.0),
            Odds(game_id=999, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
                spread_home=0.0, spread_away=0.0, over_under=0.0,
                timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)),
            StrategyModel(id=1, name="ensemble", config_json='{"min_edge": 3.0}', is_active=True),
            StrategyModel(id=2, name="combat_sports", config_json='{"min_edge": 3.0}', is_active=False),
        ])
        session.commit()

        # Two window runs, same as the scheduler making several passes over
        # one day. strategy_id=1 is ensemble's -- the caller never knows or
        # cares that this game will actually be predicted and stored under
        # combat_sports' id (2).
        first = generate_and_store_picks(
            session, strategy_id=1, target_date=_date(2026, 4, 29), skip_started=False)
        second = generate_and_store_picks(
            session, strategy_id=1, target_date=_date(2026, 4, 29), skip_started=False)

        assert first >= 1
        assert second == 0, "the second run must not re-insert the same pick"
        picks = session.query(PickModel).filter(PickModel.game_id == 999).all()
        assert len(picks) == 1, \
            "exactly one combat moneyline must exist after two runs"
    finally:
        session.close()
