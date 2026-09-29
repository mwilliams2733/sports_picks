"""Owner-only routes refuse everyone without the key -- including when no key
is configured at all (fail closed). DELETE /users/999 answers 404 once past
the guard, which proves the guard let the request through."""
import pytest
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from backend.api.auth import require_owner
from backend.api.main import create_app
from backend.config import resolve_owner_key
from backend.tests.auth_helpers import OWNER_HEADERS


def _client():
    return TestClient(create_app(":memory:"))


def assert_owner_guarded(client, method, path):
    """No key -> 403, wrong key -> 403, right key -> the guard let it through
    (anything other than 403/503; what the route itself then does -- 404,
    200, ... -- is not this helper's concern).
    """
    call = getattr(client, method.lower())
    no_key = call(path)
    assert no_key.status_code == 403, f"no key: expected 403, got {no_key.status_code}"
    wrong_key = call(path, headers={"X-Owner-Key": "nope"})
    assert wrong_key.status_code == 403, (
        f"wrong key: expected 403, got {wrong_key.status_code}")
    right_key = call(path, headers=OWNER_HEADERS)
    assert right_key.status_code not in (403, 503), (
        f"right key: guard still refused, got {right_key.status_code}")


def test_delete_route_is_owner_guarded():
    """DELETE /users/999 answers 404 once past the guard, which proves the
    guard let the request through."""
    client = _client()
    assert_owner_guarded(client, "DELETE", "/users/999")
    assert client.delete("/users/999", headers=OWNER_HEADERS).status_code == 404


def test_an_unconfigured_server_refuses_owner_routes(monkeypatch, tmp_path):
    monkeypatch.delenv("SPORTS_PICKS_OWNER_KEY", raising=False)
    monkeypatch.setenv("SHARED_ENV_PATH", str(tmp_path / "missing.env"))
    r = _client().delete("/users/999", headers=OWNER_HEADERS)
    assert r.status_code == 503


def test_the_key_is_read_from_the_shared_secrets_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SPORTS_PICKS_OWNER_KEY", raising=False)
    env = tmp_path / "shared.env"
    env.write_text("SPORTS_PICKS_OWNER_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("SHARED_ENV_PATH", str(env))
    assert resolve_owner_key() == "from-file"


def test_reads_stay_open():
    assert _client().get("/users/").status_code == 200


def test_assert_owner_guarded_catches_a_guard_that_does_not_refuse():
    """Proof the helper isn't vacuous: never edits backend/api/auth.py or any
    real route, builds a synthetic app with a deliberately lenient dependency
    instead -- one that returns without checking anything, header or not.
    assert_owner_guarded must fail loudly against it, not pass -- on the
    "no key" branch specifically, since this fake never refuses at all.

    `x_owner_key` is declared with `Header(alias="X-Owner-Key")` so the fake
    actually reads the header FastAPI would bind for a real dependency
    (a bare `str | None = None` parameter binds to a QUERY parameter
    instead, which this test never sends -- that mismatch is exactly the
    bug Fix round 1 corrected here).
    """
    app = FastAPI()

    def lenient(x_owner_key: str | None = Header(default=None, alias="X-Owner-Key")):
        return None  # never refuses, regardless of the header

    @app.delete("/lenient/{item_id}")
    def _lenient_route(item_id: int, _: None = Depends(lenient)):
        return {"ok": True}

    with pytest.raises(AssertionError) as excinfo:
        assert_owner_guarded(TestClient(app), "DELETE", "/lenient/1")
    assert str(excinfo.value).startswith("no key:"), str(excinfo.value)


def test_assert_owner_guarded_catches_a_guard_that_ignores_the_key_value():
    """A second lenient dependency: this one DOES refuse a missing header
    (unlike the one above), but accepts any value at all for it -- it never
    checks the key is right. assert_owner_guarded's "wrong key" branch must
    catch this: a wrong key must still be refused, not merely "some header
    present". Proof the helper isn't vacuous on that branch specifically.

    `x_owner_key` is declared with `Header(alias="X-Owner-Key")` -- see the
    note on the previous test. Without it this fake reads a query parameter
    that is never sent, so it is always None and the fake refuses every
    request including the right key, tripping the helper's "right key"
    assertion instead of the "wrong key" one this test means to exercise.
    """
    app = FastAPI()

    def checks_presence_only(x_owner_key: str | None = Header(default=None, alias="X-Owner-Key")):
        if x_owner_key is None:
            raise HTTPException(status_code=403, detail="no key")
        return None  # any non-None value is accepted, right or wrong

    @app.delete("/present-only/{item_id}")
    def _route(item_id: int, _: None = Depends(checks_presence_only)):
        return {"ok": True}

    with pytest.raises(AssertionError) as excinfo:
        assert_owner_guarded(TestClient(app), "DELETE", "/present-only/1")
    assert str(excinfo.value).startswith("wrong key:"), str(excinfo.value)


def test_require_owner_fails_closed_on_a_fake_route(monkeypatch, tmp_path):
    """The REAL require_owner, wired onto a synthetic route (not a real admin
    route), still fails closed when no key is configured anywhere -- even
    with a header present. Proves fail-closed is a property of require_owner
    itself, not of any particular route's plumbing.
    """
    monkeypatch.delenv("SPORTS_PICKS_OWNER_KEY", raising=False)
    monkeypatch.setenv("SHARED_ENV_PATH", str(tmp_path / "missing.env"))

    app = FastAPI()

    @app.post("/fake-admin")
    def _fake_admin(_: None = Depends(require_owner)):
        return {"ok": True}

    resp = TestClient(app).post("/fake-admin", headers=OWNER_HEADERS)
    assert resp.status_code == 503
