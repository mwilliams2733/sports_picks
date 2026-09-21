"""One bout stored twice is one bout counted twice.

24 mma fighter-pairs are stored more than once, usually with the corners
mirrored -- `Al-Selwady vs Shem Rock` and `Shem Rock vs Al-Selwady`, the
same date, the scores swapped to match. The winner agrees in every case,
so nothing is *wrong*; it is counted twice. 25 redundant rows carry 30
`elo_history` rows -- 24% of all mma Elo history -- and 23 graded picks
worth +6.17 units that ROI counts as real wagers.

This is `dedupe_picks` one level up: there the same wager appeared three
times, here the same bout does.

Scope, deliberately narrow
--------------------------
**Same date only.** 10 of the 24 pairs sit on different dates, and several
are 06-14 against 06-15 -- one card under two date conventions, not a
duplicate to collapse. Others could be a real rematch, and two fighters
meeting twice is normal in this sport. Collapsing either would destroy a
genuine bout, so a differing date means hands off.

**Contradictory winners are refused, not resolved.** If two copies
disagree about who won, one of them has been grading picks backwards and
feeding a reversed Elo update. Picking a side would bury that; the pair is
reported instead.

Elo must be replayed, not patched
---------------------------------
Deleting a redundant `elo_history` row does not undo its effect: combat
ratings are a running product, and `EloRating` still holds the value the
double-counted bout produced. `backfill_elo_history` refuses combat sports
outright -- their history is post-game and grader-owned -- so the replay
here drives `_apply_combat_elo_update`, the same function that wrote the
rows, rather than a second implementation of the same arithmetic.
"""
import datetime

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import (Base, EloHistory, EloRating, Game, PickModel,
                            PickResult, Team)
from backend.scripts.dedupe_combat_games import (duplicate_groups,
                                                 rebuild_combat_elo,
                                                 run_on_session)

DAY = datetime.date(2026, 3, 21)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def _team(session, name, sport="mma"):
    t = (session.query(Team)
         .filter(Team.sport == sport, Team.abbreviation == name).first())
    if t:
        return t
    t = Team(name=name, abbreviation=name, sport=sport)
    session.add(t)
    session.flush()
    return t


def _bout(session, day, home, away, *, home_score=None, away_score=None,
          sport="mma"):
    status = "final" if home_score is not None else "scheduled"
    g = Game(sport=sport, date=day, status=status, season="2026",
             home_team_id=_team(session, home, sport).id,
             away_team_id=_team(session, away, sport).id,
             home_score=home_score, away_score=away_score)
    session.add(g)
    session.flush()
    return g


def test_a_mirrored_same_date_pair_is_one_group(session):
    a = _bout(session, DAY, "Al-Selwady", "Shem Rock", home_score=1, away_score=0)
    b = _bout(session, DAY, "Shem Rock", "Al-Selwady", home_score=0, away_score=1)
    session.commit()

    groups = duplicate_groups(session, "mma")

    assert len(groups) == 1
    assert groups[0].keep == a.id, "the lowest id is the copy that stands"
    assert groups[0].drop == [b.id]


def test_the_same_pair_on_a_different_date_is_left_alone(session):
    """06-14 against 06-15 is one card under two date conventions, and two
    fighters meeting twice is a normal rematch. Either way, not ours."""
    _bout(session, DAY, "Fighter A", "Fighter B", home_score=1, away_score=0)
    _bout(session, DAY + datetime.timedelta(days=1), "Fighter B", "Fighter A",
          home_score=0, away_score=1)
    session.commit()

    assert duplicate_groups(session, "mma") == []


def test_contradictory_winners_are_refused_not_resolved(session):
    """Disagreement means one copy has been grading picks backwards."""
    _bout(session, DAY, "Fighter A", "Fighter B", home_score=1, away_score=0)
    _bout(session, DAY, "Fighter A", "Fighter B", home_score=0, away_score=1)
    session.commit()

    summary = run_on_session(session, "mma", apply=True)

    assert summary["deleted"] == 0
    assert summary["refused_conflict"] == 1


