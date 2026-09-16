"""Orchestrate the daily digest: select, render, send.

Never raises. A failed digest must not take down the scheduler that also
owns pick generation's sibling jobs.
"""
import logging
import os
from datetime import date as _date
from zoneinfo import ZoneInfo

from backend.database import get_session
from backend.digest.selector import select_digest
from backend.digest.render import render_digest
from backend.digest.sender import send_email

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


def send_daily_digest(config: dict, engine, target_date=None) -> dict:
    cfg = config.get("digest", {}) or {}
    result = {"sent": False, "sections": 0, "picks": 0}

    if target_date is None:
        from datetime import datetime
        target_date = datetime.now(tz=ET).date()

    session = get_session(engine)
    try:
        sections = select_digest(
            session,
            target_date,
            cfg.get("sports", ["ncaaf", "nfl", "nba", "mlb"]),
            config.get("seasons", {}),
            max_per_sport=cfg.get("max_per_sport", 5),
        )
        result["sections"] = len(sections)
        result["picks"] = sum(len(s.picks) for s in sections)

        subject, html, text = render_digest(sections, target_date)
        if not subject:
            logger.info("Digest for %s is empty; nothing sent", target_date)
            return result

        dry_run_path = None
        if not cfg.get("enabled", False) or os.environ.get("DIGEST_DRY_RUN") == "1":
            dry_run_path = os.environ.get("DIGEST_DRY_RUN_PATH", "digest_preview.html")

        result["sent"] = send_email(
            subject, html, text,
            cfg.get("from", "picks@example.com"),
            cfg.get("recipients", []),
            api_key=os.environ.get("RESEND_API_KEY"),
            dry_run_path=dry_run_path,
        )
        return result
    except Exception as e:
        logger.exception("Daily digest failed for %s", target_date)
        result["error"] = type(e).__name__
        return result
    finally:
        session.close()
