"""Render a digest selection into email bodies.

Table-based HTML with inline styles only — the one approach that renders
reliably across Gmail, Outlook and Apple Mail. No external CSS, no web
fonts, no JavaScript. A plain-text alternative is always produced.
"""
from datetime import date
from html import escape as _escape

from backend.analysis.prop_markets import market_label
from backend.digest.selector import DigestPick, DigestSection

# Product name, prefixed to every digest subject so recipients can filter on it.
BRAND = "Metric Edge"

SPORT_LABELS = {
    "nfl": "NFL", "ncaaf": "College Football", "nba": "NBA",
    "mlb": "MLB", "ncaab": "College Basketball",
    "boxing": "Boxing", "mma": "MMA",
}

_FONT = "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"


def _label(sport: str) -> str:
    return SPORT_LABELS.get(sport, sport.upper())


def _pct(p: float | None) -> str:
    return "—" if p is None else f"{round(p * 100)}%"


def _fmt_edge(edge_pct: float) -> str:
    """A game pick's raw edge, signed, one decimal: "+6.2" or "-3.0".

    Shown only for game picks -- see the note on prop edge in the props
    loop below for why a prop's edge_pct must never be rendered this way.
    """
    return f"+{edge_pct:.1f}" if edge_pct >= 0 else f"{edge_pct:.1f}"


def _fmt_odds(odds: int) -> str:
    """American odds with an explicit sign on a positive price.

    "(145)" reads as a magnitude, not a price -- a reader has to already
    know American odds convention to tell it apart from "(-145)" at a
    glance. "(+145)" needs no such context.
    """
    return f"+{odds}" if odds > 0 else str(odds)


_HEAD = '<head><meta charset="utf-8"></head>'


#: Words for what a totals line counts, by sport. Absent -> no unit word,
#: which is correct for a sport nobody has checked rather than a guess.
_TOTAL_UNITS = {
    "mlb": "runs", "nfl": "points", "ncaaf": "points",
    "nba": "points", "ncaab": "points",
}


def _selection_label(p: DigestPick) -> str:
    """What the pick is, in words a reader does not have to decode.

    `pick_value` is stored as the SIDE of the game -- "HOME ML", "AWAY +1.5"
    -- which is the right thing to store and the wrong thing to email: the
    reader has to work out which team is home. The team names travel on the
    DigestPick for exactly this.

    Anything unrecognised falls through to the stored value unchanged. A
    label this function cannot parse is still information; swallowing it
    would turn a readable oddity into a blank line.
    """
    value = p.pick_value
    side, _, rest = value.partition(" ")
    team = {"HOME": p.home_team, "AWAY": p.away_team}.get(side)

    if team:
        if rest == "ML":
            return f"{team} to win"
        if rest:
            return f"{team} {rest}"       # spread: "Pirates -1.5"
        return team

    if side in ("Over", "Under") and rest:
        unit = _TOTAL_UNITS.get(p.sport)
        return f"{side} {rest} {unit}" if unit else value

    return value


def _prop_label(pick_value: str) -> str:
    """A prop's stored value with a trailing raw market key made readable.

    Picks stored before the label table was complete end in the API key --
    "Zach Ertz Over 10.5 player_reception_yds" on 2026-09-28. Only a key
    `market_label` knows is swapped; anything else passes through.
    """
    head, _, last = pick_value.rpartition(" ")
    label = market_label(last)
    return f"{head} {label}" if label != last else pick_value


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
    subject = f"{BRAND} — Top picks — {pretty} ({count_phrase})"

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
                f'{_escape(_selection_label(p))} <span style="font-weight:400;color:#6b7280;">({_fmt_odds(p.odds)})</span></div>'
                f'<div style="{_FONT}font-size:13px;color:#374151;padding-top:2px;">'
                f'{_escape(p.matchup)} &nbsp;·&nbsp; '
                f'Model {_pct(p.model_prob)} &nbsp;·&nbsp; Price {_pct(p.price_prob)} '
                f'&nbsp;·&nbsp; Edge {_fmt_edge(p.edge_pct)} pts</div>'
                f'{rationale_html}</td></tr>'
            )
            text_lines.append(
                f"  {_selection_label(p)} ({_fmt_odds(p.odds)}) — {p.matchup} — "
                f"Model {_pct(p.model_prob)} / Price {_pct(p.price_prob)} / "
                f"Edge {_fmt_edge(p.edge_pct)} pts"
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
                    f'{_escape(_prop_label(p.pick_value))} <span style="color:#6b7280;">({_fmt_odds(p.odds)})</span></div>'
                    f'<div style="{_FONT}font-size:12px;color:#6b7280;padding-top:2px;">'
                    f'{_escape(p.matchup)}</div></td></tr>'
                )
                text_lines.append(
                    f"    {_prop_label(p.pick_value)} ({_fmt_odds(p.odds)}) — {p.matchup}"
                )
        text_lines.append("")

    html = (
        f'<html>{_HEAD}<body style="margin:0;padding:0;background:#f9fafb;">'
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
        f'<tr><td style="{_FONT}font-size:11px;color:#9ca3af;padding-top:6px;">'
        f'{_EDGE_FOOTER_TEXT}</td></tr>'
        f'</table></td></tr></table></body></html>'
    )

    text_lines.append(
        'Model output for research, not betting advice. "Model" is the '
        'model\'s win probability; "Price" is what the quoted price implies.'
    )
    text_lines.append(_EDGE_FOOTER_TEXT)
    return subject, html, "\n".join(text_lines)


