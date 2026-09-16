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
    assert "(1 pick, 1 prop across 1 sport)" in subject


def test_props_only_subject_does_not_claim_zero():
    """A subject saying "0" over a body listing props erodes trust fast."""
    section = DigestSection(
        sport="nba", picks=[],
        props=[DigestPick(sport="nba", matchup="Lakers @ Celtics",
                          pick_value="Tatum Over 27.5 Points", odds=-115,
                          confidence=4, edge_pct=6.0, rationale="")],
    )
    subject, html, text = render_digest([section], date(2026, 9, 20))
    assert "(1 prop across 1 sport)" in subject
    assert "0 pick" not in subject
    assert "0 prop" not in subject
    assert "Tatum Over 27.5 Points" in html
    assert "Tatum Over 27.5 Points" in text


def test_subject_and_header_agree():
    """Both are built from one phrase, so they cannot drift apart."""
    subject, html, _ = render_digest([_section()], date(2026, 9, 20))
    phrase = subject.split("(", 1)[1].rstrip(")")
    assert phrase in html


def test_prop_row_names_the_matchup_and_confidence():
    _, html, text = render_digest([_section()], date(2026, 9, 20))
    prop_block = html.split("Player props", 1)[1]
    assert "Bills @ Chiefs" in prop_block, "a reader must know which game a prop is from"
    assert "★" in prop_block or "☆" in prop_block
    assert "Bills @ Chiefs" in text.split("Player props:", 1)[1]


def test_prop_row_omits_the_percent_edge_glyph():
    """Prop edge is (prob - 0.5) * 200 and price-blind; game edge is de-vigged.
    An identical "+X%" on both invites a comparison that is not meaningful."""
    section = DigestSection(
        sport="nba", picks=[],
        props=[DigestPick(sport="nba", matchup="Lakers @ Celtics",
                          pick_value="Tatum Over 27.5 Points", odds=-115,
                          confidence=4, edge_pct=37.5, rationale="")],
    )
    _, html, text = render_digest([section], date(2026, 9, 20))
    assert "37.5" not in html
    assert "37.5" not in text


def test_prop_matchup_is_escaped_in_html():
    section = DigestSection(
        sport="ncaab", picks=[],
        props=[DigestPick(sport="ncaab", matchup="Texas A&M @ LSU",
                          pick_value="Jones Over 12.5 Points", odds=-110,
                          confidence=3, edge_pct=0.0, rationale="")],
    )
    _, html, _ = render_digest([section], date(2026, 9, 20))
    assert "Texas A&amp;M" in html
    assert "Texas A&M" not in html


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
    # A bare "<" can legitimately appear inside a pick_value (e.g.
    # "Smith <1.5 Rebounds"), so assert the real intent: no markup leaked.
    assert "<div" not in text
    assert "<tr" not in text
    assert "<td" not in text
    assert "<html" not in text


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
