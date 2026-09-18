"""A WebSocket transport must actually be installed.

This is a dependency guard, not a functional test, and it exists because no
functional test can catch this. `TestClient` implements WebSocket *in-process*
using Starlette's own code and never touches uvicorn's transport -- so the 15
tests covering /ws all passed while the feature was dead in every real
deployment.

Observed when the app was launched for real:

    WARNING: No supported WebSocket library detected. Please use
             "pip install 'uvicorn[standard]'", or install 'websockets' or
             'wsproto' manually.
    INFO:    "GET /ws HTTP/1.1" 200 OK        <- 200, not 101

uvicorn could not upgrade the connection, so the browser retried with
backoff (2s, 3s, 4s, 8s, 16s) and never connected. The route, the dispatch and
the frontend URL were all correct; only the transport was missing.
"""
import importlib.util


def test_uvicorn_can_upgrade_a_websocket_connection():
    """uvicorn needs `websockets` or `wsproto` to serve /ws.

    Without one it answers the upgrade request with a plain 200 and the
    activity feed never connects -- in local runs and in the Docker image,
    which installs from the same pyproject.
    """
    installed = [name for name in ("websockets", "wsproto")
                 if importlib.util.find_spec(name) is not None]

    assert installed, (
        "no WebSocket transport installed, so uvicorn cannot serve /ws. "
        "Add 'websockets' to pyproject.toml dependencies (or use "
        "uvicorn[standard]) and regenerate constraints.txt."
    )


def test_the_transport_is_a_declared_dependency_not_an_accident():
    """It has to be in pyproject, or a fresh install -- including the Docker
    build -- silently loses it again."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert re.search(r'"(websockets|wsproto|uvicorn\[standard\])', pyproject), (
        "no WebSocket transport is declared in pyproject.toml; the installed "
        "one is incidental and will not survive a fresh install"
    )