_FOOTER_TEXT = ('Model output for research, not betting advice. "Model" is the '
               'model\'s win probability; "Price" is what the quoted price implies.')

#: Added 2026-09-30 when the 3-point shrunk-edge send bar was lifted (owner
#: decision -- see docs/review-remediation.md): every game pick's raw edge
#: is now shown, and this line is the reader's only guardrail against
#: mistaking "shown" for "validated".
_EDGE_FOOTER_TEXT = (
    "Edge = model win % minus the market's no-vig win %, in points. The "
    "model has not yet beaten the market; edges are shown for your "
    "judgement, not as a signal."
)

#: The empty-day email shows no "Model"/"Price" figures at all -- unlike
#: the normal digest's footer, defining those terms here would refer to
#: nothing on the page.
_EMPTY_DAY_FOOTER_TEXT = 'Model output for research, not betting advice.'


def empty_day_reason(section: DigestSection, min_odds: int, max_odds: int) -> str:
    """One line explaining why this sport contributed nothing today.

    Shared between the empty-day email body and the job's INFO log, so the
    two can never say something different about the same sport.

    Since the 3-point shrunk-edge gate was removed (owner decision
    2026-09-30, see docs/review-remediation.md), every priced game pick is
    shown -- there is no longer a way for picks to survive the price window
    and still be empty. Two reasons, not three: "no picks generated today"
    and "picks generated, none priced in the window".
    """
    d = section.diagnostics
    label = _label(section.sport)
    if d is None or d.generated == 0:
        return f"{label}: no picks generated today."
    return (f"{label}: {d.generated} pick{'' if d.generated == 1 else 's'} generated, "
           f"none priced between {_fmt_odds(min_odds)} and {_fmt_odds(max_odds)}.")


def render_empty_day_digest(sections: list[DigestSection], target_date: date,
                            min_odds: int, max_odds: int):
    """Return (subject, html, text) for a day where nothing was priced or
    generated, for any in-season sport.

    ``sections`` is the full diagnostics list from ``select_digest`` -- every
    in-season sport, including ones with zero games that day. The caller is
    responsible for only calling this when at least one in-season sport had
    at least one game (see backend/digest/job.py): with no games at all,
    the existing "nothing to send" behavior applies instead.
    """
    pretty = target_date.strftime("%a %b %d").replace(" 0", " ")
    subject = f"{BRAND} — No qualifying picks — {pretty}"

    reasons = [empty_day_reason(s, min_odds, max_odds) for s in sections]

    text_lines = [f"NO QUALIFYING PICKS — {pretty}", ""] + reasons + ["", _EMPTY_DAY_FOOTER_TEXT]
    text = "\n".join(text_lines)

    rows = "".join(
        f'<tr><td style="{_FONT}font-size:14px;color:#111827;padding:4px 0;">'
        f'{_escape(r)}</td></tr>'
        for r in reasons
    )
    html = (
        f'<html>{_HEAD}<body style="margin:0;padding:0;background:#f9fafb;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:#f9fafb;padding:16px;"><tr><td align="center">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="max-width:560px;background:#ffffff;border-radius:10px;padding:20px;">'
        f'<tr><td style="{_FONT}font-size:18px;font-weight:700;color:#111827;'
        f'padding-bottom:10px;">No qualifying picks — {pretty}</td></tr>'
        + rows
        + f'<tr><td style="{_FONT}font-size:11px;color:#9ca3af;padding-top:18px;">'
        f'{_EMPTY_DAY_FOOTER_TEXT}</td></tr>'
        f'</table></td></tr></table></body></html>'
    )
    return subject, html, text
