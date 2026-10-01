"""One `Odds` row per (game, bookmaker).

Nothing enforced it, and both game-merge scripts reparented a dropped
game's rows onto the survivor without asking whether the survivor already
had that book. The live db held 6 such pairs (12 rows) on game 1018. The
collector's `.first()` kept updating one row of each pair and froze the
other, and every consensus over a game's rows counted that book twice.

The newer row wins everywhere: it is the price the collector wrote last.
"""
import datetime
import logging

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from backend.database import (ODDS_UNIQUE_INDEX, get_engine, get_session,
                              migrate_odds_one_row_per_book)
from backend.models import Base, Game, Odds, PickModel, StrategyModel, Team
from backend.pipeline.odds_rows import drop_odds_collisions, stale_duplicates
from backend.scripts import dedupe_odds_rows
from backend.scripts.dedupe_combat_games import merge_date_splits
from backend.scripts.merge_duplicate_games import run as merge_twins

OLD = datetime.datetime(2026, 3, 15, 18, 15)
NEW = datetime.datetime(2026, 3, 15, 19, 28)


def _legacy_engine(path=None):
    """A database as it was before the index: same schema, no rule."""
    engine = create_engine(f"sqlite:///{path}" if path else "sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(f"DROP INDEX {ODDS_UNIQUE_INDEX}"))
    return engine


def _has_index(engine) -> bool:
    return any(ix["name"] == ODDS_UNIQUE_INDEX
               for ix in inspect(engine).get_indexes("odds"))


def _seed_game(session, gid=1, sport="nba"):
    session.add_all([Team(id=gid * 10 + 1, name=f"H{gid}", abbreviation=f"H{gid}", sport=sport),
                     Team(id=gid * 10 + 2, name=f"A{gid}", abbreviation=f"A{gid}", sport=sport)])
    session.add(Game(id=gid, sport=sport, season="2026", date=datetime.date(2026, 3, 15),
                     home_team_id=gid * 10 + 1, away_team_id=gid * 10 + 2, status="final"))
    session.flush()


def _odds(game_id, book, ts, ml=-110, **kw):
    return Odds(game_id=game_id, bookmaker=book, timestamp=ts,
                moneyline_home=ml, moneyline_away=-ml, **kw)


# --- the rule itself ----------------------------------------------------

def test_the_database_refuses_a_second_row_for_one_book():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    _seed_game(session)
    session.add(_odds(1, "dk", OLD))
    session.commit()
    session.add(_odds(1, "dk", NEW))
    with pytest.raises(IntegrityError):
        session.commit()


def test_the_same_book_on_two_games_is_fine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    _seed_game(session, 1)
    _seed_game(session, 2)
    session.add_all([_odds(1, "dk", OLD), _odds(2, "dk", OLD)])
    session.commit()


# --- stale_duplicates ---------------------------------------------------

def test_stale_duplicates_keeps_the_newest_row_per_book():
    session = get_session(_legacy_engine())
    _seed_game(session)
    session.add_all([_odds(1, "dk", NEW, ml=-120), _odds(1, "dk", OLD, ml=-150),
                     _odds(1, "fd", OLD), _odds(1, "fd", NEW)])
    session.commit()
    doomed = stale_duplicates(session)
    assert sorted((r.bookmaker, r.timestamp) for r in doomed) == [("dk", OLD), ("fd", OLD)]


def test_a_timestamp_tie_keeps_the_later_insert():
    session = get_session(_legacy_engine())
    _seed_game(session)
    first, second = _odds(1, "dk", OLD), _odds(1, "dk", OLD)
    session.add(first)
    session.flush()
    session.add(second)
    session.commit()
    assert [r.id for r in stale_duplicates(session)] == [first.id]


# --- the migration ------------------------------------------------------

def test_the_migration_builds_the_index_on_a_clean_table():
    engine = _legacy_engine()
    migrate_odds_one_row_per_book(engine)
    assert _has_index(engine)
    migrate_odds_one_row_per_book(engine)   # idempotent
    assert _has_index(engine)


def test_the_migration_never_deletes_and_lets_the_app_start(caplog):
    engine = _legacy_engine()
    session = get_session(engine)
    _seed_game(session)
    session.add_all([_odds(1, "dk", OLD), _odds(1, "dk", NEW)])
    session.commit()
    with caplog.at_level(logging.WARNING):
        migrate_odds_one_row_per_book(engine)
    assert not _has_index(engine)
    assert session.query(Odds).count() == 2
    assert "dedupe_odds_rows" in caplog.text


# --- the cleanup script -------------------------------------------------

def test_cleanup_dry_run_then_apply_then_nothing(tmp_path):
    db = tmp_path / "odds.db"
    engine = _legacy_engine(db)
    session = get_session(engine)
    _seed_game(session)
    session.add_all([_odds(1, "dk", OLD, ml=-150), _odds(1, "dk", NEW, ml=-120),
                     _odds(1, "fd", NEW)])
    session.commit()
    session.close()
    engine.dispose()

    dry = dedupe_odds_rows.run(str(db))
    assert dry["deleted"] == 1 and dry["by_game"] == {1: 1}
    assert dry["index_present"] is False

    applied = dedupe_odds_rows.run(str(db), apply=True)
    assert applied["deleted"] == 1
    assert applied["index_present"] is True

    again = dedupe_odds_rows.run(str(db), apply=True)
    assert again["deleted"] == 0

    session = get_session(get_engine(str(db)))
    rows = sorted((r.bookmaker, r.moneyline_home) for r in session.query(Odds))
    session.close()
    assert rows == [("dk", -120), ("fd", -110)]


def test_cleanup_refuses_a_missing_db(tmp_path):
    with pytest.raises(FileNotFoundError):
        dedupe_odds_rows.run(str(tmp_path / "nope.db"))


# --- drop_odds_collisions -----------------------------------------------

def test_a_collision_keeps_the_newer_row_on_either_side():
    session = get_session(_legacy_engine())
    _seed_game(session, 1)
    _seed_game(session, 2)
    session.add_all([
        _odds(1, "dk", OLD, ml=-150), _odds(2, "dk", NEW, ml=-120),   # newer on the loser
        _odds(1, "fd", NEW, ml=-130), _odds(2, "fd", OLD, ml=-160),   # newer on the survivor
        _odds(2, "mgm", OLD),                                         # no collision
    ])
    session.commit()
    assert drop_odds_collisions(session, from_game_id=2, to_game_id=1) == 2
    left = sorted((r.game_id, r.bookmaker, r.moneyline_home) for r in session.query(Odds))
    assert left == [(1, "fd", -130), (2, "dk", -120), (2, "mgm", -110)]


def test_a_duplicate_already_on_the_dropped_game_does_not_move_twice():
    session = get_session(_legacy_engine())
    _seed_game(session, 1)
    _seed_game(session, 2)
    session.add_all([_odds(2, "dk", OLD, ml=-150), _odds(2, "dk", NEW, ml=-120)])
    session.commit()
    assert drop_odds_collisions(session, 2, 1) == 1
    assert [(r.bookmaker, r.moneyline_home) for r in session.query(Odds)] == [("dk", -120)]


# --- both merge scripts -------------------------------------------------

def test_merging_espn_twins_leaves_one_row_per_book(tmp_path):
    """The index is live here, so without the collision step the merge
    would not just duplicate -- it would fail on the reparent."""
    db = tmp_path / "merge.db"
    session = get_session(get_engine(str(db)))
    Base.metadata.create_all(session.get_bind())
    session.add_all([Team(id=1, name="MIA", abbreviation="MIA", sport="nba"),
                     Team(id=2, name="ORL", abbreviation="ORL", sport="nba"),
                     StrategyModel(id=1, name="s", config_json="{}")])
    for gid, day in ((1, datetime.date(2026, 3, 14)), (2, datetime.date(2026, 3, 15))):
        session.add(Game(id=gid, sport="nba", season="2025-26", date=day, espn_id="401700001",
                         home_team_id=1, away_team_id=2, status="scheduled"))
    session.flush()
    session.add(PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                          pick_value="HOME ML", confidence=3, edge_pct=4.0, odds_at_pick=-110))
    session.add_all([_odds(1, "dk", OLD, ml=-150), _odds(2, "dk", NEW, ml=-120),
                     _odds(2, "fd", NEW)])
    session.commit()
    session.close()

    merge_twins(str(db))

    session = get_session(get_engine(str(db)))
    rows = sorted((r.game_id, r.bookmaker, r.moneyline_home) for r in session.query(Odds))
    session.close()
    assert rows == [(1, "dk", -120), (1, "fd", -110)]


