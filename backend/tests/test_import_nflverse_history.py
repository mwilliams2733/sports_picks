"""NFL history from nflverse, with the closing lines it carries.

The database held 48 nfl games, 2026-09-09 to 2026-09-28, of which 31 were
final. That is the entire nfl record the model trains and is measured on,
against 1,253 nba games -- and it is why nfl's home-advantage slot is fitted
from 30 hosted games, why `MIN_EVAL_GAMES = 200` means nfl can never be
scored by the calibration report, and why every nfl conclusion this repo has
reached rests on single digits.

nflverse publishes 1,411 games over 2022-2026, 1,171 of them with BOTH a
result and a CLOSING moneyline. Measured on that sample the closing line is
well calibrated in every band (0.0-0.2 predicts 16.3% and observes 15.4%;
0.8-1.0 predicts 85.3% and observes 89.3%), which is the null the market
shrinkage measurement rests on and could not previously resolve.

Two abbreviations differ and are the only ones that do
-----------------------------------------------------
nflverse writes `LA` and `WAS`; this database writes `LAR` and `WSH`. The
other 30 match exactly. An unmapped abbreviation must REFUSE rather than
create a second team row -- duplicate team identities are what
`dedupe_combat_games` exists to clean up, and a silent `Team(name="LA")`
would split the Rams' Elo history in two.

The closing line is not a pre-game quote
----------------------------------------
Closing lines are stored as an `Odds` row under the bookmaker
`nflverse_close`, so they are labelled rather than mixed into the live
consensus. `_average_odds` would otherwise treat a closing line as one more
book's pre-game price. For a finished game that is harmless; for anything
being predicted it is lookahead, because nobody could have taken the close
at pick time.
"""
import datetime

import pytest
from sqlalchemy import create_engine

from backend.database import get_session, run_migrations
from backend.models import Base, Game, Odds, Team
from backend.scripts.import_nflverse_history import (ABBR_FIXUPS,
                                                     CLOSING_BOOKMAKER,
                                                     NflverseGame,
                                                     import_games)


def _row(**kw):
    base = dict(game_id="2024_01_BUF_LA", season=2024, week=1, game_type="REG",
                gameday=datetime.date(2024, 9, 8), away_team="BUF",
                home_team="LA", away_score=31, home_score=10, roof="dome",
                spread_line=-1.0, home_spread_odds=101, away_spread_odds=-112,
                total_line=51.5, over_odds=-111, under_odds=-101,
                home_moneyline=106, away_moneyline=-117)
    base.update(kw)
    return NflverseGame(**base)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    run_migrations(engine)
    s = get_session(engine)
    for abbr in ("BUF", "LAR", "WSH", "KC"):
        s.add(Team(name=abbr, abbreviation=abbr, sport="nfl"))
    s.commit()
    return s


def test_the_two_differing_abbreviations_are_mapped():
    assert ABBR_FIXUPS == {"LA": "LAR", "WAS": "WSH"}


def test_a_game_is_imported_against_existing_team_rows(session):
    summary = import_games(session, [_row()], apply=True)

    assert summary["created"] == 1
    game = session.query(Game).one()
    assert game.sport == "nfl"
    assert game.status == "final"
    assert (game.home_score, game.away_score) == (10, 31)


def test_an_unknown_abbreviation_refuses_rather_than_inventing_a_team(session):
    """A silent `Team(name="XXX")` would split a franchise's Elo history."""
    summary = import_games(session, [_row(home_team="XXX")], apply=True)

    assert summary["created"] == 0
    assert summary["refused_unknown_team"] == 1
    assert session.query(Team).filter_by(abbreviation="XXX").count() == 0


def test_the_closing_line_is_stored_under_its_own_bookmaker(session):
    """Labelled, not mixed into the live consensus: `_average_odds` would
    otherwise read a closing line as one more pre-game quote."""
    import_games(session, [_row()], apply=True)

    odds = session.query(Odds).one()
    assert odds.bookmaker == CLOSING_BOOKMAKER
    assert odds.moneyline_home == 106
    assert odds.moneyline_away == -117
    assert odds.over_under == 51.5


def test_the_spread_is_stored_from_each_sides_own_view(session):
    """nflverse `spread_line` is positive when the HOME side is favoured.
    `Odds.spread_home` is the handicap applied to the home team, so the sign
    flips. Storing it unflipped prices every favourite as an underdog."""
    import_games(session, [_row(spread_line=6.5)], apply=True)

    odds = session.query(Odds).one()
    assert odds.spread_home == -6.5
    assert odds.spread_away == 6.5


