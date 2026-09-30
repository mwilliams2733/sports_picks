"""The picks that went out by email are recorded, and graded as emailed.

Before 2026-09-28 nothing kept what the digest sent. Its "last 30 days"
header is every game pick for the sport, emailed or not, so "how have the
picks we sent done?" had no answer.

Graded from the EMAILED snapshot, not the stored pick: game picks are
refreshed in place until kickoff, so the stored price -- or the side --
can change after the email has gone out. Grading reuses grader.grade_pick,
grade_prop_pick and payout_for rather than restating them.
"""
from datetime import date, datetime, timezone

import pytest

import backend.digest.job as job
from backend.database import get_engine, get_session
from backend.digest.record import emailed_record
from backend.digest.selector import DigestPick, DigestSection, select_digest
from backend.models import (Base, EmailedPick, Game, PickModel, PlayerStat,
                            StrategyModel, Team)

DAY = date(2026, 11, 1)


@pytest.fixture
def engine():
    e = get_engine(":memory:")
    Base.metadata.create_all(e)
    s = get_session(e)
    s.add_all([StrategyModel(id=1, name="x", config_json="{}", is_active=True),
               StrategyModel(id=2, name="prop_value", config_json="{}",
                             is_active=True, strategy_type="prop")])
    s.add_all([Team(id=1, name="Home Team", abbreviation="H", sport="nfl"),
               Team(id=2, name="Away Team", abbreviation="A", sport="nfl")])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026", date=DAY, status="scheduled",
               home_team_id=1, away_team_id=2))
    s.flush()
    s.add_all([
        PickModel(id=10, game_id=1, strategy_id=1, pick_type="moneyline",
                  pick_value="HOME ML", confidence=4, edge_pct=6.0,
                  odds_at_pick=-120),
        PickModel(id=11, game_id=1, strategy_id=2, pick_type="prop",
                  pick_value="Q Back Over 220.5 Pass Yards", confidence=5,
                  edge_pct=9.0, odds_at_pick=-110, prop_player="Q Back",
                  prop_market="player_pass_yds"),
    ])
    s.commit()
    s.close()
    return e


def _sections():
    game = DigestPick(sport="nfl", matchup="Away Team at Home Team",
                      pick_value="HOME ML", odds=-120, confidence=4,
                      edge_pct=6.0, rationale="", pick_id=10)
    prop = DigestPick(sport="nfl", matchup="Away Team at Home Team",
                      pick_value="Q Back Over 220.5 Pass Yards", odds=-110,
                      confidence=5, edge_pct=9.0, rationale="", pick_id=11)
    return [DigestSection(sport="nfl", picks=[game], props=[prop])]


def _send(engine, monkeypatch, *, sent=True, enabled=True):
    monkeypatch.setattr(job, "select_digest", lambda *a, **k: _sections())
    monkeypatch.setattr(job, "send_email", lambda *a, **k: sent)
    monkeypatch.delenv("DIGEST_DRY_RUN", raising=False)
    return job.send_daily_digest(
        {"digest": {"enabled": enabled, "recipients": ["x@example.com"]}},
        engine, target_date=DAY)


def _rows(engine):
    s = get_session(engine)
    try:
        return [(r.digest_date, r.pick_id, r.pick_type, r.pick_value, r.odds)
                for r in s.query(EmailedPick).order_by(EmailedPick.pick_id)]
    finally:
        s.close()


# --- recording -------------------------------------------------------------

def test_a_sent_digest_records_every_pick_as_emailed(engine, monkeypatch):
    _send(engine, monkeypatch)

    assert _rows(engine) == [
        (DAY, 10, "moneyline", "HOME ML", -120),
        (DAY, 11, "prop", "Q Back Over 220.5 Pass Yards", -110),
    ]
    s = get_session(engine)
    try:
        stars = {r.pick_id: r.confidence for r in s.query(EmailedPick)}
    finally:
        s.close()
    assert stars == {10: 4, 11: 5}     # as emailed (the DigestPicks' confidence)


def test_a_failed_send_records_nothing(engine, monkeypatch):
    _send(engine, monkeypatch, sent=False)

    assert _rows(engine) == []