def test_merging_a_mirrored_combat_split_keeps_the_newer_quote_swapped():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    a = Team(name="Fighter A", abbreviation="Fighter A", sport="mma")
    b = Team(name="Fighter B", abbreviation="Fighter B", sport="mma")
    session.add_all([a, b])
    session.flush()
    day = datetime.date(2026, 6, 14)
    final = Game(sport="mma", season="2026", date=day, status="final",
                 home_team_id=a.id, away_team_id=b.id, home_score=1, away_score=0)
    mirrored = Game(sport="mma", season="2026", date=day + datetime.timedelta(days=1),
                    status="scheduled", home_team_id=b.id, away_team_id=a.id)
    session.add_all([final, mirrored])
    session.flush()
    # dk quotes both rows; the mirrored row's quote is newer and prices
    # Fighter B (its home) at -200.
    session.add_all([_odds(final.id, "dk", OLD, ml=-300),
                     Odds(game_id=mirrored.id, bookmaker="dk", timestamp=NEW,
                          moneyline_home=-200, moneyline_away=170)])
    session.commit()
    final_id = final.id

    summary = merge_date_splits(session, "mma", apply=True)

    assert summary["merged"] == 1 and summary["odds_collisions_dropped"] == 1
    session.expire_all()
    row = session.query(Odds).filter_by(game_id=final_id).one()
    # Newer quote, swapped onto the kept row's fighters: A (home) is +170.
    assert (row.moneyline_home, row.moneyline_away) == (170, -200)


