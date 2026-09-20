"""Guards for the ESPN retry policy.

The dangerous outcomes are retrying a 404 -- which is a real answer, and
retrying it spends a request the throttle budget needs elsewhere -- and
retrying forever inside the slowest job in the scheduler.
"""
import httpx
import pytest

from backend.collectors.espn_http import (
    BASE_DELAY,
    MAX_ATTEMPTS,
    MAX_DELAY,
    backoff_delay,
    get_with_retry,
)


class _Client:
    """Returns the given statuses in order, recording how many were asked."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    async def get(self, url, **kwargs):
        self.calls += 1
        code = self.statuses[min(self.calls - 1, len(self.statuses) - 1)]
        return httpx.Response(code, request=httpx.Request("GET", url))


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    async def instant(_seconds):
        return None
    monkeypatch.setattr("backend.collectors.espn_http.asyncio.sleep", instant)


@pytest.mark.asyncio
async def test_a_throttled_request_is_retried_and_can_succeed():
    c = _Client([403, 403, 200])
    resp = await get_with_retry(c, "https://espn/x")
    assert resp.status_code == 200
    assert c.calls == 3


@pytest.mark.asyncio
async def test_a_404_is_not_retried():
    """ESPN saying the athlete has no stats page is a real answer.

    Retrying it spends a request against a budget that is already the reason
    the throttling started.
    """
    c = _Client([404])
    resp = await get_with_retry(c, "https://espn/x")
    assert resp.status_code == 404
    assert c.calls == 1


@pytest.mark.asyncio
async def test_a_200_is_not_retried():
    c = _Client([200])
    await get_with_retry(c, "https://espn/x")
    assert c.calls == 1


@pytest.mark.asyncio
async def test_retries_are_bounded():
    """This runs inside the slowest job in the scheduler."""
    c = _Client([429])
    resp = await get_with_retry(c, "https://espn/x")
    assert c.calls == MAX_ATTEMPTS
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_a_persistent_failure_is_returned_not_raised():
    """Callers already call raise_for_status and handle the error."""
    c = _Client([503])
    resp = await get_with_retry(c, "https://espn/x")
    assert resp.status_code == 503
    with pytest.raises(httpx.HTTPStatusError):
        resp.raise_for_status()


@pytest.mark.asyncio
async def test_5xx_is_retried():
    c = _Client([500, 200])
    assert (await get_with_retry(c, "https://espn/x")).status_code == 200
    assert c.calls == 2


def test_backoff_grows_with_the_attempt_number():
    # rand=1.0 removes the jitter so the ceiling itself is under test.
    ceilings = [backoff_delay(i, rand=lambda: 1.0) for i in range(4)]
    assert ceilings == sorted(ceilings)
    assert ceilings[0] == BASE_DELAY


def test_backoff_is_capped():
    assert backoff_delay(50, rand=lambda: 1.0) == MAX_DELAY


def test_backoff_is_jittered_not_fixed():
    """Equal delays make concurrent collectors retry in lockstep.

    That reproduces the burst that caused the throttling in the first place,
    so the delay must be spread across the range, not pinned to its ceiling.
    """
    assert backoff_delay(3, rand=lambda: 0.0) == 0.0
    assert backoff_delay(3, rand=lambda: 1.0) > 0.0
    assert backoff_delay(3, rand=lambda: 0.5) == pytest.approx(
        backoff_delay(3, rand=lambda: 1.0) / 2)


# --------------------------------------------------------------------------
# Transport failures.
#
# The retry originally looked only at status codes, so a read timeout
# propagated on the first occurrence. An audit sweep died on
# httpx.ReadTimeout after ~200 rapid requests, losing the whole run -- the
# same throttling that produces a 403 also produces a stalled read.
# --------------------------------------------------------------------------

class _FlakyClient:
    """Raises or returns per a scripted sequence of outcomes."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def get(self, url, **kwargs):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, request=httpx.Request("GET", url))


@pytest.mark.asyncio
async def test_a_read_timeout_is_retried_and_can_succeed():
    c = _FlakyClient([httpx.ReadTimeout("slow"), 200])
    resp = await get_with_retry(c, "https://espn/x")
    assert resp.status_code == 200
    assert c.calls == 2


@pytest.mark.asyncio
async def test_a_connect_error_is_retried():
    c = _FlakyClient([httpx.ConnectError("refused"), 200])
    assert (await get_with_retry(c, "https://espn/x")).status_code == 200
    assert c.calls == 2


@pytest.mark.asyncio
async def test_a_persistent_timeout_is_raised_not_swallowed():
    """There is no response to return, so the caller must see the failure."""
    c = _FlakyClient([httpx.ReadTimeout("slow")])
    with pytest.raises(httpx.ReadTimeout):
        await get_with_retry(c, "https://espn/x")
    assert c.calls == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_a_non_transport_error_is_not_retried():
    """A bug in our own code must surface immediately, not four times."""
    c = _FlakyClient([ValueError("bad argument")])
    with pytest.raises(ValueError):
        await get_with_retry(c, "https://espn/x")
    assert c.calls == 1


@pytest.mark.asyncio
async def test_timeouts_and_throttles_share_one_attempt_budget():
    """Otherwise a request alternating the two could retry indefinitely."""
    c = _FlakyClient([httpx.ReadTimeout("slow"), 403,
                      httpx.ReadTimeout("slow"), 403, 403, 403])
    resp = await get_with_retry(c, "https://espn/x")
    assert c.calls == MAX_ATTEMPTS
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_a_timeout_after_a_throttle_still_succeeds():
    c = _FlakyClient([403, httpx.ReadTimeout("slow"), 200])
    assert (await get_with_retry(c, "https://espn/x")).status_code == 200
    assert c.calls == 3


@pytest.mark.asyncio
async def test_the_backoff_is_waited_between_transport_retries(monkeypatch):
    waited = []

    async def record(seconds):
        waited.append(seconds)

    monkeypatch.setattr("backend.collectors.espn_http.asyncio.sleep", record)
    c = _FlakyClient([httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"), 200])
    await get_with_retry(c, "https://espn/x")
    assert len(waited) == 2
    assert all(0.0 <= w <= MAX_DELAY for w in waited)