def test_a_dry_run_records_nothing(engine, monkeypatch):
    """A preview written to disk reached nobody; it is not an emailed pick."""
    _send(engine, monkeypatch, enabled=False)

    assert _rows(engine) == []


def test_a_resend_the_same_day_does_not_double_count(engine, monkeypatch):
    _send(engine, monkeypatch)
    second = _send(engine, monkeypatch)

    assert len(_rows(engine)) == 2
    # Skipped cleanly, not rejected by the unique constraint and swallowed.
    assert second.get("recorded") == 0
    assert "error" not in second


def test_a_recording_failure_does_not_unsend_the_digest(engine, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(job, "record_emailed", boom)
    result = _send(engine, monkeypatch)

    assert result["sent"] is True
    assert "error" not in result, "a sent digest was reported as failed"


def test_the_selector_carries_the_pick_id(engine):
    s = get_session(engine)
    try:
        sections = select_digest(
            s, DAY, ["nfl"], {"nfl": {"start": "09-05", "end": "02-10"}},
            send_bar={"min_shrunk_edge_pp": 0.0, "min_odds": -100_000, "max_odds": 100_000,
                     "max_game_picks": 10, "max_props": 10, "blend_weight": {}})
    finally:
        s.close()

    ids = {p.pick_id for sec in sections for p in sec.picks + sec.props}
    assert ids == {10, 11}


# --- grading the emailed snapshot ------------------------------------------

def _finish(engine, home, away, *, pass_yards=None):
    s = get_session(engine)
    g = s.get(Game, 1)
    g.status, g.home_score, g.away_score = "final", home, away
    if pass_yards is not None:
        s.add(PlayerStat(player_name="Q Back", stat_type="game_log", team_id=1,
                         game_date=DAY, pass_yards=pass_yards, sport="nfl",
                         source="espn"))
    s.commit()
    s.close()


def _record(engine):
    s = get_session(engine)
    try:
        return {(r.sport, r.kind): r for r in emailed_record(s)}
    finally:
        s.close()


def test_an_unfinished_game_is_pending_not_a_loss(engine, monkeypatch):
    _send(engine, monkeypatch)

    rec = _record(engine)[("nfl", "game")]
    assert (rec.wins, rec.losses, rec.pending) == (0, 0, 1)


def test_a_game_pick_is_graded_at_the_emailed_price(engine, monkeypatch):
    _send(engine, monkeypatch)
    _finish(engine, 24, 17, pass_yards=250)

    rec = _record(engine)[("nfl", "game")]
    assert (rec.wins, rec.losses) == (1, 0)
    assert rec.units == pytest.approx(100 / 120, abs=1e-3)


def test_a_refresh_after_sending_does_not_change_the_emailed_result(
        engine, monkeypatch):
    """The stored pick flips side and price after the email. The email said
    HOME at -120, and that is what the reader saw and could bet."""
    _send(engine, monkeypatch)
    s = get_session(engine)
    p = s.get(PickModel, 10)
    p.pick_value, p.odds_at_pick = "AWAY ML", 110
    s.commit()
    s.close()
    _finish(engine, 24, 17)

    rec = _record(engine)[("nfl", "game")]
    assert (rec.wins, rec.losses) == (1, 0)
    assert rec.units == pytest.approx(100 / 120, abs=1e-3)


def test_a_prop_is_graded_against_the_box_score(engine, monkeypatch):
    _send(engine, monkeypatch)
    _finish(engine, 24, 17, pass_yards=200)   # under 220.5: the Over loses

    rec = _record(engine)[("nfl", "prop")]
    assert (rec.wins, rec.losses, rec.pending) == (0, 1, 0)
    assert rec.units == pytest.approx(-1.0)


def test_a_prop_without_a_box_score_yet_is_pending(engine, monkeypatch):
    _send(engine, monkeypatch)
    _finish(engine, 24, 17)

    assert _record(engine)[("nfl", "prop")].pending == 1


def test_win_rate_counts_decided_results_only(engine, monkeypatch):
    _send(engine, monkeypatch)
    _finish(engine, 24, 17, pass_yards=200)

    game, prop = _record(engine)[("nfl", "game")], _record(engine)[("nfl", "prop")]
    assert game.win_pct == pytest.approx(1.0)
    assert prop.win_pct == pytest.approx(0.0)
