"""Odds must attach to the game they were priced for.

`_find_game_by_teams` ordered candidates by date descending and took the
first, ignoring the event's commence_time entirely. In a series -- the same
two teams on consecutive days, which is the normal case in baseball --
tonight's prices were written onto tomorrow's fixture.

Measured in production on 2026-09-19: of 15 mlb games, the 10 whose opponents
also played on the 20th got zero odds, and their prices landed on the 20th's
rows instead. The 5 that matched were exactly the pairs with no game the next
day. Nothing logged, because `_store_odds` skipped an unmatched event with a
bare `continue`.

Two harms, not one: tonight's game cannot be priced, and tomorrow's game
carries a price that is not its own.
"""
import datetime
import logging

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.pipeline.full_pipeline import _find_game_by_teams, _store_odds

D19 = datetime.date(2026, 9, 19)
D20 = datetime.date(2026, 9, 20)


@pytest.fixture()
def session(tmp_path):
    s = get_session(get_engine(str(tmp_path / "m.db")))
    Base.metadata.create_all(s.get_bind())
    s.add_all([
        Team(id=1, name="Cleveland Guardians", abbreviation="CLE", sport="mlb"),
        Team(id=2, name="Athletics", abbreviation="ATH", sport="mlb"),
    ])
    s.flush()
    s.add_all([
        Game(id=10, sport="mlb", season="2026", date=D19, status="scheduled",
             home_team_id=1, away_team_id=2,
             start_time=datetime.datetime(2026, 9, 19, 22, 10)),
        Game(id=11, sport="mlb", season="2026", date=D20, status="scheduled",
             home_team_id=1, away_team_id=2,
             start_time=datetime.datetime(2026, 9, 20, 22, 10)),
    ])
    s.commit()
    return s


def _when(dt):
    return dt.replace(tzinfo=datetime.timezone.utc)


def test_tonights_event_matches_tonights_game_not_tomorrows(session):
    """The regression. Ordering by date desc always picked id=11."""
    g = _find_game_by_teams(session, "mlb", "Cleveland Guardians", "Athletics",
                            when=_when(datetime.datetime(2026, 9, 19, 22, 10)))
    assert g is not None and g.id == 10


def test_tomorrows_event_matches_tomorrows_game(session):
    g = _find_game_by_teams(session, "mlb", "Cleveland Guardians", "Athletics",
                            when=_when(datetime.datetime(2026, 9, 20, 22, 10)))
    assert g is not None and g.id == 11


def test_an_event_far_from_every_candidate_matches_nothing(session):
    """Better no price than a price belonging to a different fixture."""
    g = _find_game_by_teams(session, "mlb", "Cleveland Guardians", "Athletics",
                            when=_when(datetime.datetime(2026, 9, 25, 22, 10)))
    assert g is None


def test_a_small_start_time_drift_still_matches(session):
    """Books and ESPN disagree by minutes; that must not break matching."""
    g = _find_game_by_teams(session, "mlb", "Cleveland Guardians", "Athletics",
                            when=_when(datetime.datetime(2026, 9, 19, 22, 40)))
    assert g is not None and g.id == 10


def test_without_a_time_the_old_latest_behaviour_is_kept(session):
    """Callers that have no commence_time keep working."""
    g = _find_game_by_teams(session, "mlb", "Cleveland Guardians", "Athletics")
    assert g is not None and g.id == 11


def test_a_game_with_no_start_time_matches_on_its_date(session):
    """ncaaf and others can lack start_time; date is then the only signal."""
    session.add(Team(id=3, name="X", abbreviation="X", sport="ncaaf"))
    session.add(Team(id=4, name="Y", abbreviation="Y", sport="ncaaf"))
    session.flush()
    session.add(Game(id=20, sport="ncaaf", season="2026", date=D19,
                     status="scheduled", home_team_id=3, away_team_id=4))
    session.commit()
    g = _find_game_by_teams(session, "ncaaf", "X", "Y",
                            when=_when(datetime.datetime(2026, 9, 19, 23, 0)))
    assert g is not None and g.id == 20


