"""Deliver the digest. The only unit in backend/digest that touches the network.

Sends over Gmail SMTP, authenticating with the GMAIL_USER and
GMAIL_APP_PASSWORD already in the shared secrets file. The original
implementation spoke only to Resend's REST API, and no RESEND_API_KEY was
ever configured, so an enabled digest logged "not sent" every day.

Credentials are read at send time and never written to config, a repo-local
.env, or a log line. `_scrub` covers both the Resend key shape and the app
password, because the failure paths interpolate exception text and an SMTP
library is free to put whatever it likes in there.
"""
import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

import httpx

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"

_KEY_RE = re.compile(r"(re_[A-Za-z0-9_]+|key=[^&\s'\"]+)")


#: Gmail's submission endpoint. Implicit TLS, so the credential never
#: crosses the wire in the clear even for the first command.
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def _scrub(text: str, *secrets: str | None) -> str:
    """Remove any credential from text before it reaches a log record."""
    out = _KEY_RE.sub("<redacted>", text)
    for secret in secrets:
        if secret:
            out = out.replace(secret, "<redacted>")
    return out


def resolve_gmail_credentials(
        shared_env_path: str | os.PathLike | None = None
) -> tuple[str | None, str | None]:
    """The Gmail account and app password, environment first.

    Same order as :func:`backend.config.resolve_odds_api_key` and for the
    same reason: a service unit or container injects them as environment
    variables, and the shared secrets file is the fallback for a local run.
    Nothing is copied into a repo-local .env, because a stale duplicate of a
    rotated credential is how a dead key once kept feeding scheduled runs.

    Returns ``(None, None)`` when either half is missing -- a user without a
    password is not a usable account, and reporting it as configured would
    turn a missing secret into an authentication failure at send time.
    """
    from backend.config import DEFAULT_SHARED_ENV, _read_env_file

    def _clean(user_value, password_value):
        # Gmail displays an app password as four space-separated groups, and
        # that is how it gets pasted into a secrets file. The spaces are
        # presentation, not part of the credential.
        user_value = user_value.strip() if user_value else user_value
        password_value = ("".join(password_value.split())
                          if password_value else password_value)
        return user_value, password_value

    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if user and password:
        return _clean(user, password)

    path = (shared_env_path
            or os.environ.get("SHARED_ENV_PATH")
            or DEFAULT_SHARED_ENV)
    values = _read_env_file(path)
    user = user or values.get("GMAIL_USER")
    password = (password or values.get("GMAIL_APP_PASSWORD")
                or values.get("GMAIL_APP_PW"))
    if not user or not password:
        return None, None
    return _clean(user, password)


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

    user, password = resolve_gmail_credentials()
    if not user or not password:
        logger.warning(
            "No GMAIL_USER/GMAIL_APP_PASSWORD configured; digest not sent")
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

    message = EmailMessage()
    message["Subject"] = subject
    # The authenticated account, not the configured `sender`: Gmail rewrites
    # a From it does not own, so honouring a placeholder like
    # picks@example.com would either be silently replaced or rejected.
    message["From"] = user
    message["To"] = ", ".join(recipients)
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=timeout,
                              context=ssl.create_default_context()) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
    except Exception as e:
        # Never raises: a digest failure must not take the scheduler down.
        logger.warning("Digest send failed: %s: %s",
                       type(e).__name__, _scrub(str(e), password, api_key))
        return False

    logger.info("Digest sent to %d recipient(s)", len(recipients))
    return True
