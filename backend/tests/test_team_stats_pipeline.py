"""Tests for the point-in-time team-stat computation (plan 008).

The property under test throughout is *point-in-time-ness*: a value attached
to game G may be derived only from games strictly before G. Several tests here
exist specifically so that relaxing the strictly-before boundary (``<`` to
``<=``) or widening a lookup (dropping ``game_id``) makes them fail.
"""
from datetime import date

import pytest

from backend.models import Base, Team, Game, TeamStat, EloHistory
from backend.pipeline import team_stats as ts


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

class FakeGame:
    """Minimal duck-typed stand-in for a Game row, for the pure functions."""

    def __init__(self, gid, d, home_team_id, away_team_id, home_score, away_score):
        self.id = gid
        self.date = d
        self.home_team_id = home_team_id
        self.away_team_id = away_team_id
        self.home_score = home_score
        self.away_score = away_score
        self.status = "final"


@pytest.fixture
def seeded(db_session):
    Base.metadata.create_all(db_session.get_bind())
    a = Team(name="Alpha", abbreviation="ALP", sport="nba")
    b = Team(name="Beta", abbreviation="BET", sport="nba")
    c = Team(name="Gamma", abbreviation="GAM", sport="nba")
    db_session.add_all([a, b, c])
    db_session.commit()
    return db_session, a, b, c


# --------------------------------------------------------------------------
# Step 2: pure functions
# --------------------------------------------------------------------------

def test_rolling_point_diff_no_prior_games_is_zero():
    assert ts.rolling_point_diff([], team_id=1) == 0.0


def test_rolling_point_diff_fewer_than_lookback():
    games = [
        FakeGame(1, date(2024, 1, 1), 1, 2, 110, 100),   # +10 for team 1
        FakeGame(2, date(2024, 1, 3), 2, 1, 90, 110),    # +20 for team 1
    ]
    assert ts.rolling_point_diff(games, team_id=1, lookback=10) == pytest.approx(15.0)


def test_rolling_point_diff_respects_lookback_window():
    games = [
        FakeGame(1, date(2024, 1, 1), 1, 2, 200, 100),   # +100, should fall out
        FakeGame(2, date(2024, 1, 3), 1, 2, 110, 100),   # +10
        FakeGame(3, date(2024, 1, 5), 1, 2, 120, 100),   # +20
    ]
    assert ts.rolling_point_diff(games, team_id=1, lookback=2) == pytest.approx(15.0)


def test_rest_days_multi_day_gap():
    games = [FakeGame(1, date(2024, 1, 1), 1, 2, 110, 100)]
    assert ts.rest_days(games, team_id=1, game_date=date(2024, 1, 6)) == 5


def test_rest_days_first_game_uses_documented_default():
    assert ts.rest_days([], team_id=1, game_date=date(2024, 1, 1)) == ts.DEFAULT_REST_DAYS


def test_record_splits_counts_home_away_and_last_n():
    games = [
        FakeGame(1, date(2024, 1, 1), 1, 2, 110, 100),   # team1 home win
        FakeGame(2, date(2024, 1, 3), 2, 1, 110, 100),   # team1 away loss
        FakeGame(3, date(2024, 1, 5), 1, 2, 90, 100),    # team1 home loss
        FakeGame(4, date(2024, 1, 7), 2, 1, 90, 100),    # team1 away win
    ]
    s = ts.record_splits(games, team_id=1)
    assert s["home_wins"] == 1
    assert s["home_losses"] == 1
    assert s["away_wins"] == 1
    assert s["away_losses"] == 1
    assert s["last_n_wins"] == 2
    assert s["last_n_losses"] == 2


# --------------------------------------------------------------------------
# Step 2/3: the strictly-before boundary
# --------------------------------------------------------------------------

def test_compute_ignores_a_game_on_the_same_date():
    """A game on the target date must not feed the target game's own features.

    Fails if the boundary filter uses ``<=`` instead of ``<``.
    """
    target_date = date(2024, 1, 10)
    games = [
        FakeGame(1, date(2024, 1, 1), 1, 2, 110, 100),      # +10, prior
        FakeGame(2, target_date, 1, 2, 200, 100),           # +100, the target day
    ]
    stats = ts.compute_team_stats(games, team_id=1, before_date=target_date)
    assert stats["point_diff"] == pytest.approx(10.0)


