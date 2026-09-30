import logging
from datetime import date

from backend.digest.sender import send_email
from backend.digest.job import send_daily_digest

WIRING_SEASONS = {"nfl": {"start": "09-05", "end": "02-10"},
                  "mlb": {"start": "03-27", "end": "10-31"}}

FROZEN_SEND_BAR = {
    "min_shrunk_edge_pp": 3.0, "min_odds": -150, "max_odds": 150,
    "max_game_picks": 3, "max_props": 5, "blend_weight": {},
}


def test_dry_run_writes_file_and_sends_nothing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("backend.digest.sender.httpx.post",
                        lambda *a, **k: calls.append(a) or None)
    out = tmp_path / "digest.html"
    sent = send_email("subj", "<p>hi</p>", "hi", "a@b.c", ["d@e.f"],
                      api_key=None, dry_run_path=str(out))
    assert sent is False
    assert out.read_text(encoding="utf-8") == "<p>hi</p>"
    assert calls == [], "dry run must not call the network"


def test_missing_api_key_does_not_send(monkeypatch):
    # httpx.post MUST be patched. Without it, deleting the `if not api_key`
    # guard would make this test hit api.resend.com for real, take a 401, and
    # still observe sent is False — i.e. pass against the broken code it
    # exists to guard.
    calls = []
    monkeypatch.setattr("backend.digest.sender.httpx.post",
                        lambda *a, **k: calls.append(a) or None)
    sent = send_email("subj", "<p>hi</p>", "hi", "a@b.c", ["d@e.f"], api_key=None)
    assert sent is False
    assert calls == [], "a missing key must not reach the network"


def test_send_failure_never_leaks_the_key(caplog, monkeypatch):
    class Boom(Exception):
        pass

    def _raise(*a, **k):
        raise Boom("failed for url https://api.resend.com/emails key=FAKEKEY123")

    monkeypatch.setattr("backend.digest.sender.httpx.post", _raise)
    caplog.set_level(logging.WARNING)
    sent = send_email("s", "<p>h</p>", "h", "a@b.c", ["d@e.f"], api_key="FAKEKEY123")
    assert sent is False
    assert "FAKEKEY123" not in caplog.text


def test_job_sends_nothing_when_no_sections(tmp_path, monkeypatch):
    from backend.database import get_engine
    from backend.models import Base
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.digest.job.send_email",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not send")))
    result = send_daily_digest(
        {"seasons": {}, "digest": {"enabled": True, "sports": ["nfl"],
                                   "from": "a@b.c", "recipients": ["d@e.f"]}},
        engine, target_date=date(2026, 11, 1))
    assert result["sent"] is False
    assert result["sections"] == 0


def test_job_never_raises(monkeypatch):
    from backend.database import get_engine
    from backend.models import Base
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.digest.job.select_digest",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    result = send_daily_digest({"seasons": {}, "digest": {"enabled": True}},
                               engine, target_date=date(2026, 11, 1))
    assert result["sent"] is False
    assert "error" in result


def test_digest_job_absent_when_disabled():
    from backend.pipeline.scheduler import configure_scheduler
    from backend.database import get_engine
    sched = configure_scheduler({"database_path": ":memory:", "digest": {"enabled": False}},
                                get_engine(":memory:"))
    assert sched.get_job("daily_digest") is None


def test_digest_job_registered_when_enabled():
    from backend.pipeline.scheduler import configure_scheduler
    from backend.database import get_engine
    sched = configure_scheduler({"database_path": ":memory:",
                                 "digest": {"enabled": True, "send_hour_et": 11}},
                                get_engine(":memory:"))
    job = sched.get_job("daily_digest")
    assert job is not None


def test_dry_run_bad_path_does_not_raise(monkeypatch):
    calls = []
    monkeypatch.setattr("backend.digest.sender.httpx.post",
                        lambda *a, **k: calls.append(a) or None)
    bad_path = "/no/such/directory/digest.html"
    sent = send_email("subj", "<p>hi</p>", "hi", "a@b.c", ["d@e.f"],
                      api_key=None, dry_run_path=bad_path)
    assert sent is False
    assert calls == [], "dry run must not call the network"


def test_send_email_rejects_scalar_recipients(monkeypatch):
    calls = []
    monkeypatch.setattr("backend.digest.sender.httpx.post",
                        lambda *a, **k: calls.append(a) or None)
    sent = send_email("subj", "<p>hi</p>", "hi", "a@b.c", "a@b.c",
                      api_key="FAKEKEY123")
    assert sent is False
    assert calls == [], "a bare string recipient must not reach the network"


