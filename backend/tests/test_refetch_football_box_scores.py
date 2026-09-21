"""Clear football game logs that carry no football statistic, so they refill.

The parser wrote 21,077 football `game_log` rows against basketball's
schema (see `test_espn_box_score_football.py`). Fixing the parser does not
repair them: `collect_box_scores_for_final_games` skips any game that
already has `game_log` rows, so the hollow rows block their own refetch.

That skip is not wrong -- it is what makes collection resumable without
tracking how far it got. The repair therefore restores the invariant the
skip assumes ("rows present means collected") by deleting the rows that
carry nothing, and then lets the existing collector do the work. A second
write path that filled them in place would be a duplicate of a collector
that already handles ids, dates, teams and upserts.

Why a kicker row counts as hollow
---------------------------------
611 of the rows are not literally empty: they hold `points`, matched from
the `kicking` block's `PTS` column. That is a basketball column holding
kicking points -- not a football measurement, and no football market reads
it. Keeping such a row would leave the game permanently skipped, so the
predicate asks whether any FOOTBALL field is populated, not whether the
row is blank.
"""
import datetime

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import Base, PlayerStat, Team
from backend.scripts.refetch_football_box_scores import (FOOTBALL_FIELDS,
                                                         hollow_logs,
                                                         run_on_session)

DAY = datetime.date(2026, 9, 14)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def _team(session, sport="nfl"):
    t = Team(name="Panthers", abbreviation="CAR", sport=sport)
    session.add(t)
    session.flush()
    return t


def _stat(session, team, *, sport="nfl", stat_type="game_log",
          game_date=DAY, **fields):
    s = PlayerStat(player_name=fields.pop("player_name", "A Player"),
                   team_id=team.id, sport=sport, stat_type=stat_type,
                   game_date=game_date, source="espn", **fields)
    session.add(s)
    session.flush()
    return s


def test_a_row_with_no_football_stat_is_hollow(session):
    t = _team(session)
    _stat(session, t)
    session.commit()

    assert len(hollow_logs(session, "nfl")) == 1


def test_a_row_carrying_rushing_yards_is_kept(session):
    t = _team(session)
    _stat(session, t, rush_yards=53.0)
    session.commit()

    assert hollow_logs(session, "nfl") == []


@pytest.mark.parametrize("field", FOOTBALL_FIELDS)
def test_any_single_football_field_is_enough_to_keep_a_row(session, field):
    """A receiver with 0 touchdowns and a back with 0 receiving yards are
    both real measurements. Only the absence of all five is hollow."""
    t = _team(session)
    _stat(session, t, **{field: 0.0})
    session.commit()

    assert hollow_logs(session, "nfl") == []


def test_a_kicker_row_holding_only_points_is_hollow(session):
    """`points` came from the `kicking` block's PTS column. It is not a
    football measurement, and leaving it keeps the game skipped forever."""
    t = _team(session)
    _stat(session, t, player_name="Ryan Fitzgerald", points=10.0)
    session.commit()

    assert len(hollow_logs(session, "nfl")) == 1


def test_season_avg_rows_are_never_touched(session):
    """Only `game_log` came from the broken parser. `season_avg` is written
    by a different collector and populates its football columns correctly."""
    t = _team(session)
    _stat(session, t, stat_type="season_avg", game_date=None)
    session.commit()

    assert hollow_logs(session, "nfl") == []


def test_another_sport_is_never_touched(session):
    """A basketball game_log has no football fields by definition. Applying
    this predicate to nba would delete every row in the table."""
    t = _team(session, sport="nba")
    _stat(session, t, sport="nba", points=11.0, rebounds=4.0)
    session.commit()

    assert hollow_logs(session, "nba") == []
    assert hollow_logs(session, "nfl") == []


def test_dry_run_is_the_default_and_deletes_nothing(session):
    t = _team(session)
    _stat(session, t)
    session.commit()

    summary = run_on_session(session, sports=("nfl",), collect=False)

    assert summary["hollow"] == 1
    assert summary["deleted"] == 0
    assert session.query(PlayerStat).count() == 1


def test_apply_deletes_only_the_hollow_rows(session):
    t = _team(session)
    _stat(session, t, player_name="Hollow Guy")
    _stat(session, t, player_name="Real Guy", rec_yards=101.0)
    session.commit()

    summary = run_on_session(session, sports=("nfl",), apply=True, collect=False)

    assert summary["deleted"] == 1
    remaining = session.query(PlayerStat).all()
    assert [r.player_name for r in remaining] == ["Real Guy"]


def test_the_collector_is_invoked_per_sport_after_the_delete(session, monkeypatch):
    """Deleting alone leaves the history emptier than it found it. The
    refetch is the point; skipping it would be a net loss of data."""
    t = _team(session)
    _stat(session, t)
    session.commit()
    calls = []

    import backend.scripts.refetch_football_box_scores as mod
    monkeypatch.setattr(mod, "collect_box_scores_for_final_games",
                        lambda s, sport=None: calls.append(sport) or 7)

    summary = run_on_session(session, sports=("nfl",), apply=True)

    assert calls == ["nfl"], "the collector must run, and only for this sport"
    assert summary["refetched"] == 7


def test_no_collect_deletes_without_spending_espn_requests(session, monkeypatch):
    """`--no-collect` exists to inspect the gap before committing to one
    request per final game -- 290 of them as of 2026-09-20. Ignoring it
    spends them anyway, against an API that answers 403 when pushed."""
    t = _team(session)
    _stat(session, t)
    session.commit()
    calls = []

    import backend.scripts.refetch_football_box_scores as mod
    monkeypatch.setattr(mod, "collect_box_scores_for_final_games",
                        lambda s, sport=None: calls.append(sport) or 0)

    summary = run_on_session(session, sports=("nfl",), apply=True, collect=False)

    assert summary["deleted"] == 1, "the delete still happens"
    assert calls == [], "but nothing is fetched"


def test_a_dry_run_never_calls_the_collector(session, monkeypatch):
    """The collector commits internally, so calling it during a dry run
    would write despite the name."""
    t = _team(session)
    _stat(session, t)
    session.commit()
    calls = []

    import backend.scripts.refetch_football_box_scores as mod
    monkeypatch.setattr(mod, "collect_box_scores_for_final_games",
                        lambda s, sport=None: calls.append(sport) or 0)

    run_on_session(session, sports=("nfl",))

    assert calls == []
