"""One retry policy for every ESPN request.

ESPN has no published rate limit and no `Retry-After` header, but it does
start returning 403 once a client asks quickly enough for long enough. A
single forced scout on 2026-09-19 made 3172 requests and collected 128
403/429 responses -- each one a silently dropped player, because the caller
logged a warning and moved on.

The retry is deliberately narrow:

* Only 429 and 403 are retried, plus 5xx. A 404 is ESPN saying the athlete
  has no stats page, which is a real answer and retrying it wastes a request
  and delays every later one.
* Transport failures are retried too -- timeouts, refused connections, reads
  that die mid-response. The same throttling that returns a 403 also stalls
  a read, so treating one as retryable and the other as fatal made no sense:
  an audit sweep lost a whole run to ``httpx.ReadTimeout`` after about 200
  rapid requests. Anything that is not a transport failure is a bug in this
  code and propagates on the first occurrence.
* Exponential backoff with full jitter. Fixed delays from many workers
  re-synchronise into the same bursts that caused the throttling.
* A small, bounded number of attempts. This runs inside a pipeline that is
  already the slowest thing in the scheduler; retrying forever would turn a
  throttled run into one that never finishes.
"""
import asyncio
import logging
import random

import httpx

logger = logging.getLogger(__name__)

#: Statuses worth asking again about. 403 is here because ESPN uses it for
#: throttling rather than the 429 the status code list would suggest.
RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})

MAX_ATTEMPTS = 4
BASE_DELAY = 0.5
MAX_DELAY = 8.0


def backoff_delay(attempt: int, *, rand=random.random) -> float:
    """Full-jitter exponential backoff for a zero-based attempt number.

    Full jitter rather than a fixed multiple: several collectors retry against
    the same host at once, and equal delays make them retry in lockstep,
    reproducing the burst that triggered the throttling.
    """
    ceiling = min(MAX_DELAY, BASE_DELAY * (2 ** attempt))
    return ceiling * rand()


async def get_with_retry(client: httpx.AsyncClient, url: str, **kwargs):
    """GET ``url``, retrying throttles, transient server errors and timeouts.

    Returns the final response. A response that is still *failing* after the
    last attempt is returned as-is rather than raised, so callers keep their
    existing ``raise_for_status`` behaviour and error handling.

    A transport failure has no response to return, so the last exception is
    re-raised instead. Status retries and transport retries share one attempt
    budget: a request alternating between the two would otherwise never stop.
    """
    last_response = None
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        reason: str
        try:
            last_response = await client.get(url, **kwargs)
        except httpx.TransportError as exc:
            # Covers timeouts, connect errors and reads that die mid-body.
            # Deliberately not `Exception`: anything else is a bug in this
            # code and retrying it just delays the traceback.
            last_error, last_response = exc, None
            reason = type(exc).__name__
        else:
            last_error = None
            if last_response.status_code not in RETRY_STATUSES:
                return last_response
            reason = str(last_response.status_code)

        if attempt == MAX_ATTEMPTS - 1:
            break
        delay = backoff_delay(attempt)
        logger.warning(
            "ESPN %s for %s; retrying in %.2fs (attempt %d/%d)",
            reason, url, delay, attempt + 1, MAX_ATTEMPTS,
        )
        await asyncio.sleep(delay)

    if last_error is not None:
        raise last_error
    return last_response