def test_job_never_raises_when_get_session_fails(monkeypatch):
    from backend.database import get_engine
    engine = get_engine(":memory:")

    def _raise(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("backend.digest.job.get_session", _raise)
    result = send_daily_digest({"seasons": {}, "digest": {"enabled": True}},
                               engine, target_date=date(2026, 11, 1))
    assert result["sent"] is False
    assert "error" in result


# --- wiring: the job's own path (selector + render), sender mocked ---------

def _seed_wiring_nfl_game(engine, target_date, picks):
    """picks: list of (pick_value, edge_pct, odds, model_prob), all on one game."""
    from backend.database import get_session
    from backend.models import Team, Game, StrategyModel, PickModel
    session = get_session(engine)
    session.add(StrategyModel(id=1, name="x", config_json="{}", is_active=True))
    session.add_all([
        Team(id=1, name="Home", abbreviation="Home", sport="nfl"),
        Team(id=2, name="Away", abbreviation="Away", sport="nfl"),
    ])
    session.flush()
    session.add(Game(id=1, sport="nfl", season="2026", date=target_date,
                     home_team_id=1, away_team_id=2, status="scheduled"))
    session.flush()
    for pv, edge, odds, prob in picks:
        session.add(PickModel(game_id=1, strategy_id=1, pick_type="moneyline",
                              pick_value=pv, confidence=3, edge_pct=edge,
                              odds_at_pick=odds, model_prob=prob))
    session.commit()
    session.close()


def test_wiring_empty_day_email_when_lambda_is_zero_for_every_sport(monkeypatch, caplog):
    """(a) lambda=0 for all sports -> nothing clears the bar -> the job sends
    the empty-day email, with a reason line per in-season sport and no stars.
    nfl has games (9 generated picks, all priced in range, none clear the
    bar since lambda is 0). mlb is in season on this date but has no games
    at all today, so its line reads "no picks generated today."."""
    from backend.database import get_engine
    from backend.models import Base
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    d = date(2026, 9, 20)  # in season for both nfl and mlb
    picks = [(f"P1-{i}", 5.0 + i, -110, 0.55) for i in range(9)]
    _seed_wiring_nfl_game(engine, d, picks)

    captured = {}
    def _fake_send(subject, html, text, from_, recipients, api_key=None, dry_run_path=None):
        captured.update(subject=subject, html=html, text=text)
        return True
    monkeypatch.setattr("backend.digest.job.send_email", _fake_send)

    def _boom_record(*a, **k):
        raise AssertionError("an empty-day send must not record any picks")
    monkeypatch.setattr("backend.digest.job.record_emailed", _boom_record)

    caplog.set_level(logging.INFO)
    cfg = {"seasons": WIRING_SEASONS,
          "digest": {"enabled": True, "sports": ["nfl", "mlb"],
                     "send_bar": FROZEN_SEND_BAR,
                     "from": "a@b.c", "recipients": ["d@e.f"]}}
    result = send_daily_digest(cfg, engine, target_date=d)

    assert result["sent"] is True
    assert result["sections"] == 0
    assert result["picks"] == 0
    assert "No qualifying picks" in captured["subject"]
    assert "NFL: 9 picks generated, none cleared the 3-point bar (model weight 0.00)." in captured["text"]
    assert "MLB: no picks generated today." in captured["text"]
    assert "★" not in captured["html"] and "☆" not in captured["html"]
    assert "★" not in captured["text"] and "☆" not in captured["text"]
    assert "NFL: 9 picks generated" in caplog.text, "the same reason must be logged at INFO"


def test_wiring_ranks_by_shrunk_edge_and_caps_at_three_game_picks(monkeypatch):
    """(b) lambda=0.5 for nfl: picks are ranked by shrunk edge (lambda *
    edge_pct), not by model probability -- chosen so the two orders
    disagree -- and at most 3 game picks go out."""
    from backend.database import get_engine
    from backend.models import Base
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    d = date(2026, 9, 20)
    # shrunk = 0.5 * edge_pct:
    #   P1-0: edge  4.0 -> shrunk 2.0  (fails the 3.0 floor)
    #   P1-1: edge 18.0 -> shrunk 9.0  (highest shrunk, LOWEST model_prob)
    #   P1-2: edge  9.0 -> shrunk 4.5
    #   P1-3: edge  7.0 -> shrunk 3.5  (cut by the cap of 3)
    #   P1-4: edge 30.0 -> shrunk 15.0 (highest shrunk of all)
    picks = [
        ("P1-0", 4.0, -110, 0.90),
        ("P1-1", 18.0, -110, 0.40),
        ("P1-2", 9.0, -110, 0.55),
        ("P1-3", 7.0, -110, 0.52),
        ("P1-4", 30.0, -110, 0.35),
    ]
    _seed_wiring_nfl_game(engine, d, picks)

    captured = {}
    def _fake_send(subject, html, text, from_, recipients, api_key=None, dry_run_path=None):
        captured.update(subject=subject, html=html, text=text)
        return True
    monkeypatch.setattr("backend.digest.job.send_email", _fake_send)

    send_bar = {**FROZEN_SEND_BAR, "blend_weight": {"nfl": 0.5}}
    cfg = {"seasons": WIRING_SEASONS,
          "digest": {"enabled": True, "sports": ["nfl"], "send_bar": send_bar,
                     "from": "a@b.c", "recipients": ["d@e.f"]}}
    result = send_daily_digest(cfg, engine, target_date=d)

    assert result["sent"] is True
    assert result["picks"] == 3, "at most max_game_picks=3 game picks go out"
    assert "P1-0" not in captured["text"], "below the shrunk-edge floor"
    assert "P1-3" not in captured["text"], "cut by the cap"
    # Order in the body must follow shrunk edge (P1-4 > P1-1 > P1-2), the
    # opposite of model-probability order (P1-2 > P1-1 > P1-4).
    i4, i1, i2 = (captured["text"].index(v) for v in ("P1-4", "P1-1", "P1-2"))
    assert i4 < i1 < i2
    assert "★" not in captured["text"] and "☆" not in captured["text"]
