"""Test-wide safety net: no test may read this machine's real secrets.

`backend.config.resolve_odds_api_key` falls back to the shared secrets file
(`~/.secrets/shared.env`) when `ODDS_API_KEY` is absent from the environment.
That is correct in production and wrong under pytest: a test that deletes the
env var would otherwise pick up a real, live API key, and could spend credits
or leak the value into an assertion message.

This fixture points `SHARED_ENV_PATH` at an empty temporary file for every
test. Tests that want to exercise the fallback pass an explicit path to
`resolve_odds_api_key(shared_env_path=...)` or set `SHARED_ENV_PATH`
themselves, which overrides this.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_shared_secrets(monkeypatch, tmp_path_factory):
    empty = tmp_path_factory.mktemp("secrets") / "shared.env"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("SHARED_ENV_PATH", str(empty))
