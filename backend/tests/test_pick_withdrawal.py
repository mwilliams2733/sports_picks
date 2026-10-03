"""A stored pick that stops qualifying is withdrawn, not left published.

The generator refreshed a pick in place while it still qualified, but never
removed one that stopped: if the market moved, or the edge definition
changed (2026-10-03, when edges moved over break-even), the old pick stayed
on the site, in the record and in the digest's pool with an edge that no
longer existed.

Withdrawal is soft. The row keeps its last values and gains `withdrawn_at`.
`PickModel.published()` excludes it, grading skips it, and `pick_versions`
records the event. Three kinds of pick are never withdrawn, because each
is already history:

* a graded pick -- it is a result;
* a pick whose game has started -- its price was the last takeable one;
* an emailed pick -- people were told to bet it.
"""
import datetime

import pytest

from backend.models import (EmailedPick, Game, PickModel, PickResult,
                            PickVersion)
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.tests.test_tracked_markets import (D, ML, SPREAD, _stub_predict,  # noqa: F401
                                                gen_session)


def _run(session, monkeypatch, picks):
    _stub_predict(monkeypatch, picks)
    generate_and_store_picks(session, 1, D)


def _pick(session, pick_type="moneyline"):
    return session.query(PickModel).filter_by(pick_type=pick_type).one()


def test_a_pick_that_stops_qualifying_is_withdrawn(gen_session, monkeypatch):
    _run(gen_session, monkeypatch, [ML])

    _run(gen_session, monkeypatch, [])

    pick = _pick(gen_session)
    assert pick.withdrawn_at is not None
    assert gen_session.query(PickModel).filter(PickModel.published()).count() == 0


def test_the_withdrawal_is_versioned(gen_session, monkeypatch):
    _run(gen_session, monkeypatch, [ML])
    _run(gen_session, monkeypatch, [])

    versions = (gen_session.query(PickVersion)
                .filter_by(pick_id=_pick(gen_session).id)
                .order_by(PickVersion.version).all())

    assert [(v.source, v.withdrawn) for v in versions] == [
        ("insert", False), ("withdraw", True)]


def test_a_pick_that_qualifies_again_is_reinstated(gen_session, monkeypatch):
    _run(gen_session, monkeypatch, [ML])
    _run(gen_session, monkeypatch, [])

    _run(gen_session, monkeypatch, [ML])

    pick = _pick(gen_session)
    assert pick.withdrawn_at is None
    last = (gen_session.query(PickVersion).filter_by(pick_id=pick.id)
            .order_by(PickVersion.version.desc()).first())
    assert (last.source, last.withdrawn) == ("refresh", False)


def test_only_the_market_that_vanished_is_withdrawn(gen_session, monkeypatch):
    _run(gen_session, monkeypatch, [ML, SPREAD])

    _run(gen_session, monkeypatch, [ML])

    assert _pick(gen_session, "moneyline").withdrawn_at is None
    assert _pick(gen_session, "spread").withdrawn_at is not None


def test_a_started_game_keeps_its_pick(gen_session, monkeypatch):
    _run(gen_session, monkeypatch, [ML])
    gen_session.query(Game).filter_by(id=1).update(
        {"start_time": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
         - datetime.timedelta(minutes=5)})
    gen_session.commit()

    # skip_started=False so the run still evaluates the game.
    _stub_predict(monkeypatch, [])
    generate_and_store_picks(gen_session, 1, D, skip_started=False)

    assert _pick(gen_session).withdrawn_at is None


def test_a_graded_pick_is_never_withdrawn(gen_session, monkeypatch):
    _run(gen_session, monkeypatch, [ML])
    gen_session.add(PickResult(pick_id=_pick(gen_session).id, result="win", payout=0.6))
    gen_session.commit()

    _run(gen_session, monkeypatch, [])

    assert _pick(gen_session).withdrawn_at is None


def test_an_emailed_pick_is_never_withdrawn(gen_session, monkeypatch):
    _run(gen_session, monkeypatch, [ML])
    pick = _pick(gen_session)
    gen_session.add(EmailedPick(pick_id=pick.id, game_id=1, digest_date=D, pick_type="moneyline",
                                pick_value=pick.pick_value, odds=pick.odds_at_pick,
                                sport="nfl",
                                sent_at=datetime.datetime.now(datetime.timezone.utc)))
    gen_session.commit()

    _run(gen_session, monkeypatch, [])

    assert _pick(gen_session).withdrawn_at is None


def test_a_failed_prediction_withdraws_nothing(gen_session, monkeypatch):
    """No answer is not the answer "no pick"."""
    from backend.analysis.variants.ensemble import EnsembleStrategy
    _run(gen_session, monkeypatch, [ML])

    def boom(self, game):
        raise RuntimeError("stats source down")
    monkeypatch.setattr(EnsembleStrategy, "predict", boom)
    generate_and_store_picks(gen_session, 1, D)

    assert _pick(gen_session).withdrawn_at is None


def test_a_withdrawn_pick_is_not_graded(gen_session, monkeypatch):
    from backend.pipeline.scheduler import grade_pending_picks
    _run(gen_session, monkeypatch, [ML])
    _run(gen_session, monkeypatch, [])
    gen_session.query(Game).filter_by(id=1).update(
        {"status": "final", "home_score": 24, "away_score": 17})
    gen_session.commit()

    grade_pending_picks(gen_session)

    assert gen_session.query(PickResult).count() == 0


def test_the_digest_health_check_ignores_a_withdrawn_pick(gen_session, monkeypatch):
    from backend.scripts.check_digest import picks_for
    _run(gen_session, monkeypatch, [ML])
    _run(gen_session, monkeypatch, [])
    path = gen_session.get_bind().url.database

    assert picks_for(path, D) == 0


def test_an_unchanged_refresh_of_an_old_pick_writes_no_version(gen_session, monkeypatch):
    """Version rows written before `withdrawn` existed read False, not None,
    so the first refresh after this change must not mint a spurious
    version for every live pick."""
    _run(gen_session, monkeypatch, [ML])
    before = gen_session.query(PickVersion).count()

    _run(gen_session, monkeypatch, [ML])

    assert gen_session.query(PickVersion).count() == before
