"""emailed_picks gains confidence; only the 2026-09-28 rows are backfilled.

Those five were verified unchanged since the send before they were recorded.
A later row's pick may have been refreshed after its email, so copying the
pick's CURRENT stars into it would be a guess.
"""
from datetime import date

from sqlalchemy import create_engine, text

from backend.database import get_session, migrate_emailed_pick_confidence
from backend.models import Base, EmailedPick, Game, PickModel, StrategyModel, Team


def _old_shape():
    """Rows written through the models (so every NOT NULL default applies),
    then the column dropped -- the shape of a database from before Task 2."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add(StrategyModel(id=1, name="x", config_json="{}", is_active=True))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026", date=date(2026, 9, 28),
               home_team_id=1, away_team_id=2, status="scheduled"))
    s.flush()
    for pid, day in ((1, date(2026, 9, 28)), (2, date(2026, 9, 29))):
        s.add(PickModel(id=pid, game_id=1, strategy_id=1, pick_type="prop",
                        pick_value="v", confidence=5, edge_pct=1.0, odds_at_pick=-110))
        s.flush()
        s.add(EmailedPick(digest_date=day, pick_id=pid, game_id=1, sport="nfl",
                          pick_type="prop", pick_value="v", odds=-110))
    s.commit()
    s.close()
    with engine.begin() as c:
        c.execute(text("ALTER TABLE emailed_picks DROP COLUMN confidence"))
    return engine


def test_adds_the_column_and_backfills_only_the_verified_day():
    engine = _old_shape()
    migrate_emailed_pick_confidence(engine)
    with engine.connect() as c:
        rows = dict(c.execute(text("SELECT digest_date, confidence FROM emailed_picks")).all())
    assert rows == {"2026-09-28": 5, "2026-09-29": None}


def test_is_idempotent():
    engine = _old_shape()
    migrate_emailed_pick_confidence(engine)
    migrate_emailed_pick_confidence(engine)
