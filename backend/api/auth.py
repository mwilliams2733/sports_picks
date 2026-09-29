"""Owner-only routes: grading, deleting players, the pipeline, strategies.

Once the app is reachable through a public link, anything not guarded here
or by a player's PIN is open to everyone who has the link. There is no
loopback exemption: cloudflared connects from localhost, so a friend's
request and the owner's are indistinguishable by address.
"""
import hmac

from fastapi import Header, HTTPException

from backend.config import resolve_owner_key

OWNER_HEADER = "X-Owner-Key"


def require_owner(x_owner_key: str | None = Header(default=None, alias=OWNER_HEADER)) -> None:
    expected = resolve_owner_key()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="No owner key is configured on this server; owner actions are disabled")
    if not x_owner_key or not hmac.compare_digest(
            x_owner_key.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Owner key required")