def test_the_closing_prices_land_on_the_price_columns(session):
    import_games(session, [_row()], apply=True)

    odds = session.query(Odds).one()
    assert odds.spread_home_price == 101
    assert odds.spread_away_price == -112
    assert odds.over_price == -111
    assert odds.under_price == -101


def test_a_game_without_a_result_is_scheduled_not_final(session):
    """Future fixtures arrive with null scores. Marking them final would
    grade picks against a game that has not happened."""
    summary = import_games(session, [_row(home_score=None, away_score=None)],
                           apply=True)

    assert summary["created"] == 1
    assert session.query(Game).one().status == "scheduled"


def test_a_game_already_present_is_not_duplicated(session):
    """Matched on (sport, date, home, away). The 48 games already here must
    be updated, never doubled -- one game stored twice is one game counted
    twice."""
    import_games(session, [_row()], apply=True)
    summary = import_games(session, [_row()], apply=True)

    assert session.query(Game).count() == 1
    assert summary["created"] == 0
    assert summary["matched_existing"] == 1


def test_an_existing_game_keeps_its_espn_id(session):
    """An imported row must not overwrite the identity the live collector
    established; espn_id is how a game is recognised across date
    conventions."""
    session.add(Game(sport="nfl", date=datetime.date(2024, 9, 8), season="2024",
                     status="final", espn_id="401547353",
                     home_team_id=session.query(Team).filter_by(
                         abbreviation="LAR").one().id,
                     away_team_id=session.query(Team).filter_by(
                         abbreviation="BUF").one().id,
                     home_score=10, away_score=31))
    session.commit()

    import_games(session, [_row()], apply=True)

    assert session.query(Game).one().espn_id == "401547353"


def test_dry_run_is_the_default_and_writes_nothing(session):
    summary = import_games(session, [_row()])

    assert summary["created"] == 1
    assert session.query(Game).count() == 0
    assert session.query(Odds).count() == 0


def test_a_row_with_no_closing_prices_creates_no_odds_row(session):
    """A 2026 fixture that has not been priced yet. An Odds row of all
    NULLs is not a quote."""
    import_games(session, [_row(home_moneyline=None, away_moneyline=None,
                                spread_line=None, total_line=None)],
                 apply=True)

    assert session.query(Game).count() == 1
    assert session.query(Odds).count() == 0


def test_a_real_bookmakers_quote_is_never_overwritten(session):
    """The 48 games already here carry live quotes from real books. Reusing
    one of those rows would replace a price that WAS takeable before kickoff
    with a closing line that was not -- destroying the pre-game quote and
    inventing a post-hoc one under a book's name."""
    # The real book's row must exist FIRST. Created second, it sorts after
    # the closing row and a query that ignores the bookmaker still happens
    # to pick the right one -- the test would pass for the wrong reason.
    session.add(Game(sport="nfl", date=datetime.date(2024, 9, 8),
                     season="2024", status="final",
                     home_team_id=session.query(Team).filter_by(
                         abbreviation="LAR").one().id,
                     away_team_id=session.query(Team).filter_by(
                         abbreviation="BUF").one().id,
                     home_score=10, away_score=31))
    session.flush()
    game = session.query(Game).one()
    session.add(Odds(game_id=game.id, bookmaker="draftkings",
                     moneyline_home=-150, moneyline_away=130,
                     spread_home=-3.0, spread_away=3.0, over_under=44.0))
    session.commit()

    import_games(session, [_row(home_moneyline=999)], apply=True)

    dk = session.query(Odds).filter_by(bookmaker="draftkings").one()
    assert dk.moneyline_home == -150, "the live quote must survive untouched"
    close = session.query(Odds).filter_by(bookmaker=CLOSING_BOOKMAKER).one()
    assert close.moneyline_home == 999
    assert session.query(Odds).count() == 2


def test_a_dry_run_creates_no_odds_even_for_a_new_game(session):
    summary = import_games(session, [_row()])

    assert summary["created"] == 1
    assert session.query(Game).count() == 0
    assert session.query(Odds).count() == 0


def test_the_season_label_is_the_repos_own_not_the_raw_year(session):
    """nflverse writes the start year as an int; this database writes
    "2026-27" for a season crossing the new year. Importing `str(season)`
    put 224 games under "2026" beside 48 under "2026-27" -- two conventions
    in one column, which is exactly the bug `season_label` was written to
    end."""
    import_games(session, [_row(gameday=datetime.date(2024, 9, 8),
                                season=2024)], apply=True)

    assert session.query(Game).one().season == "2024-25"