def test_a_team_named_only_by_an_alias_still_matches(session):
    """The lookup must use the same identity rules as game creation.

    `_ensure_game_from_odds` resolves through team_identity; this path used
    raw equality, so the two could disagree about the same label.
    """
    session.add(Team(id=5, name="Sam Houston Bearkats",
                     abbreviation="SHSU", sport="ncaaf"))
    session.add(Team(id=6, name="App State Mountaineers",
                     abbreviation="APP", sport="ncaaf"))
    session.flush()
    session.add(Game(id=21, sport="ncaaf", season="2026", date=D19,
                     status="scheduled", home_team_id=5, away_team_id=6))
    session.commit()
    g = _find_game_by_teams(
        session, "ncaaf",
        "Sam Houston State Bearkats",        # the Odds API spelling
        "Appalachian State Mountaineers",
        when=_when(datetime.datetime(2026, 9, 19, 23, 0)))
    assert g is not None and g.id == 21


def test_an_unmatched_event_is_reported(session, caplog):
    """A bare `continue` is how this hid for months."""
    events = [{
        "home_team": "Cleveland Guardians", "away_team": "Athletics",
        "commence_time": "2026-09-25T22:10:00Z",
        "bookmakers": [{"key": "bk", "moneyline_home": -120,
                        "moneyline_away": 100, "spread_home": -1.5,
                        "spread_away": 1.5, "over_under": 8.5}],
    }]
    with caplog.at_level(logging.WARNING):
        stored = _store_odds(session, "mlb", events)
    assert stored == 0
    assert "Cleveland Guardians" in caplog.text


def test_store_odds_writes_to_the_dated_game(session):
    events = [{
        "home_team": "Cleveland Guardians", "away_team": "Athletics",
        "commence_time": "2026-09-19T22:10:00Z",
        "bookmakers": [{"key": "bk", "moneyline_home": -120,
                        "moneyline_away": 100, "spread_home": -1.5,
                        "spread_away": 1.5, "over_under": 8.5}],
    }]
    # skip_started=False because this file is about WHICH game a price
    # attaches to, not whether it was placeable. These fixtures are dated in
    # the past on purpose -- they reproduce a specific production slate --
    # and the started-game guard would drop every event before the matching
    # under test ever ran.
    _store_odds(session, "mlb", events, skip_started=False)
    from backend.models import Odds
    rows = session.query(Odds).all()
    assert len(rows) == 1
    assert rows[0].game_id == 10, "priced tomorrow's game instead of tonight's"


def test_an_exact_start_time_beats_a_date_only_candidate(session):
    """A precise match must outrank a guess, not merely tie it.

    Games created from the Odds API have no start_time; those from ESPN do.
    A late game's UTC timestamp lands on the next calendar day, so a
    date-only candidate dated that day scores a perfect zero on the
    date comparison -- tying the game that actually starts at that instant.
    Ordering by date desc then handed the tie to the imprecise one.

    Production, 2026-09-19: the 00:10Z ARI/NYY event matched game 1890
    (dated the 20th, no start_time) instead of 1818, which started at
    exactly 00:10Z.
    """
    session.add(Game(id=30, sport="mlb", season="2026", date=D19,
                     status="scheduled", home_team_id=1, away_team_id=2,
                     start_time=datetime.datetime(2026, 9, 20, 0, 10)))
    # Same pair, dated the 20th, created from odds so it has no start_time.
    session.add(Game(id=31, sport="mlb", season="2026", date=D20,
                     status="scheduled", home_team_id=1, away_team_id=2))
    session.commit()

    g = _find_game_by_teams(session, "mlb", "Cleveland Guardians", "Athletics",
                            when=_when(datetime.datetime(2026, 9, 20, 0, 10)))
    assert g is not None
    assert g.id == 30, f"took the date-only candidate {g.id} over the exact match"


def test_a_date_only_candidate_is_still_used_when_it_is_the_only_one(session):
    """The preference is a tiebreak, not a requirement for start_time."""
    session.add(Team(id=7, name="P", abbreviation="P", sport="mlb"))
    session.add(Team(id=8, name="Q", abbreviation="Q", sport="mlb"))
    session.flush()
    session.add(Game(id=32, sport="mlb", season="2026", date=D20,
                     status="scheduled", home_team_id=7, away_team_id=8))
    session.commit()

    g = _find_game_by_teams(session, "mlb", "P", "Q",
                            when=_when(datetime.datetime(2026, 9, 20, 0, 10)))
    assert g is not None and g.id == 32


