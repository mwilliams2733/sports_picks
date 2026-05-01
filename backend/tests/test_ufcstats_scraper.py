"""Tests for UFCStats scraper. HTML is loaded from a checked-in fixture."""
from pathlib import Path

from backend.collectors.ufcstats_scraper import parse_event_fights


FIXTURE = Path(__file__).parent / "fixtures" / "ufcstats_event.html"


def test_parse_event_fights_returns_two_rows():
    html = FIXTURE.read_text(encoding="utf-8")
    fights = parse_event_fights(html)
    assert len(fights) == 2


def test_parse_event_fights_extracts_fighter_names():
    html = FIXTURE.read_text(encoding="utf-8")
    fights = parse_event_fights(html)
    assert fights[0]["fighter_a_name"] == "Alex Pereira"
    assert fights[0]["fighter_b_name"] == "Jamahal Hill"
    assert fights[1]["fighter_a_name"] == "Charles Oliveira"
    assert fights[1]["fighter_b_name"] == "Arman Tsarukyan"


def test_parse_event_fights_extracts_winner():
    """First fight: fighter_a wins (Pereira). Second fight: fighter_b wins (Tsarukyan)."""
    html = FIXTURE.read_text(encoding="utf-8")
    fights = parse_event_fights(html)
    assert fights[0]["winner"] == "fighter_a"
    assert fights[1]["winner"] == "fighter_b"


def test_parse_event_fights_extracts_method():
    html = FIXTURE.read_text(encoding="utf-8")
    fights = parse_event_fights(html)
    assert fights[0]["method"] == "KO/TKO"
    assert "Decision" in fights[1]["method"]


def test_parse_event_fights_handles_empty_html():
    """A page without the fight table (e.g., upcoming-event page with no results yet)
    should return an empty list, not crash."""
    fights = parse_event_fights("<html><body><p>No fights yet</p></body></html>")
    assert fights == []
