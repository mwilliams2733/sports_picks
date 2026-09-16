import logging
from datetime import date

from backend.digest.sender import send_email
from backend.digest.job import send_daily_digest


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


def test_missing_api_key_does_not_send(tmp_path):
    sent = send_email("subj", "<p>hi</p>", "hi", "a@b.c", ["d@e.f"], api_key=None)
    assert sent is False


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