def test_point_in_time_middle_game_sees_only_the_first():
    """Step 3's mutation target.

    Three games for one team. Computing the MIDDLE game's stats must reflect
    only the FIRST game -- not the middle one (lookahead on itself) and not the
    last one (lookahead on the future).
    """
    g1 = FakeGame(1, date(2024, 1, 1), 1, 2, 110, 100)      # +10
    g2 = FakeGame(2, date(2024, 1, 5), 1, 2, 150, 100)      # +50  <- target
    g3 = FakeGame(3, date(2024, 1, 9), 1, 2, 100, 190)      # -90
    games = [g1, g2, g3]

    stats = ts.compute_team_stats(games, team_id=1, before_date=g2.date)

    assert stats["point_diff"] == pytest.approx(10.0), "must see only game 1"
    assert stats["rest_days"] == 4
    assert stats["home_wins"] == 1
    assert stats["home_losses"] == 0


def test_compute_refuses_to_emit_unmeasurable_features():
    """offensive_rating / defensive_rating / pace need possessions, which this
    repo does not collect. They must be absent, not defaulted."""
    games = [FakeGame(1, date(2024, 1, 1), 1, 2, 110, 100)]
    stats = ts.compute_team_stats(games, team_id=1, before_date=date(2024, 1, 5))
    for forbidden in ("offensive_rating", "defensive_rating", "pace", "sos"):
        assert forbidden not in stats
    assert set(stats) == set(ts.COMPUTED_STAT_TYPES)


# --------------------------------------------------------------------------
# Step 4: persistence + idempotency
# --------------------------------------------------------------------------

def test_store_team_stats_writes_rows_for_both_teams(seeded):
    session, a, b, _ = seeded
    g1 = Game(sport="nba", season="2024-2025", date=date(2024, 1, 1),
              home_team_id=a.id, away_team_id=b.id,
              home_score=110, away_score=100, status="final")
    g2 = Game(sport="nba", season="2024-2025", date=date(2024, 1, 5),
              home_team_id=a.id, away_team_id=b.id,
              home_score=120, away_score=100, status="final")
    session.add_all([g1, g2])
    session.commit()

    n = ts.store_team_stats_for_game(session, g2, [g1])
    session.commit()

    assert n == 2 * len(ts.COMPUTED_STAT_TYPES)
    rows = session.query(TeamStat).filter(TeamStat.game_id == g2.id).all()
    assert {r.team_id for r in rows} == {a.id, b.id}
    home_pd = next(r.value for r in rows
                   if r.team_id == a.id and r.stat_type == "point_diff")
    assert home_pd == pytest.approx(10.0)


def test_store_team_stats_is_idempotent(seeded):
    session, a, b, _ = seeded
    g1 = Game(sport="nba", season="2024-2025", date=date(2024, 1, 1),
              home_team_id=a.id, away_team_id=b.id,
              home_score=110, away_score=100, status="final")
    g2 = Game(sport="nba", season="2024-2025", date=date(2024, 1, 5),
              home_team_id=a.id, away_team_id=b.id,
              home_score=120, away_score=100, status="final")
    session.add_all([g1, g2])
    session.commit()

    ts.store_team_stats_for_game(session, g2, [g1])
    session.commit()
    first = session.query(TeamStat).filter(TeamStat.game_id == g2.id).count()

    ts.store_team_stats_for_game(session, g2, [g1])
    session.commit()
    second = session.query(TeamStat).filter(TeamStat.game_id == g2.id).count()

    assert second == first


# --------------------------------------------------------------------------
# Step 5: Elo history, pre-game
# --------------------------------------------------------------------------

def test_elo_history_row_for_game_two_does_not_reflect_game_two(seeded):
    session, a, b, _ = seeded
    games = [
        Game(sport="nba", season="2024-2025", date=date(2024, 1, 1),
             home_team_id=a.id, away_team_id=b.id,
             home_score=130, away_score=100, status="final"),
        Game(sport="nba", season="2024-2025", date=date(2024, 1, 5),
             home_team_id=a.id, away_team_id=b.id,
             home_score=100, away_score=140, status="final"),
        Game(sport="nba", season="2024-2025", date=date(2024, 1, 9),
             home_team_id=a.id, away_team_id=b.id,
             home_score=105, away_score=100, status="final"),
    ]
    session.add_all(games)
    session.commit()

    ts.backfill_elo_history(session, "nba")
    session.commit()

    hist = {(e.team_id, e.game_id): e.rating
            for e in session.query(EloHistory).all()}

    g1, g2, g3 = games
    # Game 1 is every team's first: pre-game rating is the initial 1500.
    assert hist[(a.id, g1.id)] == pytest.approx(1500.0)
    assert hist[(b.id, g1.id)] == pytest.approx(1500.0)

    # Game 2's row must be the rating carried INTO game 2, i.e. it reflects
    # game 1 (a big home win for A) but NOT game 2 (a heavy loss for A).
    assert hist[(a.id, g2.id)] > 1500.0, "game 2's row should reflect game 1's win"
    assert hist[(a.id, g3.id)] < hist[(a.id, g2.id)], \
        "game 2's loss should only show up in game 3's row"

    # Zero-sum check: the pair's ratings going into game 2 still sum to 3000,
    # so nothing from game 2 itself has been folded in.
    assert hist[(a.id, g2.id)] + hist[(b.id, g2.id)] == pytest.approx(3000.0)


