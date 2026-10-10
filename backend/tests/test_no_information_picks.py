"""A pick at exactly the no-information 0.5 is tracked, not published.

Owner, 2026-10-09: 23 of 24 published MMA picks since 10-01 had model_prob
0.5 -- the model knew nothing about those fights, so every fighter priced
under even money read as a big "edge" and went out in the email. Until the
investigation (docs/FINDINGS.md) says why, such picks are stored as
tracking-only: kept for the record, never on the board or in the email.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

import backend.pipeline.pick_generator as pg
from backend.data_types import Pick
from backend.models import Base, EloRating, EmailedPick, Game, Odds, PickModel, StrategyModel, Team
from backend.scripts import demote_no_information_picks as demote

DAY = date(2026, 3, 1)


def _seed(session):
    session.add_all([Team(id=1, name="H", abbreviation="H1", sport="nba"),
                     Team(id=2, name="A", abbreviation="A1", sport="nba")])
    session.flush()
    session.add(Game(id=1, sport="nba", season="2025-26", date=DAY, home_team_id=1,
                     away_team_id=2, status="scheduled"))
    session.flush()
    session.add_all([
        EloRating(team_id=1, sport="nba", rating=1500),
        EloRating(team_id=2, sport="nba", rating=1500),
        Odds(game_id=1, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
             spread_home=0.0, spread_away=0.0, over_under=0.0,
             timestamp=datetime(2026, 3, 1, 18, 0)),
        StrategyModel(id=1, name="value_only", config_json="{}", is_active=True),
    ])
    session.commit()


def _fake_strategy(prob):
    class Fake:
        def __init__(self, name, config, sport_thresholds):
            pass

        def predict(self, game_data):
            return [Pick(game_id=1, pick_type="moneyline", pick_value="AWAY ML", confidence=3,
                         edge_pct=19.0, model_probability=prob, implied_probability=0.42,
                         odds_at_pick=130)]
    return Fake


@pytest.mark.parametrize("prob, tracked", [(0.5, True), (0.55, False), (0.4999, False)])
def test_a_pick_at_exactly_one_half_is_stored_as_tracking_only(db_engine, db_session, monkeypatch,
                                                                prob, tracked):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    monkeypatch.setitem(pg.STRATEGY_MAP, "value_only", _fake_strategy(prob))
    pg.generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)
    pick = db_session.query(PickModel).one()
    assert pick.tracking_only is tracked


def test_the_demote_script_moves_only_published_unstarted_one_half_picks(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    now = datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc)
    db_session.add_all([Team(id=1, name="H", abbreviation="H", sport="mma"),
                        Team(id=2, name="A", abbreviation="A", sport="mma"),
                        StrategyModel(id=1, name="x", config_json="{}")])
    db_session.flush()
    future = Game(id=1, sport="mma", season="2026", date=date(2026, 10, 10), status="scheduled",
                  home_team_id=1, away_team_id=2)
    started = Game(id=2, sport="mma", season="2026", date=date(2026, 10, 9), status="scheduled",
                   home_team_id=1, away_team_id=2,
                   start_time=(now - timedelta(hours=1)).replace(tzinfo=None))
    done = Game(id=3, sport="mma", season="2026", date=date(2026, 10, 3), status="final",
                home_team_id=1, away_team_id=2)
    db_session.add_all([future, started, done])
    db_session.flush()

    def pick(gid, prob, **kw):
        p = PickModel(game_id=gid, strategy_id=1, pick_type=kw.pop("pick_type", "moneyline"),
                      pick_value="AWAY ML", confidence=3, edge_pct=19.0, odds_at_pick=130,
                      model_prob=prob, **kw)
        db_session.add(p)
        return p

    target = pick(1, 0.5)
    informed = pick(1, 0.61, pick_type="spread")  # has information -- but MMA (owner, 10-10)
    pick(2, 0.5, pick_type="spread")              # game started
    pick(3, 0.5, pick_type="spread")              # graded history stays as emailed
    pick(1, 0.5, pick_type="over_under", tracking_only=True)   # already tracked
    db_session.commit()
    assert [p.id for p in demote.candidates(db_session, now)] == [target.id, informed.id]


def test_an_emailed_pick_stays_published_when_a_refresh_finds_no_information(db_engine, db_session,
                                                                             monkeypatch):
    # An emailed pick was advice people acted on (withdraw_pick refuses it for
    # the same reason): the public record keeps it whatever a refresh finds.
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    p = PickModel(game_id=1, strategy_id=1, pick_type="moneyline", pick_value="AWAY ML",
                  confidence=3, edge_pct=10.0, odds_at_pick=130, model_prob=0.55)
    db_session.add(p)
    db_session.flush()
    db_session.add(EmailedPick(digest_date=DAY, pick_id=p.id, game_id=1, sport="nba",
                               pick_type="moneyline", pick_value="AWAY ML", odds=130))
    db_session.commit()
    monkeypatch.setitem(pg.STRATEGY_MAP, "value_only", _fake_strategy(0.5))
    pg.generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)
    db_session.expire_all()
    pick = db_session.get(PickModel, p.id)
    assert pick.model_prob == 0.5            # refreshed...
    assert pick.tracking_only is False       # ...but still on the public record


def test_the_demote_script_skips_emailed_picks_and_apply_unpublishes(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    now = datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc)
    db_session.add_all([Team(id=1, name="H", abbreviation="H", sport="mma"),
                        Team(id=2, name="A", abbreviation="A", sport="mma"),
                        StrategyModel(id=1, name="x", config_json="{}")])
    db_session.flush()
    db_session.add(Game(id=1, sport="mma", season="2026", date=date(2026, 10, 10),
                        status="scheduled", home_team_id=1, away_team_id=2))
    db_session.flush()
    emailed = PickModel(game_id=1, strategy_id=1, pick_type="moneyline", pick_value="AWAY ML",
                        confidence=3, edge_pct=19.0, odds_at_pick=130, model_prob=0.5)
    quiet = PickModel(game_id=1, strategy_id=1, pick_type="spread", pick_value="AWAY +1.5",
                      confidence=3, edge_pct=19.0, odds_at_pick=130, model_prob=0.5)
    db_session.add_all([emailed, quiet])
    db_session.flush()
    db_session.add(EmailedPick(digest_date=date(2026, 10, 9), pick_id=emailed.id, game_id=1,
                               sport="mma", pick_type="moneyline", pick_value="AWAY ML", odds=130))
    db_session.commit()
    found = demote.candidates(db_session, now)
    assert [p.id for p in found] == [quiet.id]
    demote.apply(db_session, found)
    published = {p.id for p in db_session.query(PickModel).filter(PickModel.published())}
    assert published == {emailed.id}


def test_every_mma_pick_is_tracking_only_for_now(db_engine, db_session, monkeypatch):
    # Owner, 2026-10-10: after the UFC history import the combat model still
    # backs every underdog (docs/FINDINGS.md), so MMA picks are kept for the
    # record but not published until it is recalibrated.
    Base.metadata.create_all(db_engine)
    db_session.add_all([Team(id=1, name="F One", abbreviation="F One", sport="mma"),
                        Team(id=2, name="F Two", abbreviation="F Two", sport="mma")])
    db_session.flush()
    db_session.add(Game(id=1, sport="mma", season="2026", date=DAY, home_team_id=1,
                        away_team_id=2, status="scheduled"))
    db_session.flush()
    db_session.add_all([
        Odds(game_id=1, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
             spread_home=0.0, spread_away=0.0, over_under=0.0,
             timestamp=datetime(2026, 3, 1, 18, 0)),
        StrategyModel(id=1, name="combat_sports", config_json="{}", is_active=True),
    ])
    db_session.commit()
    monkeypatch.setattr(pg, "CombatSportsStrategy", _fake_strategy(0.6))
    pg.generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)
    assert db_session.query(PickModel).one().tracking_only is True


def test_every_boxing_pick_is_tracking_only_for_now(db_engine, db_session, monkeypatch):
    # Owner, 2026-10-10: boxing uses the uncalibrated blend that backed every
    # MMA underdog; its picks are recorded, not published, until measured.
    Base.metadata.create_all(db_engine)
    db_session.add_all([Team(id=1, name="B One", abbreviation="B One", sport="boxing"),
                        Team(id=2, name="B Two", abbreviation="B Two", sport="boxing")])
    db_session.flush()
    db_session.add(Game(id=1, sport="boxing", season="2026", date=DAY, home_team_id=1,
                        away_team_id=2, status="scheduled"))
    db_session.flush()
    db_session.add_all([
        Odds(game_id=1, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
             spread_home=0.0, spread_away=0.0, over_under=0.0,
             timestamp=datetime(2026, 3, 1, 18, 0)),
        StrategyModel(id=1, name="combat_sports", config_json="{}", is_active=True),
    ])
    db_session.commit()
    monkeypatch.setattr(pg, "CombatSportsStrategy", _fake_strategy(0.6))
    pg.generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)
    assert db_session.query(PickModel).one().tracking_only is True
