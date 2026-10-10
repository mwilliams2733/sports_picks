from datetime import date

from backend.analysis.combat_history import Bout, replay


def _b(i, d, a, b, s):
    return Bout(game_id=i, date=date(2020, 1, d), a=a, b=b, a_score=s)


def test_features_are_taken_before_the_bout():                  # Review Focus 2
    bouts = [_b(1, 1, 1, 2, 1.0), _b(2, 2, 1, 3, 1.0)]
    f = replay(bouts, k=24)
    assert (f[0].elo_a, f[0].elo_b, f[0].fights_a, f[0].form_a) == (1500, 1500, 0, 0.5)
    assert f[1].elo_a == 1512 and f[1].fights_a == 1 and f[1].form_a == 1.0
    assert f[1].quality_a == 1488          # opponent 2's CURRENT rating (live rule)
    assert f[1].fights_b == 0 and f[1].quality_b is None


def test_a_bouts_own_result_never_changes_its_features():         # Review Focus 2
    win = replay([_b(1, 1, 1, 2, 1.0), _b(2, 2, 1, 2, 1.0)], k=24)[1]
    loss = replay([_b(1, 1, 1, 2, 1.0), _b(2, 2, 1, 2, 0.0)], k=24)[1]
    assert (win.elo_a, win.form_a, win.quality_a) == (loss.elo_a, loss.form_a, loss.quality_a)
    assert (win.outcome, loss.outcome) == (1.0, 0.0)


def test_form_and_count_use_the_last_five():
    bouts = [_b(i, i, 1, 10 + i, 1.0 if i <= 3 else 0.0) for i in range(1, 8)] + [_b(99, 20, 1, 50, 1.0)]
    last = replay(bouts, k=24)[-1]
    assert last.fights_a == 5 and last.form_a == 1 / 5      # most recent five: L L L L W


def test_replay_matches_the_live_feature_builder(db_engine, db_session):   # Review Focus 3
    from backend.models import Base, Game, Team
    from backend.pipeline.pick_generator import _build_fighter_stats
    from backend.scripts.dedupe_combat_games import rebuild_combat_elo
    Base.metadata.create_all(db_engine)
    for tid in range(1, 5):
        db_session.add(Team(id=tid, name=f"F{tid}", abbreviation=f"F{tid}", sport="mma"))
    db_session.flush()
    spec = [(1, 1, 1, 2, 1, 0), (2, 2, 3, 1, 1, 0), (3, 3, 2, 4, 1, 0)]
    for gid, d, h, a, hs, as_ in spec:
        db_session.add(Game(id=gid, sport="mma", season="2020", date=date(2020, 1, d),
                            home_team_id=h, away_team_id=a, status="final", home_score=hs, away_score=as_))
    db_session.commit()
    rebuild_combat_elo(db_session, "mma")
    live = _build_fighter_stats(db_session, 1, "mma", date(2020, 1, 30))
    bouts = [_b(g, d, h, a, 1.0 if hs > as_ else 0.0) for g, d, h, a, hs, as_ in spec] + [_b(9, 30, 1, 4, 1.0)]
    from backend.analysis.elo import get_k_factor
    f = replay(bouts, k=get_k_factor("mma"))[-1]      # the K rebuild_combat_elo uses
    assert (f.elo_a, f.form_a, f.fights_a) == (live.elo_rating, live.recent_form_score, live.fights_count)
    assert abs(f.quality_a - live.opponent_avg_elo) < 1e-9


def test_two_bouts_on_one_date_never_see_each_other():          # Review Focus 2/3
    # Final review: early UFC tournaments put a fighter in 2-3 bouts in one
    # night, and the CSV lists the final first (lowest id). Live reads only
    # bouts on EARLIER dates, so neither same-day bout may see the other.
    final, semi = _b(1, 1, 1, 2, 1.0), _b(2, 1, 1, 3, 1.0)
    f = replay([final, semi], k=24)
    assert (f[1].fights_a, f[1].elo_a, f[1].form_a) == (0, 1500, 0.5)
    nxt = replay([final, semi, _b(3, 2, 1, 4, 1.0)], k=24)[2]
    assert nxt.fights_a == 2                    # both count from the next date on