def test_elo_backfill_is_idempotent(seeded):
    session, a, b, _ = seeded
    session.add(Game(sport="nba", season="2024-2025", date=date(2024, 1, 1),
                     home_team_id=a.id, away_team_id=b.id,
                     home_score=130, away_score=100, status="final"))
    session.commit()
    ts.backfill_elo_history(session, "nba")
    session.commit()
    n1 = session.query(EloHistory).count()
    ts.backfill_elo_history(session, "nba")
    session.commit()
    assert session.query(EloHistory).count() == n1


def test_elo_backfill_skips_combat_sports(seeded):
    """mma/boxing Elo history is owned by grader._apply_combat_elo_update and
    uses post-game semantics; this backfill must not write into it."""
    session, a, b, _ = seeded
    for t in (a, b):
        t.sport = "mma"
    session.add(Game(sport="mma", season="2024-2025", date=date(2024, 1, 1),
                     home_team_id=a.id, away_team_id=b.id,
                     home_score=1, away_score=0, status="final"))
    session.commit()
    with pytest.raises(ValueError):
        ts.backfill_elo_history(session, "mma")


# --------------------------------------------------------------------------
# Step 8: backfill over a session
# --------------------------------------------------------------------------

def test_backfill_team_stats_is_chronological_and_resumable(seeded):
    session, a, b, c = seeded
    games = [
        Game(sport="nba", season="2024-2025", date=date(2024, 1, 1),
             home_team_id=a.id, away_team_id=b.id,
             home_score=110, away_score=100, status="final"),
        Game(sport="nba", season="2024-2025", date=date(2024, 1, 5),
             home_team_id=a.id, away_team_id=c.id,
             home_score=120, away_score=100, status="final"),
        Game(sport="nba", season="2024-2025", date=date(2024, 1, 9),
             home_team_id=b.id, away_team_id=a.id,
             home_score=100, away_score=130, status="final"),
    ]
    session.add_all(games)
    session.commit()

    res = ts.backfill_team_stats(session, "nba")
    session.commit()
    assert res["games_processed"] == 3

    # Game 2's point_diff for team A sees only game 1 (+10).
    v = (session.query(TeamStat)
         .filter(TeamStat.game_id == games[1].id, TeamStat.team_id == a.id,
                 TeamStat.stat_type == "point_diff").one().value)
    assert v == pytest.approx(10.0)

    # Game 3's point_diff for team A sees games 1 and 2 (+10, +20) = +15.
    v3 = (session.query(TeamStat)
          .filter(TeamStat.game_id == games[2].id, TeamStat.team_id == a.id,
                  TeamStat.stat_type == "point_diff").one().value)
    assert v3 == pytest.approx(15.0)

    # Resume: a second pass skips games that already have rows and writes none.
    before = session.query(TeamStat).count()
    res2 = ts.backfill_team_stats(session, "nba")
    session.commit()
    assert res2["games_skipped"] == 3
    assert res2["rows_written"] == 0
    assert session.query(TeamStat).count() == before


def test_backfill_team_stats_does_not_assume_contiguity(seeded):
    """A gap in the middle must be filled: the resume check is per game."""
    session, a, b, _ = seeded
    games = [
        Game(sport="nba", season="2024-2025", date=date(2024, 1, d),
             home_team_id=a.id, away_team_id=b.id,
             home_score=110, away_score=100, status="final")
        for d in (1, 5, 9)
    ]
    session.add_all(games)
    session.commit()

    ts.backfill_team_stats(session, "nba")
    session.commit()
    # Delete the MIDDLE game's rows only.
    (session.query(TeamStat)
     .filter(TeamStat.game_id == games[1].id)
     .delete(synchronize_session=False))
    session.commit()

    res = ts.backfill_team_stats(session, "nba")
    session.commit()
    assert res["games_processed"] == 1
    assert session.query(TeamStat).filter(
        TeamStat.game_id == games[1].id).count() == 2 * len(ts.COMPUTED_STAT_TYPES)


def test_backfill_dry_run_writes_nothing(seeded):
    session, a, b, _ = seeded
    session.add(Game(sport="nba", season="2024-2025", date=date(2024, 1, 1),
                     home_team_id=a.id, away_team_id=b.id,
                     home_score=110, away_score=100, status="final"))
    session.commit()
    res = ts.backfill_team_stats(session, "nba", dry_run=True)
    assert res["games_processed"] == 1
    assert session.query(TeamStat).count() == 0
    ts.backfill_elo_history(session, "nba", dry_run=True)
    assert session.query(EloHistory).count() == 0
