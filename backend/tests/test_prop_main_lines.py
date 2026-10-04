"""One prop line per book: a book's ladder is stored as its main line.

Bovada quotes a ladder inside the standard prop markets. `_store_props`
upserts with no ``line`` in its key, so each rung overwrote the last and the
top rung was stored. Prop edge ignores price, so that rung (Under 275.5 at
-230 against a projection near 245) read as a large edge: re-running the
analyzer on 2026-09-27..10-03 NFL data chose 16 of 231 picks on a rung.

The ladder below is Bovada's Josh Allen pass yards, NE @ BUF, from a raw
Odds API response on 2026-10-04, in the order the API returned it.
"""
from datetime import date

from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import Base, Game, PlayerProp, Team
from backend.pipeline.full_pipeline import _store_props, main_lines

ALLEN_BOVADA = [(215.5, -250, 185), (225.5, -190, 145), (235.5, -145, 110),
                (245.5, -115, -115), (255.5, 110, -145), (265.5, 140, -185),
                (275.5, 170, -230)]


def _rows(book, ladder, player="Josh Allen", market="player_pass_yds"):
    out = []
    for line, over, under in ladder:
        for outcome, odds in (("Over", over), ("Under", under)):
            out.append({"event_id": "e", "bookmaker": book, "market": market,
                        "player_name": player, "outcome": outcome,
                        "line": line, "odds": odds})
    return out


def test_a_ladder_keeps_its_main_rung():
    kept = main_lines(_rows("bovada", ALLEN_BOVADA))

    assert {(r["outcome"], r["line"], r["odds"]) for r in kept} == {
        ("Over", 245.5, -115), ("Under", 245.5, -115)}


def test_a_single_line_book_is_untouched():
    rows = _rows("draftkings", [(245.5, -113, -111)])

    assert main_lines(rows) == rows


def test_each_book_and_player_is_judged_separately():
    rows = (_rows("bovada", ALLEN_BOVADA) + _rows("fanduel", [(242.5, -113, -113)])
            + _rows("bovada", [(40.5, -200, 160), (50.5, -110, -110)],
                    player="DJ Moore", market="player_reception_yds"))

    kept = {(r["bookmaker"], r["player_name"], r["line"]) for r in main_lines(rows)}

    assert kept == {("bovada", "Josh Allen", 245.5), ("fanduel", "Josh Allen", 242.5),
                    ("bovada", "DJ Moore", 50.5)}


def test_a_rung_missing_a_side_is_not_the_main_line():
    rows = _rows("bovada", [(235.5, -145, 110), (245.5, -115, -115)])
    rows = [r for r in rows if not (r["line"] == 245.5 and r["outcome"] == "Under")]

    assert {r["line"] for r in main_lines(rows)} == {235.5}


def test_a_prop_with_no_line_passes_through():
    """Anytime TD: outcome "Yes", no point."""
    td = [{"event_id": "e", "bookmaker": "bovada", "market": "player_anytime_td",
           "player_name": "Josh Allen", "outcome": "Yes", "line": None, "odds": 150}]

    assert main_lines(td) == td


def test_the_stored_row_is_the_main_line_not_the_last_rung():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026", date=date(2026, 10, 4),
               status="scheduled", home_team_id=1, away_team_id=2))
    s.commit()

    _store_props(s, 1, _rows("bovada", ALLEN_BOVADA))

    stored = {(r.outcome, r.line, r.odds) for r in s.query(PlayerProp)}
    assert stored == {("Over", 245.5, -115), ("Under", 245.5, -115)}
