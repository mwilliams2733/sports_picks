"""Tests for `backend.analysis.sport_signal_report`.

This report answers a coarser question than `calibration_report`: does each
sport's model beat its two trivial nulls, all sports at once, without the
200-game floor that report enforces. The load-bearing tests here are the
within-sport split (a global cutoff would strand a whole sport on one side
of the line) and the leakage guard on the base-rate null (it must read the
TRAIN half's home-win rate, never the eval half's -- an eval-half rate would
leak the answer into the thing it's being compared against).
"""
from datetime import date, timedelta

from backend.models import Base, EloRating, Game, Odds, Team, TeamStat, PickModel, CalibrationHistory
from backend.analysis.sport_signal_report import (
    brier_score,
    per_sport_split_dates,
    run_report,
)


def _seed_sport(session, sport, home_wins, start=date(2026, 1, 1), team_offset=0, gid_offset=0):
    """Seed one completed game per entry of `home_wins` (bool), one per day.

    Distinct teams and game ids per call, via `team_offset`/`gid_offset`, so
    two sports can be seeded into the same session without colliding.
    """
    teams = [Team(id=team_offset + i, name=f"{sport}T{i}", abbreviation=f"{sport}T{i}",
                  sport=sport) for i in range(1, 9)]
    session.add_all(teams)
    session.flush()
    session.add_all([EloRating(team_id=team_offset + i, sport=sport, rating=1500.0 + 20 * i)
                     for i in range(1, 9)])
    games = []
    for k, home_win in enumerate(home_wins):
        gid = gid_offset + k + 1
        home = team_offset + (k % 8) + 1
        away = team_offset + ((k + 3) % 8) + 1
        home_score, away_score = (110, 100) if home_win else (100, 110)
        g = Game(id=gid, sport=sport, season="2026",
                 date=start + timedelta(days=k), home_team_id=home,
                 away_team_id=away, home_score=home_score, away_score=away_score,
                 status="final")
        session.add(g)
        session.add(Odds(game_id=gid, bookmaker="book",
                         moneyline_home=-150, moneyline_away=130))
        games.append(g)
    session.commit()
    return games


def test_split_is_within_sport(db_session):
    Base.metadata.create_all(db_session.get_bind())
    _seed_sport(db_session, "nba", [True] * 20, start=date(2026, 1, 1),
               team_offset=0, gid_offset=0)
    _seed_sport(db_session, "mlb", [True] * 20, start=date(2026, 6, 1),
               team_offset=100, gid_offset=100)

    splits = per_sport_split_dates(db_session, train_frac=0.7)
    assert set(splits) == {"nba", "mlb"}

    for sport in ("nba", "mlb"):
        games = (
            db_session.query(Game)
            .filter(Game.sport == sport, Game.status == "final")
            .all()
        )
        cutoff = splits[sport]
        train = [g for g in games if g.date < cutoff]
        eval_ = [g for g in games if g.date >= cutoff]
        assert train, f"{sport} has no training games"
        assert eval_, f"{sport} has no evaluation games"


def test_a_sport_below_min_eval_is_not_scored(db_session):
    Base.metadata.create_all(db_session.get_bind())
    # 10 games -> 70% split puts 7 in train, 3 in eval.
    _seed_sport(db_session, "ncaab", [True, False] * 5)

    results = run_report(db_session, train_frac=0.7, min_eval=40)
    row = next(r for r in results if r.sport == "ncaab")

    assert row.n_eval == 3
    assert row.below_min_eval is True
    assert row.model_brier is None
    assert row.base_rate_brier is None


def test_brier_of_a_perfect_predictor_is_zero():
    assert brier_score([(1.0, 1), (0.0, 0)]) == 0.0


