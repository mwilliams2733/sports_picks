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
from backend.digest.render import render_digest, render_empty_day_digest, empty_day_reason
from backend.digest.sender import send_email
from backend.digest.record import record_emailed

logger = logging.getLogger(__name__)

from backend.time_utils import ET  # noqa: F401  (re-exported)


def send_daily_digest(config: dict, engine, target_date=None) -> dict:
    cfg = config.get("digest", {}) or {}
    result = {"sent": False, "sections": 0, "picks": 0}

    if target_date is None:
        from datetime import datetime
        target_date = datetime.now(tz=ET).date()

    session = None
    try:
        session = get_session(engine)
        send_bar = cfg.get("send_bar", {}) or {}
        sections = select_digest(
            session,
            target_date,
            cfg.get("sports", ["ncaaf", "nfl", "nba", "mlb"]),
            config.get("seasons", {}),
            send_bar=send_bar,
            min_trailing_win_pct=cfg.get("min_trailing_win_pct"),
            min_trailing_picks=cfg.get("min_trailing_picks", 20),
        )

        # `select_digest` returns one section per in-season sport, even one
        # with zero games or zero qualifying picks -- diagnostics live there
        # so the empty-day email can explain itself. Only sections with real
        # content go to the normal renderer / get recorded as emailed.
        content_sections = [s for s in sections if s.picks or s.props]
        result["sections"] = len(content_sections)
        result["picks"] = sum(len(s.picks) for s in content_sections)

        empty_day = False
        if content_sections:
            subject, html, text = render_digest(content_sections, target_date)
        else:
            # Nothing to show. Tell apart "no games at all" (unchanged from
            # before: send nothing) from "games happened but nothing cleared
            # the send bar" (the new empty-day email) using the diagnostics
            # select_digest attaches to each section. A section without
            # diagnostics (e.g. a caller/test that stubs select_digest
            # directly) can't be classified this way and is treated as "no
            # games" -- the same as before this feature existed.
            total_games = sum((s.diagnostics.games if s.diagnostics else 0) for s in sections)
            if total_games == 0:
                logger.info("Digest for %s is empty; nothing sent", target_date)
                return result

            empty_day = True
            min_odds = send_bar["min_odds"]
            max_odds = send_bar["max_odds"]
            for section in sections:
                logger.info("Digest for %s: %s", target_date,
                           empty_day_reason(section, min_odds, max_odds))

            # A distinct marker, logged ONLY when every in-season sport that
            # had games today generated zero picks -- the 2026-09-28 class
            # of fault (the scout ran, fetched games, but produced nothing).
            # `check_digest.py` treats this the same as the old "digest was
            # empty" case, even though the empty-day email below DOES send
            # (and therefore logs "Digest sent to N recipient(s)", which on
            # its own reads as healthy). Without this marker, a scout that
            # silently produced nothing would report OK.
            sports_with_games = [s for s in sections
                                 if s.diagnostics and s.diagnostics.games > 0]
            if sports_with_games and all(s.diagnostics.generated == 0
                                        for s in sports_with_games):
                logger.info("Digest for %s: no picks were generated for any sport",
                           target_date)

            subject, html, text = render_empty_day_digest(sections, target_date,
                                                           min_odds, max_odds)

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
        # Only a real send is a record: a dry run reached nobody. Recorded
        # after the send, and a failure here is logged, never raised -- the
        # email has already gone and must not be reported as failed. An
        # empty-day send has no picks to record.
        if result["sent"] and dry_run_path is None and not empty_day:
            try:
                result["recorded"] = record_emailed(session, content_sections, target_date)
            except Exception:
                logger.exception("Digest for %s sent but not recorded", target_date)
                session.rollback()
        return result
    except Exception as e:
        logger.exception("Daily digest failed for %s", target_date)
        result["error"] = type(e).__name__
        return result
    finally:
        if session is not None:
            session.close()
