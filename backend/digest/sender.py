"""Deliver the digest. The only unit in backend/digest that touches the network.

Uses httpx (already a project dependency) against Resend's REST API rather
than adding a provider SDK.
"""
import logging
import re

import httpx

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"

_KEY_RE = re.compile(r"(re_[A-Za-z0-9_]+|key=[^&\s'\"]+)")


def _scrub(text: str, api_key: str | None) -> str:
    """Remove any credential from text before it reaches a log record."""
    out = _KEY_RE.sub("<redacted>", text)
    if api_key:
        out = out.replace(api_key, "<redacted>")
    return out


def send_email(subject, html, text, sender, recipients,
               api_key=None, dry_run_path=None, timeout=15.0) -> bool:
    """Send one email. Returns True only if the provider accepted it.

    Never raises: a digest failure must not propagate into the scheduler.
    """
    if dry_run_path:
        try:
            with open(dry_run_path, "w", encoding="utf-8") as fh:
                fh.write(html)
            logger.info("Digest dry run written to %s (nothing sent)", dry_run_path)
        except OSError as e:
            logger.warning("Digest dry run write failed: %s: %s",
                           type(e).__name__, _scrub(str(e), api_key))
        return False

    if not api_key:
        logger.warning("No RESEND_API_KEY configured; digest not sent")
        return False
    if not recipients:
        logger.warning("No digest recipients configured; nothing sent")
        return False
    if not isinstance(recipients, (list, tuple)):
        logger.warning(
            "digest.recipients must be a list, got %s; nothing sent",
            type(recipients).__name__,
        )
        return False

    try:
        response = httpx.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"from": sender, "to": list(recipients),
                  "subject": subject, "html": html, "text": text},
            timeout=timeout,
        )
        if response.status_code >= 400:
            logger.warning("Digest send failed: HTTP %s: %s",
                           response.status_code, _scrub(response.text, api_key))
            return False
        logger.info("Digest sent to %d recipient(s)", len(recipients))
        return True
    except Exception as e:
        logger.warning("Digest send failed: %s: %s",
                       type(e).__name__, _scrub(str(e), api_key))
        return False
