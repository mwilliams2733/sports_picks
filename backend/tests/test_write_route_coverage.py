"""Every route that changes data is guarded, or explicitly allowed open.

Walks the real app's routes, so a write route added later without a guard
fails here. ALLOWED_UNPROTECTED is the only escape hatch; adding to it is a
security decision, not a fix for a failing test.
"""
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute

try:
    # FastAPI 0.141.x wraps an included router lazily instead of copying its
    # routes onto the parent app, so app.routes no longer flattens to plain
    # APIRoute objects. Unwrap it below; on older FastAPI (<0.14x, where
    # include_router copies routes directly) this stays an empty tuple and
    # isinstance() against it is simply never true, so the walk still works
    # unchanged.
    from fastapi.routing import _IncludedRouter
except ImportError:
    _IncludedRouter = ()

from backend.api.auth import require_owner
from backend.api.main import create_app

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: POST /users/ is open by design (spec §6: anyone with the link may join).
#: Task 5 removes the two PIN routes from this set when it guards them.
ALLOWED_UNPROTECTED = {
    ("POST", "/users/"),
    ("POST", "/users/{user_id}/picks"),
    ("POST", "/users/{user_id}/parlay"),
}


def _guards():
    return {require_owner}


def _calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _calls(dep)


def _flatten(routes, prefix=""):
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif isinstance(route, _IncludedRouter):
            yield from _flatten(route.original_router.routes,
                                 prefix + route.include_context.prefix)


def _write_routes(app):
    for path, route in _flatten(app.routes):
        for method in route.methods & WRITE_METHODS:
            yield method, path, set(_calls(route.dependant))


def unguarded_write_routes(app, guards, allowed):
    """Every write route in `app` with none of `guards` in its dependency
    tree, excluding anything in `allowed`. Returns ["METHOD /path", ...] --
    empty means every write route is either guarded or explicitly allowed."""
    return [f"{m} {p}" for m, p, calls in _write_routes(app)
            if not (calls & guards) and (m, p) not in allowed]


def test_every_write_route_is_guarded():
    app = create_app(":memory:")
    assert unguarded_write_routes(app, _guards(), ALLOWED_UNPROTECTED) == []


def test_the_walk_finds_the_write_routes():
    """Guards the guard: an empty walk would pass the test above vacuously."""
    app = create_app(":memory:")
    assert len(list(_write_routes(app))) >= 12


def test_unguarded_write_routes_catches_a_missing_guard():
    """Proof the walk (and unguarded_write_routes) isn't vacuous: never edits
    a real route, builds a synthetic app instead.

    One POST route carries `Depends(require_owner)`, one POST route carries
    no guard at all, mounted through `app.include_router(..., prefix=...)` so
    the `_IncludedRouter`/`_flatten` path is actually exercised (not just a
    bare route on `app` directly), and one GET route is present as a
    distractor that must never show up (it isn't a write method at all).
    """
    app = FastAPI()

    @app.post("/guarded")
    def _guarded(_: None = Depends(require_owner)):
        return {"ok": True}

    @app.get("/read")
    def _read():
        return {"ok": True}

    sub = APIRouter()

    @sub.post("/thing")
    def _unguarded_thing():
        return {"ok": True}

    app.include_router(sub, prefix="/sub")

    result = unguarded_write_routes(app, _guards(), allowed=set())
    assert result == ["POST /sub/thing"]
