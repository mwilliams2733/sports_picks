"""Player PINs: stored hashed, required to bet, locked after repeated misses.

A 4-digit PIN has 10,000 values, so without the lockout it is guessable in
minutes through a public link. The lock counter lives in memory: a restart
clears it, which is acceptable for a handful of friends.
"""
import hashlib
import hmac
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Header, HTTPException, Request

from backend.database import get_session
from backend.models import UserProfile

PIN_PATTERN = re.compile(r"^\d{4,6}$")
PIN_HEADER = "X-Player-Pin"
ITERATIONS = 200_000
MAX_FAILURES = 5
LOCK_FOR = timedelta(minutes=15)


def hash_pin(pin: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, ITERATIONS)
    return digest.hex(), salt.hex()


def pin_matches(pin: str, pin_hash: str, pin_salt: str) -> bool:
    candidate, _ = hash_pin(pin, bytes.fromhex(pin_salt))
    return hmac.compare_digest(candidate, pin_hash)


class PinGuard:
    """Counts wrong PINs per player; locks after MAX_FAILURES for LOCK_FOR.

    `reserve_attempt` and `release` are the only methods `require_player_pin`
    calls around a PIN check, and both take `_lock` for their whole body.
    That is deliberate: the lockout must be enforced BEFORE the slow PBKDF2
    compare runs, in the same critical section as the "am I already locked"
    read, or concurrent requests can each observe "not locked yet" and all
    slip through -- a burst of 30 simultaneous wrong guesses against a
    file-backed app once got 19 of them evaluated instead of at most
    MAX_FAILURES. A plain `threading.Lock` (not re-entrant) is enough: no
    method here ever calls another while holding it.
    """

    def __init__(self, now: Callable[[], datetime] | None = None):
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._failures: dict[int, int] = {}
        self._locked_until: dict[int, datetime] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()
        self.now = lambda: datetime.now(timezone.utc)

    def _locked_locked(self, user_id: int) -> bool:
        """`locked()`'s body, callable only while `_lock` is already held."""
        until = self._locked_until.get(user_id)
        if until is None:
            return False
        if self.now() >= until:
            del self._locked_until[user_id]
            self._failures.pop(user_id, None)
            return False
        return True

    def locked(self, user_id: int) -> bool:
        with self._lock:
            return self._locked_locked(user_id)

    def reserve_attempt(self, user_id: int) -> bool:
        """Call this BEFORE hashing the candidate PIN, never after.

        In one critical section: if the player is already locked, returns
        True and reserves nothing -- the caller must refuse without ever
        touching the hash, so a flood of guesses made while locked can't
        each sneak a "not locked yet" read in ahead of the write that would
        have locked them. Otherwise counts this attempt as a provisional
        failure (locking at MAX_FAILURES) and returns False -- the caller
        may now hash and compare. If the PIN turns out right, call
        `release`; if wrong, do nothing further -- the reservation already
        counted it.
        """
        with self._lock:
            if self._locked_locked(user_id):
                return True
            count = self._failures.get(user_id, 0) + 1
            self._failures[user_id] = count
            if count >= MAX_FAILURES:
                self._locked_until[user_id] = self.now() + LOCK_FOR
            return False

    def release(self, user_id: int) -> None:
        """The reserved attempt turned out to be the correct PIN: undo it
        (and any lock that same reservation just set) and clear the count."""
        with self._lock:
            self._failures.pop(user_id, None)
            self._locked_until.pop(user_id, None)

    def clear(self, user_id: int) -> None:
        with self._lock:
            self._failures.pop(user_id, None)
            self._locked_until.pop(user_id, None)


guard = PinGuard()


def require_player_pin(request: Request, user_id: int,
                       x_player_pin: str | None = Header(default=None, alias=PIN_HEADER)) -> None:
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        if not x_player_pin or not PIN_PATTERN.match(x_player_pin):
            raise HTTPException(status_code=401, detail="PIN required (4-6 digits)")
        if user.pin_hash is None:
            user.pin_hash, user.pin_salt = hash_pin(x_player_pin)
            session.commit()
            return
        # Reserve the attempt BEFORE hashing -- this is the atomic
        # check-and-count that closes the concurrent-guess race. A locked
        # player is refused here, before pin_matches ever runs, so a wrong
        # and a right PIN get an identical 429 with no way to tell them
        # apart from the response.
        if guard.reserve_attempt(user_id):
            raise HTTPException(status_code=429,
                                detail="Too many wrong PINs; try again in 15 minutes")
        if not pin_matches(x_player_pin, user.pin_hash, user.pin_salt):
            raise HTTPException(status_code=401, detail="Wrong PIN")
        guard.release(user_id)
    finally:
        session.close()
