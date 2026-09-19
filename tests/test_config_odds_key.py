"""Where the Odds API key comes from.

The key was rotated, the new value went into the shared secrets file under
`TheODDSAPI`, and the pipeline kept reading a stale `ODDS_API_KEY` from a
repo-local `.env` dated March 2026. Every call returned 401 and the pipeline
produced no picks for months' worth of scheduled runs while looking healthy.
These tests pin the resolution order so that cannot recur silently.
"""

import textwrap

import pytest
import yaml

from backend.config import resolve_odds_api_key


@pytest.fixture()
def cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"seasons": {}}), encoding="utf-8")
    return str(p)


@pytest.fixture()
def shared_env(tmp_path):
    def _write(body):
        f = tmp_path / "shared.env"
        f.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
        return str(f)
    return _write


def test_environment_wins_over_the_shared_file(monkeypatch, shared_env):
    """Docker and deploy/*.service inject ODDS_API_KEY; that must keep working."""
    path = shared_env("TheODDSAPI=from-file")
    monkeypatch.setenv("ODDS_API_KEY", "from-env")

    assert resolve_odds_api_key(shared_env_path=path) == "from-env"


def test_falls_back_to_the_shared_file_under_its_own_name(monkeypatch, shared_env):
    path = shared_env("TheODDSAPI=rotated-key")
    monkeypatch.delenv("ODDS_API_KEY", raising=False)

    assert resolve_odds_api_key(shared_env_path=path) == "rotated-key"


def test_shared_file_may_also_use_the_canonical_name(monkeypatch, shared_env):
    path = shared_env("ODDS_API_KEY=canonical")
    monkeypatch.delenv("ODDS_API_KEY", raising=False)

    assert resolve_odds_api_key(shared_env_path=path) == "canonical"


def test_other_secrets_in_the_file_are_ignored(monkeypatch, shared_env):
    path = shared_env(
        """
        GMAIL_USER=someone@example.com
        TheODDSAPI=the-right-one
        POLYGON_API_KEY=unrelated
        """
    )
    monkeypatch.delenv("ODDS_API_KEY", raising=False)

    assert resolve_odds_api_key(shared_env_path=path) == "the-right-one"


def test_quotes_and_blank_lines_and_comments_are_handled(monkeypatch, shared_env):
    path = shared_env(
        """
        # a comment

        TheODDSAPI="quoted-value"
        """
    )
    monkeypatch.delenv("ODDS_API_KEY", raising=False)

    assert resolve_odds_api_key(shared_env_path=path) == "quoted-value"


def test_a_missing_shared_file_returns_none_rather_than_raising(monkeypatch, tmp_path):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)

    assert resolve_odds_api_key(shared_env_path=str(tmp_path / "nope.env")) is None


def test_load_config_uses_the_resolved_key(monkeypatch, cfg, shared_env):
    from backend.config import load_config

    path = shared_env("TheODDSAPI=rotated-key")
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("SHARED_ENV_PATH", path)

    assert load_config(cfg)["odds_api_key"] == "rotated-key"


def test_load_config_omits_the_key_when_none_is_found(monkeypatch, cfg, tmp_path):
    from backend.config import load_config

    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("SHARED_ENV_PATH", str(tmp_path / "nope.env"))

    # Absent rather than None: callers use config.get("odds_api_key"), and a
    # present-but-empty key would be sent to the API and 401.
    assert "odds_api_key" not in load_config(cfg)


def test_the_key_is_never_logged(monkeypatch, caplog, cfg, shared_env):
    from backend.config import load_config

    path = shared_env("TheODDSAPI=super-secret-value")
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("SHARED_ENV_PATH", path)

    with caplog.at_level("DEBUG"):
        load_config(cfg)

    assert "super-secret-value" not in caplog.text
