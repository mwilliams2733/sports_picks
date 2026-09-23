"""Render a digest selection into email bodies.

Table-based HTML with inline styles only — the one approach that renders
reliably across Gmail, Outlook and Apple Mail. No external CSS, no web
fonts, no JavaScript. A plain-text alternative is always produced.
"""
from datetime import date
from html import escape as _escape

from backend.digest.selector import DigestSection

SPORT_LABELS = {
    "nfl": "NFL", "ncaaf": "College Football", "nba": "NBA",
    "mlb": "MLB", "ncaab": "College Basketball",
    "boxing": "Boxing", "mma": "MMA",
}

_FONT = "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"


def _stars(confidence: int) -> str:
    return "★" * confidence + "☆" * (5 - confidence)


def _label(sport: str) -> str:
    return SPORT_LABELS.get(sport, sport.upper())


def _pct(p: float | None) -> str:
    return "—" if p is None else f"{round(p * 100)}%"


def _record(section: DigestSection) -> str:
    if section.record is None:
        return ""
    w, l = section.record
    return f"last 30 days {w}-{l}"


def _count_phrase(n_picks: int, n_props: int, n_sports: int) -> str:
    """Describe what the body actually contains.

    Props live in their own per-sport block and were previously left out of
    the count entirely, so a props-only sport produced "0 across 1 sports"
    over a body full of props. Picks and props are counted separately rather
    than summed, because they are different things to a reader — but neither
    may be silently dropped.
    """
    parts = []
    if n_picks:
        parts.append(f"{n_picks} pick{'' if n_picks == 1 else 's'}")
    if n_props:
        parts.append(f"{n_props} prop{'' if n_props == 1 else 's'}")
    if not parts:
        parts.append("0 picks")
    return f"{', '.join(parts)} across {n_sports} sport{'' if n_sports == 1 else 's'}"


def render_digest(sections: list[DigestSection], target_date: date):
    """Return (subject, html, text). All three are "" when there is nothing
    to send — the caller must not send an empty digest."""
    if not sections:
        return "", "", ""

    total_picks = sum(len(s.picks) for s in sections)
    total_props = sum(len(s.props) for s in sections)
    # NOTE: %-d is not portable (fails on Windows). Use %d and strip the
    # leading zero, which works on every platform.
    pretty = target_date.strftime("%a %b %d").replace(" 0", " ")
    # One phrase, used for both the subject and the header, so the two can
    # never disagree with each other or with the body.
    count_phrase = _count_phrase(total_picks, total_props, len(sections))
    subject = f"Top picks — {pretty} ({count_phrase})"

    rows = []
    text_lines = [f"TOP PICKS — {pretty}", ""]

    for section in sections:
        record_text = _record(section)
        record_html = f" &nbsp;·&nbsp; {record_text}" if record_text else ""
        rows.append(
            f'<tr><td style="{_FONT}padding:18px 0 6px 0;font-size:13px;'
            f'letter-spacing:.08em;text-transform:uppercase;color:#6b7280;">'
            f'{_label(section.sport)}{record_html}</td></tr>'
        )
        text_lines.append(
            f"-- {_label(section.sport)} --" + (f" {record_text}" if record_text else "")
        )

        for p in section.picks:
            rationale_html = (
                f'<div style="{_FONT}font-size:13px;color:#6b7280;padding-top:3px;">'
                f'{_escape(p.rationale)}</div>' if p.rationale else ""
            )
            rows.append(
                f'<tr><td style="padding:10px 0;border-bottom:1px solid #e5e7eb;">'
                f'<div style="{_FONT}font-size:15px;font-weight:600;color:#111827;">'
                f'{_escape(p.pick_value)} <span style="font-weight:400;color:#6b7280;">({p.odds})</span></div>'
                f'<div style="{_FONT}font-size:13px;color:#374151;padding-top:2px;">'
                f'{_escape(p.matchup)} &nbsp;·&nbsp; {_stars(p.confidence)} &nbsp;·&nbsp; '
                f'Model {_pct(p.model_prob)} &nbsp;·&nbsp; Price {_pct(p.price_prob)}</div>'
                f'{rationale_html}</td></tr>'
            )
            text_lines.append(
                f"  {p.pick_value} ({p.odds}) — {p.matchup} — {_stars(p.confidence)} "
                f"Model {_pct(p.model_prob)} / Price {_pct(p.price_prob)}"
            )
            if p.rationale:
                text_lines.append(f"      {p.rationale}")

        if section.props:
            rows.append(
                f'<tr><td style="{_FONT}padding:12px 0 4px 0;font-size:12px;'
                f'color:#6b7280;">Player props</td></tr>'
            )
            text_lines.append("  Player props:")
            for p in section.props:
                # No claimed edge here on purpose: a prop's nominal edge is
                # (prob - 0.5) * 200 and ignores the prop's price, while a
                # game pick's model/price probabilities are measured against
                # an actual quoted price. Rendering both the same way invites
                # exactly the cross-scale comparison the selector's
                # two-query separation prevents.
                rows.append(
                    f'<tr><td style="padding:6px 0;border-bottom:1px solid #f3f4f6;">'
                    f'<div style="{_FONT}font-size:14px;color:#111827;">'
                    f'{_escape(p.pick_value)} <span style="color:#6b7280;">({p.odds})</span></div>'
                    f'<div style="{_FONT}font-size:12px;color:#6b7280;padding-top:2px;">'
                    f'{_escape(p.matchup)} &nbsp;·&nbsp; {_stars(p.confidence)}</div></td></tr>'
                )
                text_lines.append(
                    f"    {p.pick_value} ({p.odds}) — {p.matchup} — {_stars(p.confidence)}"
                )
        text_lines.append("")

    html = (
        f'<html><body style="margin:0;padding:0;background:#f9fafb;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:#f9fafb;padding:16px;"><tr><td align="center">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="max-width:560px;background:#ffffff;border-radius:10px;padding:20px;">'
        f'<tr><td style="{_FONT}font-size:18px;font-weight:700;color:#111827;'
        f'padding-bottom:2px;">Top picks — {pretty}</td></tr>'
        f'<tr><td style="{_FONT}font-size:13px;color:#6b7280;padding-bottom:6px;">'
        f'{count_phrase}</td></tr>'
        + "".join(rows)
        + f'<tr><td style="{_FONT}font-size:11px;color:#9ca3af;padding-top:18px;">'
        f'Model output for research, not betting advice. "Model" is the '
        f'model\'s win probability; "Price" is what the quoted price implies.'
        f'</td></tr>'
        f'</table></td></tr></table></body></html>'
    )

    text_lines.append(
        'Model output for research, not betting advice. "Model" is the '
        'model\'s win probability; "Price" is what the quoted price implies.'
    )
    return subject, html, "\n".join(text_lines)