# --------------------------------------------------------------------------
# An expected drop is not a warning.
#
# The unmatched-event warning exists because odds used to be dropped in
# silence. But once futures markets are deliberately skipped, their prices
# legitimately have no fixture, and they fired the warning on every run --
# ten lines per scout for boxing and mma alone. A warning that always fires
# stops being read, which would undo the reason it was added.
#
# Downgrading all of them would hide a real drop. The two cases are
# separated instead.
# --------------------------------------------------------------------------

def _speculative_event(home, away, when="2026-12-31T22:57:00Z"):
    return {"home_team": home, "away_team": away, "commence_time": when,
            "bookmakers": [{"key": "bk", "moneyline_home": -120,
                            "moneyline_away": 100, "spread_home": None,
                            "spread_away": None, "over_under": None}]}


def test_a_deliberately_skipped_event_logs_at_info(session, caplog):
    """Its prices have no fixture because we chose not to create one."""
    events = [_speculative_event("Joshua", "Fury"),
              _speculative_event("Joshua", "Dubois")]
    with caplog.at_level(logging.DEBUG):
        _store_odds(session, "boxing", events, skipped=frozenset(
            {("2026-12-31", "Joshua"), ("2026-12-31", "Fury"),
             ("2026-12-31", "Dubois")}))

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "Joshua" in caplog.text, "it still says something"


def test_an_unexpected_drop_is_still_a_warning(session, caplog):
    """The case the warning was added for must keep shouting."""
    events = [{
        "home_team": "Cleveland Guardians", "away_team": "Athletics",
        "commence_time": "2026-09-25T22:10:00Z",
        "bookmakers": [{"key": "bk", "moneyline_home": -120,
                        "moneyline_away": 100, "spread_home": -1.5,
                        "spread_away": 1.5, "over_under": 8.5}],
    }]
    with caplog.at_level(logging.WARNING):
        _store_odds(session, "mlb", events)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "Cleveland Guardians" in caplog.text


def test_skipping_is_keyed_on_the_event_not_the_sport(session, caplog):
    """A skipped name must not mute an unrelated event on the same run."""
    events = [
        _speculative_event("Joshua", "Fury"),
        {"home_team": "Cleveland Guardians", "away_team": "Athletics",
         "commence_time": "2026-09-25T22:10:00Z",
         "bookmakers": [{"key": "bk", "moneyline_home": -120,
                         "moneyline_away": 100, "spread_home": -1.5,
                         "spread_away": 1.5, "over_under": 8.5}]},
    ]
    with caplog.at_level(logging.WARNING):
        _store_odds(session, "boxing", events, skipped=frozenset(
            {("2026-12-31", "Joshua"), ("2026-12-31", "Fury")}))

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "Cleveland Guardians" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_the_skipped_set_reaches_store_odds(session, monkeypatch):
    """The wiring, not just the helper.

    _store_odds only knows a drop was expected because fetch_and_store_odds
    passes it the same set it skipped. A mutation removing that argument
    left every direct-call test green, which is how a wiring gap hides.
    """
    import logging as _logging

    import backend.pipeline.full_pipeline as fp

    events = [_speculative_event("Joshua", "Fury"),
              _speculative_event("Joshua", "Dubois")]

    class _Collector:
        requests_remaining = 100
        def __init__(self, key): pass
        async def fetch_odds(self, sport): return events
        async def close(self): return None

    monkeypatch.setattr(fp, "OddsAPICollector", _Collector)
    monkeypatch.setattr(fp, "record_api_call", lambda *a, **k: None)

    seen = {}
    real = fp._store_odds
    monkeypatch.setattr(fp, "_store_odds",
                        lambda s, sport, data, skipped=None:
                        seen.setdefault("skipped", skipped) or 0)

    await fp.fetch_and_store_odds(session, ["boxing"], "key")

    assert seen["skipped"], "fetch_and_store_odds did not pass the skipped set"
    assert ("2026-12-31", "Joshua") in seen["skipped"]
