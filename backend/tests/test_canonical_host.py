"""The bare domain redirects to www (owner, 2026-10-10).

The browser keeps a friend's chosen player, saved PIN and bet slip per
origin, so one person using both metricedgepicks.com and
www.metricedgepicks.com saw two different states. Every request to the bare
domain is sent, permanently, to the same path and query on www.
"""
from fastapi.testclient import TestClient

from backend.api.main import create_app


def _client():
    return TestClient(create_app(":memory:"))


def test_a_page_on_the_bare_domain_moves_to_www_with_path_and_query():
    r = _client().get("/leaders?x=1", headers={"host": "metricedgepicks.com"}, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "https://www.metricedgepicks.com/leaders?x=1"


def test_a_non_get_keeps_its_method():
    r = _client().post("/users/1/picks", headers={"host": "metricedgepicks.com"}, json={},
                       follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "https://www.metricedgepicks.com/users/1/picks"


def test_a_forwarded_bare_host_redirects_and_its_port_is_ignored():
    r = _client().get("/", headers={"host": "127.0.0.1:8000", "x-forwarded-host": "MetricEdgePicks.com:443"},
                      follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "https://www.metricedgepicks.com/"


def test_www_and_local_hosts_pass_through():
    c = _client()
    for host in ("www.metricedgepicks.com", "127.0.0.1:8000", "localhost"):
        r = c.get("/health", headers={"host": host}, follow_redirects=False)
        assert r.status_code == 200, host