# --- the accepted race: two writers inserting the same new book -------------

def _race_setup(tmp_path, monkeypatch):
    """A scheduled game, a fake feed quoting book "dk" on it, and a second
    writer that commits its own "dk" row between our SELECT and our flush --
    the one race the unique index turns from a silent duplicate into an
    error (owner-accepted trade-off, 2026-09-30)."""
    import asyncio
    import backend.pipeline.full_pipeline as fp
    engine = get_engine(str(tmp_path / "race.db"))
    Base.metadata.create_all(engine)
    session = get_session(engine)
    _seed_game(session, 1, sport="nfl")
    game = session.get(Game, 1)
    game.status = "scheduled"
    game.date = datetime.date.today() + datetime.timedelta(days=3)
    session.commit()

    fetched = []

    class FakeCollector:
        requests_remaining = 100

        def __init__(self, key):
            pass

        async def fetch_odds(self, sport):
            fetched.append(sport)
            if sport != "nfl":
                return []   # only nfl quotes the game, so mlb can't repair it
            return [{"id": "x", "home_team": "H1", "away_team": "A1",
                     "commence_time": f"{game.date.isoformat()}T20:00:00Z",
                     "bookmakers": [{"key": "dk", "moneyline_home": -125,
                                     "moneyline_away": 105, "spread_home": None,
                                     "spread_away": None, "over_under": None}]}]

        async def close(self):
            pass

    real_snapshot = fp.record_snapshot

    def racing_snapshot(sess, gid, book, bk):
        if getattr(racing_snapshot, "done", False):
            return real_snapshot(sess, gid, book, bk)
        racing_snapshot.done = True
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO odds (game_id, bookmaker, timestamp) "
                              "VALUES (1, 'dk', '2026-01-01')"))
        return real_snapshot(sess, gid, book, bk)

    monkeypatch.setattr(fp, "OddsAPICollector", FakeCollector)
    monkeypatch.setattr(fp, "_ensure_game_from_odds", lambda *a, **k: None)
    monkeypatch.setattr(fp, "_find_game_by_teams",
                        lambda sess, sport, h, a, when=None: sess.get(Game, 1))
    monkeypatch.setattr(fp, "record_snapshot", racing_snapshot)
    budget = {"monthly_limit": 10000, "daily_target": 1000, "reserve": 10}
    run = lambda: asyncio.run(fp.fetch_and_store_odds(session, ["nfl", "mlb"], "k",
                                                      budget=budget))
    return session, fetched, run, fp


def test_losing_the_insert_race_updates_the_winners_row(tmp_path, monkeypatch):
    session, fetched, run, _ = _race_setup(tmp_path, monkeypatch)
    run()
    rows = session.query(Odds).filter_by(game_id=1, bookmaker="dk").all()
    # One row, carrying OUR quote: the retry found the winner's row and
    # updated it, which is what the upsert would have done a moment later.
    assert [(r.moneyline_home, r.moneyline_away) for r in rows] == [(-125, 105)]


def test_losing_the_insert_race_does_not_wedge_the_run(tmp_path, monkeypatch):
    """The session is shared by everything after the fetch -- the next
    sport, props, pick generation. A failed flush left it in
    PendingRollbackError and took the whole window run with it."""
    session, fetched, run, fp = _race_setup(tmp_path, monkeypatch)

    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    def locked(mapper, connection, target):
        # A flush error that is not the race, so the retry does not apply
        # -- "database is locked" is the realistic one under WAL.
        raise OperationalError("INSERT INTO odds", {}, Exception("database is locked"))

    event.listen(Odds, "before_insert", locked)
    try:
        run()
    finally:
        event.remove(Odds, "before_insert", locked)
    assert fetched == ["nfl", "mlb"]
    session.query(StrategyModel).first()   # pick generation's first query
