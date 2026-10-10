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
    assert summary == {"inserted": 2, "duplicates": 1, "name_variants": 0, "same_fighter": 1,       # Review Focus 1
                       "teams_created": 1, "ambiguous_existing": 1,           # Review Focus 2
                       "matched_non_final": [], "finalized": []}
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


def _pair_game(session, gid, home, away, day, status="final"):
    session.add(Game(id=gid, sport="mma", season="2026", date=day, status=status,
                     home_team_id=home, away_team_id=away,
                     home_score=1 if status == "final" else None,
                     away_score=0 if status == "final" else None))


def test_a_fighter_split_across_two_rows_is_still_one_bout(db_engine, db_session):
    # Final review: a history row "Wang Cong" and an odds-feed row "Cong Wang"
    # for one fighter; the feed's graded bout is on the feed row. A refresh
    # must not insert that bout again (the 09-20 double count).
    Base.metadata.create_all(db_engine)
    _team(db_session, 1, "Wang Cong")
    _team(db_session, 2, "Natalia Silva")
    _team(db_session, 20, "Cong Wang")
    db_session.flush()
    _pair_game(db_session, 100, 20, 2, date(2026, 10, 3))
    db_session.commit()
    summary = import_bouts(db_session, [BOUTS[0]])
    assert (summary["inserted"], summary["duplicates"]) == (0, 1)


def test_a_bout_two_days_off_is_the_same_card(db_engine, db_session):
    # dedupe_combat_games.ADJACENT_DAYS: one card under two date conventions.
    Base.metadata.create_all(db_engine)
    _team(db_session, 1, "Cong Wang")
    _team(db_session, 2, "Natalia Silva")
    db_session.flush()
    _pair_game(db_session, 100, 1, 2, date(2026, 10, 5))
    db_session.commit()
    assert import_bouts(db_session, [BOUTS[0]])["inserted"] == 0


def test_a_bout_stored_unfinished_is_reported_not_silently_skipped(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _team(db_session, 1, "Cong Wang")
    _team(db_session, 2, "Natalia Silva")
    db_session.flush()
    _pair_game(db_session, 100, 1, 2, date(2026, 10, 3), status="scheduled")
    db_session.commit()
    summary = import_bouts(db_session, [BOUTS[0]])
    assert (summary["inserted"], summary["duplicates"], summary["matched_non_final"]) == (0, 0, [100])
    assert db_session.get(Game, 100).status == "scheduled"     # never modified


def test_finalize_unfinished_completes_a_stuck_bout_with_no_bets(db_engine, db_session):
    # Owner, 2026-10-10: the 15 Mar-Jul bouts stuck scheduled/canceled are
    # finalized from the CSV -- only where no pick or paper bet is attached.
    from backend.models import PickModel, StrategyModel
    Base.metadata.create_all(db_engine)
    _team(db_session, 1, "Cong Wang")
    _team(db_session, 2, "Natalia Silva")
    _team(db_session, 3, "Deiveson Figueiredo")
    _team(db_session, 4, "Payton Talbott")
    db_session.add(StrategyModel(id=1, name="x", config_json="{}"))
    db_session.flush()
    _pair_game(db_session, 100, 1, 2, date(2026, 10, 3), status="scheduled")   # Wang home
    _pair_game(db_session, 101, 3, 4, date(2026, 10, 3), status="canceled")
    db_session.flush()
    db_session.add(PickModel(game_id=101, strategy_id=1, pick_type="moneyline",
                             pick_value="HOME ML", confidence=3, edge_pct=5.0, odds_at_pick=100))
    db_session.commit()
    summary = import_bouts(db_session, BOUTS[:2], finalize_unfinished=True)
    stuck = db_session.get(Game, 100)
    # Silva (CSV fighter_a) won; she is AWAY on the stored row.
    assert (stuck.status, stuck.home_score, stuck.away_score) == ("final", 0, 1)
    assert db_session.get(Game, 101).status == "canceled"        # has a pick: left alone
    assert (summary["finalized"], summary["matched_non_final"], summary["inserted"]) == ([100], [101], 0)