def test_the_redundant_copy_and_everything_hanging_off_it_go(session):
    a = _bout(session, DAY, "Al-Selwady", "Shem Rock", home_score=1, away_score=0)
    b = _bout(session, DAY, "Shem Rock", "Al-Selwady", home_score=0, away_score=1)
    pick = PickModel(game_id=b.id, strategy_id=1, pick_type="moneyline",
                     pick_value="HOME", confidence=3, edge_pct=5.0,
                     odds_at_pick=-110)
    session.add(pick)
    session.flush()
    session.add(PickResult(pick_id=pick.id, result="win", payout=0.91))
    session.add(EloHistory(team_id=b.home_team_id, game_id=b.id, sport="mma",
                           rating=1510.0))
    session.commit()
    # Held as plain ints: after a bulk delete the ORM instances are stale,
    # and touching `b.id` triggers a refresh of a row that is gone.
    a_id, b_id, pick_id = a.id, b.id, pick.id

    summary = run_on_session(session, "mma", apply=True)

    assert summary["deleted"] == 1
    session.expire_all()
    assert session.query(Game).filter_by(id=b_id).count() == 0
    assert session.query(Game).filter_by(id=a_id).count() == 1, "survivor stays"
    assert session.query(PickModel).filter_by(game_id=b_id).count() == 0
    assert session.query(PickResult).filter_by(pick_id=pick_id).count() == 0
    assert session.query(EloHistory).filter_by(game_id=b_id).count() == 0
    assert summary["picks_deleted"] == 1
    assert summary["units_removed"] == 0.91


def test_dry_run_is_the_default_and_writes_nothing(session):
    _bout(session, DAY, "Al-Selwady", "Shem Rock", home_score=1, away_score=0)
    b = _bout(session, DAY, "Shem Rock", "Al-Selwady", home_score=0, away_score=1)
    session.commit()
    b_id = b.id

    summary = run_on_session(session, "mma")

    assert summary["deleted"] == 1
    assert session.query(Game).filter_by(id=b_id).count() == 1


def test_another_sport_is_not_touched(session):
    _bout(session, DAY, "Boxer A", "Boxer B", home_score=1, away_score=0,
          sport="boxing")
    _bout(session, DAY, "Boxer B", "Boxer A", home_score=0, away_score=1,
          sport="boxing")
    session.commit()

    assert run_on_session(session, "mma", apply=True)["deleted"] == 0


# --- the Elo replay -------------------------------------------------------

def test_replaying_elo_removes_the_double_count(session):
    """The point of the whole exercise: one bout must move a rating once."""
    _bout(session, DAY, "Winner", "Loser", home_score=1, away_score=0)
    _bout(session, DAY, "Loser", "Winner", home_score=0, away_score=1)
    session.commit()

    # Both copies rated, as the grader would have done.
    from backend.pipeline.grader import _apply_combat_elo_update
    for g in session.query(Game).order_by(Game.id):
        _apply_combat_elo_update(session, g)
    session.commit()
    doubled = (session.query(EloRating)
               .filter_by(team_id=_team(session, "Winner").id).one().rating)

    run_on_session(session, "mma", apply=True)

    single = (session.query(EloRating)
              .filter_by(team_id=_team(session, "Winner").id).one().rating)
    assert single < doubled, "a deduped bout must move the rating less"
    assert session.query(EloHistory).filter_by(sport="mma").count() == 2, \
        "one surviving bout writes one row per fighter"


def test_the_replay_seeds_from_scratch_not_from_current_ratings(session):
    """Elo is a running product: replaying on top of existing ratings would
    apply every bout twice instead of fixing the double count."""
    g = _bout(session, DAY, "Winner", "Loser", home_score=1, away_score=0)
    session.commit()
    from backend.pipeline.grader import _apply_combat_elo_update
    _apply_combat_elo_update(session, g)
    session.commit()
    once = (session.query(EloRating)
            .filter_by(team_id=_team(session, "Winner").id).one().rating)

    rebuild_combat_elo(session, "mma")
    session.commit()

    again = (session.query(EloRating)
             .filter_by(team_id=_team(session, "Winner").id).one().rating)
    assert again == pytest.approx(once), (
        "replaying the same single bout must land on the same rating, not "
        "compound on top of it")
