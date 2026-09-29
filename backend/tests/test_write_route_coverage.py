"""Every route that changes data is guarded, or explicitly allowed open.

Walks the real app's routes, so a write route added later without a guard
fails here. ALLOWED_UNPROTECTED is the only escape hatch; adding to it is a
security decision, not a fix for a failing test.
"""
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

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

from backend.api.main import create_app

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: POST /users/ is open by design (spec §6: anyone with the link may join).
ALLOWED_UNPROTECTED = {
    ("POST", "/users/"),
}


def _guards():
    from backend.api.auth import require_owner
    from backend.api.pins import require_player_pin
    return {require_owner, require_player_pin}


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
        elif isinstance(route, APIWebSocketRoute):
            continue  # not an HTTP write route; nothing to guard here
        elif isinstance(route, Mount) and isinstance(route.app, StaticFiles):
            continue  # static assets, not a write route
        elif (isinstance(route, Route) and route.methods is not None
              and route.methods <= {"GET", "HEAD"}):
            continue  # a plain read route (e.g. the SPA catch-all)
        else:
            raise TypeError(
                f"_flatten does not know how to walk route type {type(route)!r} "
                f"({route!r}); it may hide an unguarded write route. Teach "
                f"_flatten about it explicitly instead of letting it fall through."
            )


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
    no guard at all mounted through `app.include_router(..., prefix=...)` so
    the `_IncludedRouter`/`_flatten` path is actually exercised (not just a
    bare route on `app` directly), a second unguarded POST sits directly on
    `app` (top level, not via include_router) to prove top-level routes are
    walked too, and one GET route is present as a distractor that must never
    show up (it isn't a write method at all).
    """
    from backend.api.auth import require_owner

    app = FastAPI()

    @app.post("/guarded")
    def _guarded(_: None = Depends(require_owner)):
        return {"ok": True}

    @app.post("/top-level-unguarded")
    def _top_level_unguarded():
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
    assert sorted(result) == sorted(["POST /sub/thing", "POST /top-level-unguarded"])


def test_flatten_raises_on_an_unrecognised_route_type():
    """`_flatten` must not silently drop a route type it doesn't recognise --
    that would hide an unguarded write route rather than report it. A raw
    starlette `Route` added via `app.add_route` with a non-GET/HEAD method is
    exactly such a route; the walk must fail loudly naming its type."""
    app = FastAPI()

    def _handler(request):
        return None

    app.add_route("/raw", _handler, methods=["POST"])

    with pytest.raises(TypeError):
        list(_flatten(app.routes))


def test_flatten_raises_on_a_class_based_endpoint_with_no_declared_methods():
    """A starlette `Route` built from an `HTTPEndpoint` subclass (or any raw
    ASGI endpoint) has `route.methods is None` -- it accepts every HTTP verb,
    dispatched dynamically per-request, not just GET/HEAD. Treating
    `methods is None` as "read-only" (e.g. `(route.methods or set()) <=
    {"GET", "HEAD"}`, which is vacuously true for `None`) would silently wave
    through a class-based route that defines `post`. The walk must raise
    instead of skipping it."""
    from starlette.endpoints import HTTPEndpoint

    class Writer(HTTPEndpoint):
        async def post(self, request):
            return None

    app = FastAPI()
    app.add_route("/cls", Writer)

    with pytest.raises(TypeError):
        list(_flatten(app.routes))
