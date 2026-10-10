"""UFC history goes in as final MMA games; Elo is replayed from them."""
from datetime import date

from backend.collectors.ufcstats_history import HistoricalBout
from backend.models import Base, EloRating, Game, Team
from backend.scripts.dedupe_combat_games import rebuild_combat_elo
from backend.scripts.import_ufc_history import coverage, import_bouts

D = date(2026, 9, 26)


def _team(session, tid, name):
    session.add(Team(id=tid, name=name, abbreviation=name, sport="mma"))


def _seed(session):
    _team(session, 1, "Cong Wang")            # odds-feed order; CSV says "Wang Cong"
    _team(session, 2, "Natalia Silva")
    _team(session, 3, "Payton Talbott")
    _team(session, 4, "Deiveson Figueiredo")
    _team(session, 9, "Deiveson Figueiredo")  # a duplicate row: lowest id (4) is used
    session.flush()
    # The odds feed already has Silva-Wang, mirrored and one day later.
    session.add(Game(sport="mma", season="2026", date=date(2026, 10, 4), status="final",
                     home_team_id=1, away_team_id=2, home_score=0, away_score=1))
    # An upcoming card: Talbott (will have history) vs a newcomer (none).
    _team(session, 5, "Brand New")
    session.flush()
    session.add(Game(sport="mma", season="2026", date=date(2026, 10, 17), status="scheduled",
                     home_team_id=3, away_team_id=5))
    session.commit()


BOUTS = [
    HistoricalBout(date(2026, 10, 3), "UFC 332", "Natalia Silva", "Wang Cong", 1, 0),
    HistoricalBout(date(2026, 10, 3), "UFC 332", "Deiveson Figueiredo", "Payton Talbott", 0, 1),
    HistoricalBout(D, "UFC FN", "Payton Talbott", "Someone Else", 1, 1),
    HistoricalBout(D, "UFC FN", "Same Guy", "Same Guy", 1, 0),
]


def test_import_matches_names_skips_known_bouts_and_creates_new_fighters(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    summary = import_bouts(db_session, BOUTS)
    assert summary == {"inserted": 2, "duplicates": 1, "same_fighter": 1,       # Review Focus 1
                       "teams_created": 1, "ambiguous_existing": 1}           # Review Focus 2
    figs = db_session.query(Game).filter(Game.date == date(2026, 10, 3)).one()
    assert (figs.home_team_id, figs.away_team_id, figs.home_score, figs.away_score, figs.status) == (
        4, 3, 0, 1, "final")
    draw = db_session.query(Game).filter(Game.date == D).one()
    assert (draw.home_score, draw.away_score) == (1, 1)
    assert db_session.query(Team).filter(Team.name == "Someone Else").count() == 1


def test_a_second_run_inserts_nothing(db_engine, db_session):                   # Review Focus 4
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    import_bouts(db_session, BOUTS)
    db_session.commit()
    again = import_bouts(db_session, BOUTS)
    assert again["inserted"] == 0 and again["teams_created"] == 0


def test_elo_replays_from_the_imported_bouts_and_coverage_is_measured(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    before = coverage(db_session, date(2026, 10, 10))
    assert before == {"games": 1, "fighters": 2, "fighters_with_history": 0, "games_both_known": 0}
    import_bouts(db_session, BOUTS)
    rebuild_combat_elo(db_session, "mma")
    ratings = {r.team_id: r.rating for r in db_session.query(EloRating).filter(EloRating.sport == "mma")}
    assert ratings[3] > 1500 > ratings[4]                 # Talbott beat Figueiredo
    after = coverage(db_session, date(2026, 10, 10))
    assert after == {"games": 1, "fighters": 2, "fighters_with_history": 1, "games_both_known": 0}
