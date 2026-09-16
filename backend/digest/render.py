"""Render a digest selection into email bodies.

Table-based HTML with inline styles only — the one approach that renders
reliably across Gmail, Outlook and Apple Mail. No external CSS, no web
fonts, no JavaScript. A plain-text alternative is always produced.
"""
from datetime import date

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


def render_digest(sections: list[DigestSection], target_date: date):
    """Return (subject, html, text). All three are "" when there is nothing
    to send — the caller must not send an empty digest."""
    if not sections:
        return "", "", ""

    total = sum(len(s.picks) for s in sections)
    # NOTE: %-d is not portable (fails on Windows). Use %d and strip the
    # leading zero, which works on every platform.
    pretty = target_date.strftime("%a %b %d").replace(" 0", " ")
    subject = f"Top picks — {pretty} ({total} across {len(sections)} sports)"

    rows = []
    text_lines = [f"TOP PICKS — {pretty}", ""]

    for section in sections:
        rows.append(
            f'<tr><td style="{_FONT}padding:18px 0 6px 0;font-size:13px;'
            f'letter-spacing:.08em;text-transform:uppercase;color:#6b7280;">'
            f'{_label(section.sport)}</td></tr>'
        )
        text_lines.append(f"-- {_label(section.sport)} --")

        for p in section.picks:
            rationale_html = (
                f'<div style="{_FONT}font-size:13px;color:#6b7280;padding-top:3px;">'
                f'{p.rationale}</div>' if p.rationale else ""
            )
            rows.append(
                f'<tr><td style="padding:10px 0;border-bottom:1px solid #e5e7eb;">'
                f'<div style="{_FONT}font-size:15px;font-weight:600;color:#111827;">'
                f'{p.pick_value} <span style="font-weight:400;color:#6b7280;">({p.odds})</span></div>'
                f'<div style="{_FONT}font-size:13px;color:#374151;padding-top:2px;">'
                f'{p.matchup} &nbsp;·&nbsp; {_stars(p.confidence)} &nbsp;·&nbsp; +{p.edge_pct}%</div>'
                f'{rationale_html}</td></tr>'
            )
            text_lines.append(f"  {p.pick_value} ({p.odds}) — {p.matchup} — {_stars(p.confidence)} +{p.edge_pct}%")
            if p.rationale:
                text_lines.append(f"      {p.rationale}")

        if section.props:
            rows.append(
                f'<tr><td style="{_FONT}padding:12px 0 4px 0;font-size:12px;'
                f'color:#6b7280;">Player props</td></tr>'
            )
            text_lines.append("  Player props:")
            for p in section.props:
                rows.append(
                    f'<tr><td style="padding:6px 0;border-bottom:1px solid #f3f4f6;">'
                    f'<div style="{_FONT}font-size:14px;color:#111827;">'
                    f'{p.pick_value} <span style="color:#6b7280;">({p.odds})</span></div></td></tr>'
                )
                text_lines.append(f"    {p.pick_value} ({p.odds})")
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
        f'{total} picks across {len(sections)} sports</td></tr>'
        + "".join(rows)
        + f'<tr><td style="{_FONT}font-size:11px;color:#9ca3af;padding-top:18px;">'
        f'Model output for research, not betting advice.</td></tr>'
        f'</table></td></tr></table></body></html>'
    )

    text_lines.append("Model output for research, not betting advice.")
    return subject, html, "\n".join(text_lines)
