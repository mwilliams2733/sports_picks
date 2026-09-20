"""Guards for finalizing mma bouts from ESPN.

`fetch_ufc_events` was referenced by two comments -- scheduler.py and
catch_up_finals.py both named it as the thing that writes combat finals --
and never existed. The consequence was total: 293 combat games, every one
`scheduled`, no scores, no elo_history, no fighter ratings, and 85 picks
none of which could ever be graded. `grade_completed_games` ran daily and
found nothing, because nothing ever set a combat game final.

ESPN's UFC scoreboard returns one event per date -- the whole card -- whose
`competitions` are the individual bouts, each carrying a status and a
`winner` flag per athlete.

The dangerous outcome is a wrong winner: a bout finalized against the wrong
fighter grades a real pick backwards and feeds a reversed Elo update that
nothing downstream can detect. So a pair that does not match exactly, in
either corner order, is left alone.
"""
import datetime

import pytest

from backend.collectors.ufc import (
    BoutResult, bouts_from_event, match_bout, normalize_name,
)

DAY = datetime.date(2026, 5, 2)


def _competition(a, b, winner, status="STATUS_FINAL"):
    return {
        "status": {"type": {"name": status}},
        "competitors": [
            {"athlete": {"displayName": a}, "winner": winner == a, "order": 1},
            {"athlete": {"displayName": b}, "winner": winner == b, "order": 2},
        ],
    }


def _event(*comps):
    return {"id": "600", "date": "2026-05-03T02:00Z", "competitions": list(comps)}


def test_a_finished_bout_yields_its_winner():
    bouts = bouts_from_event(_event(
        _competition("Dom Mar Fan", "Kody Steele", winner="Kody Steele")))
    assert bouts == [BoutResult(fighter_a="Dom Mar Fan",
                                fighter_b="Kody Steele",
                                winner="Kody Steele")]


def test_an_unfinished_bout_is_skipped():
    """A scheduled or in-progress bout has no result to write."""
    assert bouts_from_event(_event(
        _competition("A", "B", winner="B", status="STATUS_SCHEDULED"))) == []


def test_a_bout_with_no_winner_is_skipped():
    """A draw or no-contest has no winner flag, and inventing one would
    grade a pick against a result that never happened."""
    comp = _competition("A", "B", winner=None)
    assert bouts_from_event(_event(comp)) == []


def test_every_bout_on_the_card_is_returned():
    bouts = bouts_from_event(_event(
        _competition("A", "B", winner="A"),
        _competition("C", "D", winner="D"),
        _competition("E", "F", winner="E")))
    assert len(bouts) == 3


def test_names_match_regardless_of_corner_order():
    """Our row's home/away split is arbitrary for a bout; ESPN's is too."""
    bouts = [BoutResult("Carlos Prates", "Jack Della Maddalena",
                        "Jack Della Maddalena")]
    assert match_bout(bouts, "Jack Della Maddalena", "Carlos Prates") is bouts[0]
    assert match_bout(bouts, "Carlos Prates", "Jack Della Maddalena") is bouts[0]


def test_names_match_through_case_accents_and_punctuation():
    """Hyphen-versus-space and accents are presentation. A missing separator
    ("alselwady") is a different spelling and is deliberately NOT matched --
    collapsing whitespace away would invite false pairs, and a wrong pair is
    the one error nothing downstream can catch.
    """
    bouts = [BoutResult("Abdul-Kareem Al-Selwady", "Shem Rock", "Shem Rock")]
    assert match_bout(bouts, "abdul kareem al selwady", "SHEM ROCK") is bouts[0]
    assert normalize_name("José Aldó") == normalize_name("jose aldo")
    assert normalize_name("O'Malley") == normalize_name("OMalley")
    assert match_bout(bouts, "abdulkareem alselwady", "Shem Rock") is None


def test_an_unknown_pair_matches_nothing():
    """Refusing beats guessing: a wrong winner grades a pick backwards and
    feeds a reversed Elo update nothing downstream can catch."""
    bouts = [BoutResult("A Fighter", "B Fighter", "A Fighter")]
    assert match_bout(bouts, "A Fighter", "C Fighter") is None
    assert match_bout(bouts, "X", "Y") is None


def test_a_half_matching_pair_is_refused():
    """One shared fighter is not the same bout -- a fighter can appear twice
    on a card in our rows if the odds feed duplicated them."""
    bouts = [BoutResult("A Fighter", "B Fighter", "A Fighter")]
    assert match_bout(bouts, "A Fighter", "Someone Else") is None


# --- the grader must be able to rate a fighter it has never seen ------------

from backend.database import get_engine, get_session
from backend.models import Base, EloRating, EloHistory, Game, Team
from backend.pipeline.grader import _apply_combat_elo_update


def _combat_db(tmp_path, *, seed_ratings):
    session = get_session(get_engine(str(tmp_path / "c.db")))
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="Fighter A", abbreviation="Fighter A", sport="mma"),
        Team(id=2, name="Fighter B", abbreviation="Fighter B", sport="mma"),
    ])
    session.flush()
    if seed_ratings:
        session.add_all([
            EloRating(team_id=1, sport="mma", rating=1500.0),
            EloRating(team_id=2, sport="mma", rating=1500.0),
        ])
    game = Game(id=1, sport="mma", season="2026", date=DAY,
                home_team_id=1, away_team_id=2, status="final",
                home_score=1, away_score=0)
    session.add(game)
    session.commit()
    return session, game


def test_a_fighter_with_no_rating_row_is_seeded_not_skipped(tmp_path):
    """`_apply_combat_elo_update` returned early when either rating row was
    missing. Nothing ever created them -- the database held zero combat
    EloRating rows -- so combat Elo could never start. A fighter with no
    rating is a new fighter, and the seed is exactly what a new fighter
    should carry.
    """
    session, game = _combat_db(tmp_path, seed_ratings=False)

    _apply_combat_elo_update(session, game)
    session.commit()

    ratings = {r.team_id: r.rating for r in session.query(EloRating).all()}
    assert set(ratings) == {1, 2}, "both fighters should now have a rating"
    assert ratings[1] > 1500.0, "the winner should have gained"
    assert ratings[2] < 1500.0, "the loser should have lost"


def test_an_existing_rating_is_still_used(tmp_path):
    """Seeding must not overwrite a rating a fighter has already earned."""
    session, game = _combat_db(tmp_path, seed_ratings=True)
    session.query(EloRating).filter(EloRating.team_id == 1).one().rating = 1700.0
    session.commit()

    _apply_combat_elo_update(session, game)
    session.commit()

    winner = session.query(EloRating).filter(EloRating.team_id == 1).one()
    assert winner.rating > 1700.0