def test_base_rate_null_uses_the_train_half_only(db_session):
    Base.metadata.create_all(db_session.get_bind())
    # 21 train games, mostly home wins (18/21); 9 eval games, mostly away
    # wins (2/9 home). If the report read the eval half's rate instead, the
    # reported base rate would be ~0.222 rather than the true train rate
    # ~0.857.
    train_pattern = [True] * 18 + [False] * 3
    eval_pattern = [True] * 2 + [False] * 7
    home_wins = train_pattern + eval_pattern
    _seed_sport(db_session, "ncaaf", home_wins)

    results = run_report(db_session, train_frac=0.7, min_eval=1)
    row = next(r for r in results if r.sport == "ncaaf")

    assert row.n_eval == 9
    assert row.base_rate == 18 / 21
    assert row.base_rate != 2 / 9


def test_a_clustered_schedule_reports_its_real_train_fraction(db_session):
    """The split cutoff is a DATE. A sport whose games cluster on very few
    distinct dates (college football is nearly all Saturdays) can land far
    from the requested `--train-frac`, because a whole slate moves to
    whichever side of the cutoff its one date falls on. `n_train` must
    report the REAL count of games before the cutoff, not
    `round(total * train_frac)` -- the two disagree exactly when clustering
    matters.
    """
    Base.metadata.create_all(db_session.get_bind())
    sport = "ncaaf"
    offset = 200

    teams = [Team(id=offset + i, name=f"{sport}T{i}", abbreviation=f"{sport}T{i}",
                  sport=sport) for i in range(1, 9)]
    db_session.add_all(teams)
    db_session.flush()
    db_session.add_all([EloRating(team_id=offset + i, sport=sport, rating=1500.0 + 20 * i)
                       for i in range(1, 9)])

    gid = offset
    # 10 games spread one-per-day early in the season.
    for k in range(10):
        gid += 1
        home = offset + (k % 8) + 1
        away = offset + ((k + 3) % 8) + 1
        db_session.add(Game(id=gid, sport=sport, season="2026",
                            date=date(2026, 1, 1) + timedelta(days=k),
                            home_team_id=home, away_team_id=away,
                            home_score=110, away_score=100, status="final"))
        db_session.add(Odds(game_id=gid, bookmaker="book",
                            moneyline_home=-150, moneyline_away=130))
    # 90 games -- a whole slate -- all on one later Saturday.
    saturday = date(2026, 2, 1)
    for k in range(90):
        gid += 1
        home = offset + (k % 8) + 1
        away = offset + ((k + 3) % 8) + 1
        home_win = k % 2 == 0
        db_session.add(Game(id=gid, sport=sport, season="2026", date=saturday,
                            home_team_id=home, away_team_id=away,
                            home_score=110 if home_win else 100,
                            away_score=100 if home_win else 110, status="final"))
        db_session.add(Odds(game_id=gid, bookmaker="book",
                            moneyline_home=-150, moneyline_away=130))
    db_session.commit()

    results = run_report(db_session, train_frac=0.7, min_eval=1)
    row = next(r for r in results if r.sport == sport)

    # The cutoff lands on the clustered Saturday, so everything before it
    # (the 10 spread-out games) is train and the whole 90-game slate is eval
    # -- a realised train fraction of 0.10 against a requested 0.70.
    assert row.n_train == 10
    assert row.n_eval == 90
    naive = round((row.n_train + row.n_eval) * 0.7)
    assert row.n_train != naive


def test_report_writes_no_rows(db_session):
    Base.metadata.create_all(db_session.get_bind())
    _seed_sport(db_session, "nba", [True, True, False, True, False] * 10)

    before = (
        db_session.query(TeamStat).count(),
        db_session.query(PickModel).count(),
        db_session.query(CalibrationHistory).count(),
    )

    run_report(db_session, train_frac=0.7, min_eval=1)

    after = (
        db_session.query(TeamStat).count(),
        db_session.query(PickModel).count(),
        db_session.query(CalibrationHistory).count(),
    )
    assert before == after == (0, 0, 0)
