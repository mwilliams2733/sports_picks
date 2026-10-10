"""Claude's reasoning from logs-archive/claude-picks-*.json onto its bets."""
from datetime import date

from backend.models import Base, Game, PaperPick, Team, UserProfile
from backend.scripts.backfill_claude_notes import match_bets, note_for

PICK = {"game": "Buccaneers @ Cowboys", "pick": "Buccaneers +9", "confidence": "2/5",
        "why": "Mayfield is out.", "vs_model": "no side"}


def test_note_for_joins_the_reasoning_confidence_and_model_view():
    assert note_for(PICK) == "Mayfield is out. Confidence 2/5 · Model: no side."


def test_an_older_file_without_a_model_view_leaves_that_clause_out():
    # claude-picks-2026-10-04-snf.json predates the vs_model field.
    old = {k: v for k, v in PICK.items() if k != "vs_model"}
    assert note_for(old) == "Mayfield is out. Confidence 2/5."


def test_only_claudes_matching_bet_without_a_note_is_matched(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    db_session.add_all([
        UserProfile(id=1, name="Marcus"), UserProfile(id=3, name="Claude"),
        Team(id=1, name="Dallas Cowboys", abbreviation="Cowboys", sport="nfl"),
        Team(id=2, name="Tampa Bay Buccaneers", abbreviation="Buccaneers", sport="nfl"),
        Team(id=3, name="Detroit Lions", abbreviation="Lions", sport="nfl")])
    db_session.flush()
    day = date(2026, 10, 8)
    db_session.add_all([
        Game(id=1, sport="nfl", season="2026", date=day, home_team_id=1, away_team_id=2, status="final"),
        Game(id=2, sport="nfl", season="2026", date=day, home_team_id=1, away_team_id=3, status="final")])
    db_session.flush()

    def bet(pid, uid, gid, note=None):
        db_session.add(PaperPick(id=pid, user_id=uid, game_id=gid, pick_type="spread",
                                 pick_value="AWAY +9", odds=-109, stake=100, note=note))

    bet(1, 3, 1)              # Claude, the right game -> matched
    bet(2, 1, 1)              # Marcus on the same game -> never
    bet(3, 3, 2)              # Claude, another game -> no
    db_session.commit()
    assert [(b.id, n) for b, n in match_bets(db_session, [PICK], day)] == [
        (1, "Mayfield is out. Confidence 2/5 · Model: no side.")]
    db_session.get(PaperPick, 1).note = "already there"
    db_session.commit()
    assert match_bets(db_session, [PICK], day) == []        # never overwrites
