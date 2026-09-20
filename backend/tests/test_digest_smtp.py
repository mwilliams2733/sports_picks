"""Guards for sending the digest over Gmail SMTP.

The sender only spoke Resend, and no RESEND_API_KEY exists; the shared
secrets file carries GMAIL_USER and GMAIL_APP_PASSWORD instead. The digest
therefore logged "not sent" every day it was enabled.

The dangerous outcomes here are a credential reaching a log line, and a
delivery failure propagating into the scheduler and taking the nightly run
down with it. `send_email` must never raise.
"""
import logging
import smtplib

import pytest

from backend.digest import sender as sender_mod
from backend.digest.sender import resolve_gmail_credentials, send_email

SUBJECT, HTML, TEXT = "Picks", "<p>hi</p>", "hi"
TO = ["a@example.com", "b@example.com"]
PW = "hunter2-app-password"


class _FakeSMTP:
    """Records what it was asked to send instead of touching the network."""
    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.logged_in_as = None
        self.sent = None
        self.quit_called = False
        _FakeSMTP.instances.append(self)

    def __enter__(self): return self
    def __exit__(self, *a): self.quit_called = True; return False
    def login(self, user, password): self.logged_in_as = (user, password)
    def send_message(self, msg): self.sent = msg


@pytest.fixture(autouse=True)
def _reset():
    _FakeSMTP.instances = []


def _patch(monkeypatch, cls=_FakeSMTP):
    monkeypatch.setattr(sender_mod.smtplib, "SMTP_SSL", cls)


def test_credentials_come_from_the_environment_first(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", PW)
    assert resolve_gmail_credentials() == ("me@gmail.com", PW)


def test_missing_credentials_send_nothing(monkeypatch, caplog):
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(sender_mod, "resolve_gmail_credentials", lambda *a, **k: (None, None))
    _patch(monkeypatch)

    assert send_email(SUBJECT, HTML, TEXT, "x@y.z", TO) is False
    assert _FakeSMTP.instances == []


def test_no_recipients_sends_nothing(monkeypatch):
    monkeypatch.setattr(sender_mod, "resolve_gmail_credentials",
                        lambda *a, **k: ("me@gmail.com", PW))
    _patch(monkeypatch)
    assert send_email(SUBJECT, HTML, TEXT, "x@y.z", []) is False
    assert _FakeSMTP.instances == []


def test_a_configured_send_reaches_every_recipient(monkeypatch):
    monkeypatch.setattr(sender_mod, "resolve_gmail_credentials",
                        lambda *a, **k: ("me@gmail.com", PW))
    _patch(monkeypatch)

    assert send_email(SUBJECT, HTML, TEXT, "ignored@example.com", TO) is True

    smtp = _FakeSMTP.instances[0]
    assert smtp.logged_in_as == ("me@gmail.com", PW)
    assert smtp.sent["To"] == "a@example.com, b@example.com"
    assert smtp.sent["Subject"] == SUBJECT
    # Gmail rewrites a From it does not own, so the authenticated account is
    # the only honest sender. A placeholder in config must not be used.
    assert smtp.sent["From"] == "me@gmail.com"


def test_both_a_text_and_an_html_part_are_attached(monkeypatch):
    monkeypatch.setattr(sender_mod, "resolve_gmail_credentials",
                        lambda *a, **k: ("me@gmail.com", PW))
    _patch(monkeypatch)
    send_email(SUBJECT, HTML, TEXT, "x@y.z", TO)

    types = {p.get_content_type() for p in _FakeSMTP.instances[0].sent.walk()}
    assert "text/plain" in types and "text/html" in types


def test_a_failure_returns_false_and_never_raises(monkeypatch):
    class _Boom(_FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad creds")

    monkeypatch.setattr(sender_mod, "resolve_gmail_credentials",
                        lambda *a, **k: ("me@gmail.com", PW))
    _patch(monkeypatch, _Boom)

    assert send_email(SUBJECT, HTML, TEXT, "x@y.z", TO) is False


def test_the_app_password_never_reaches_a_log(monkeypatch, caplog):
    """A failure path that interpolates the exception must not leak it."""
    class _Leaky(_FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPException(f"auth failed for {user} with {password}")

    monkeypatch.setattr(sender_mod, "resolve_gmail_credentials",
                        lambda *a, **k: ("me@gmail.com", PW))
    _patch(monkeypatch, _Leaky)

    with caplog.at_level(logging.DEBUG):
        send_email(SUBJECT, HTML, TEXT, "x@y.z", TO)

    assert PW not in caplog.text


def test_dry_run_still_writes_and_sends_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(sender_mod, "resolve_gmail_credentials",
                        lambda *a, **k: ("me@gmail.com", PW))
    _patch(monkeypatch)
    out = tmp_path / "preview.html"

    assert send_email(SUBJECT, HTML, TEXT, "x@y.z", TO,
                      dry_run_path=str(out)) is False
    assert out.read_text(encoding="utf-8") == HTML
    assert _FakeSMTP.instances == []


def test_an_app_password_is_stripped_of_its_display_spaces(monkeypatch):
    """Gmail shows app passwords as four space-separated groups, and that is
    how they get pasted into a secrets file. Send-time is the wrong place to
    discover the spaces were carried through."""
    monkeypatch.setenv("GMAIL_USER", "  me@gmail.com ")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    assert resolve_gmail_credentials() == ("me@gmail.com", "abcdefghijklmnop")
