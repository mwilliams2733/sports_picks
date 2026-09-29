"""Player PINs: stored hashed, required to bet, locked after repeated misses.

A 4-digit PIN has 10,000 values, so without the lockout it is guessable in
minutes through a public link. The lock counter lives in memory: a restart
clears it, which is acceptable for a handful of friends.
"""
import hashlib
import hmac
import os
import re
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
    """Counts wrong PINs per player; locks after MAX_FAILURES for LOCK_FOR."""

    def __init__(self, now: Callable[[], datetime] | None = None):
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._failures: dict[int, int] = {}
        self._locked_until: dict[int, datetime] = {}

    def reset(self) -> None:
        self._failures.clear()
        self._locked_until.clear()
        self.now = lambda: datetime.now(timezone.utc)

    def locked(self, user_id: int) -> bool:
        until = self._locked_until.get(user_id)
        if until is None:
            return False
        if self.now() >= until:
            del self._locked_until[user_id]
            self._failures.pop(user_id, None)
            return False
        return True

    def fail(self, user_id: int) -> None:
        count = self._failures.get(user_id, 0) + 1
        self._failures[user_id] = count
        if count >= MAX_FAILURES:
            self._locked_until[user_id] = self.now() + LOCK_FOR

    def succeed(self, user_id: int) -> None:
        self._failures.pop(user_id, None)

    def clear(self, user_id: int) -> None:
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
        if guard.locked(user_id):
            raise HTTPException(status_code=429,
                                detail="Too many wrong PINs; try again in 15 minutes")
        if not x_player_pin or not PIN_PATTERN.match(x_player_pin):
            raise HTTPException(status_code=401, detail="PIN required (4-6 digits)")
        if user.pin_hash is None:
            user.pin_hash, user.pin_salt = hash_pin(x_player_pin)
            session.commit()
            return
        if not pin_matches(x_player_pin, user.pin_hash, user.pin_salt):
            guard.fail(user_id)
            raise HTTPException(status_code=401, detail="Wrong PIN")
        guard.succeed(user_id)
    finally:
        session.close()
