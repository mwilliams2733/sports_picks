from datetime import date

from backend.digest.selector import DigestPick, DigestSection
from backend.digest.render import render_digest


def _section():
    return DigestSection(
        sport="nfl",
        picks=[DigestPick(sport="nfl", matchup="Bills @ Chiefs", pick_value="HOME ML",
                          odds=-110, confidence=5, edge_pct=8.1,
                          rationale="Rating gap strongly favors Chiefs.")],
        props=[DigestPick(sport="nfl", matchup="Bills @ Chiefs",
                          pick_value="Mahomes Over 275.5 (player_pass_yds)",
                          odds=-115, confidence=0, edge_pct=0.0, rationale="")],
    )


def test_subject_names_date_and_count():
    subject, _, _ = render_digest([_section()], date(2026, 9, 20))
    assert "Sep 20" in subject
    assert "1" in subject


def test_html_contains_pick_and_rationale():
    _, html, _ = render_digest([_section()], date(2026, 9, 20))
    assert "HOME ML" in html
    assert "Bills @ Chiefs" in html
    assert "Rating gap strongly favors Chiefs." in html
    assert "-110" in html


def test_html_has_no_external_resources():
    _, html, _ = render_digest([_section()], date(2026, 9, 20))
    assert "<script" not in html.lower()
    assert "<link" not in html.lower()


def test_props_render_in_their_own_section():
    _, html, _ = render_digest([_section()], date(2026, 9, 20))
    assert "Player props" in html
    assert "Mahomes Over 275.5" in html


def test_text_alternative_is_always_produced():
    _, _, text = render_digest([_section()], date(2026, 9, 20))
    assert "HOME ML" in text
    assert "<" not in text


def test_empty_sections_render_empty_subject_marker():
    subject, html, text = render_digest([], date(2026, 9, 20))
    assert subject == ""
    assert html == ""
    assert text == ""


def test_html_escapes_ampersand_in_matchup():
    section = DigestSection(
        sport="ncaaf",
        picks=[DigestPick(sport="ncaaf", matchup="Texas A&M @ LSU", pick_value="HOME ML",
                          odds=-120, confidence=4, edge_pct=5.0, rationale="")],
        props=[],
    )
    _, html, _ = render_digest([section], date(2026, 9, 20))
    assert "Texas A&amp;M" in html
    assert "Texas A&M" not in html


def test_text_leaves_ampersand_in_matchup_unescaped():
    section = DigestSection(
        sport="ncaaf",
        picks=[DigestPick(sport="ncaaf", matchup="Texas A&M @ LSU", pick_value="HOME ML",
                          odds=-120, confidence=4, edge_pct=5.0, rationale="")],
        props=[],
    )
    _, _, text = render_digest([section], date(2026, 9, 20))
    assert "Texas A&M" in text
    assert "A&amp;M" not in text


def test_html_escapes_angle_bracket_in_pick_value():
    section = DigestSection(
        sport="nba",
        picks=[],
        props=[DigestPick(sport="nba", matchup="Lakers @ Celtics",
                          pick_value="Smith <1.5 Rebounds",
                          odds=-115, confidence=0, edge_pct=0.0, rationale="")],
    )
    _, html, _ = render_digest([section], date(2026, 9, 20))
    assert "&lt;1.5 Rebounds" in html
