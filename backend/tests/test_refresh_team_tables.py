"""Guards for the team-snapshot refresh.

The snapshot decides which schools can be identified at all, so the dangerous
outcomes are a silently truncated list (a real program simply missing) and a
silently resolved abbreviation collision (one school mapped onto another's
ESPN id).
"""
import pytest

from backend.scripts.refresh_team_tables import (
    RESOLVED_COLLISIONS,
    _collapse_espn_duplicates,
    apply_resolved_collisions,
    colliding_abbreviations,
    main,
)


def _row(espn_id, abbr, name, location=""):
    return {"espn_id": espn_id, "abbreviation": abbr, "display_name": name,
            "location": location}


def test_records_identical_but_for_their_id_collapse_to_the_lowest():
    """ESPN lists Roosevelt Lakers as both 599 and 127991."""
    rows = _collapse_espn_duplicates([
        _row("127991", "RSVT", "Roosevelt Lakers", "Roosevelt"),
        _row("599", "RSVT", "Roosevelt Lakers", "Roosevelt"),
    ])
    assert [r["espn_id"] for r in rows] == ["599"]


def test_two_different_schools_are_not_collapsed():
    """Same abbreviation, different names -- a real collision, not a dupe."""
    rows = _collapse_espn_duplicates([
        _row("194", "OSU", "Ohio State Buckeyes", "Ohio State"),
        _row("3161", "OSU", "Ohio State Newark Titans", "Ohio State Newark"),
    ])
    assert len(rows) == 2


def test_a_reviewed_collision_keeps_only_the_named_winner():
    rows = apply_resolved_collisions("ncaaf", [
        _row("194", "OSU", "Ohio State Buckeyes"),
        _row("3161", "OSU", "Ohio State Newark Titans"),
        _row("333", "ALA", "Alabama Crimson Tide"),
    ])
    assert sorted(r["espn_id"] for r in rows) == ["194", "333"]


def test_a_reviewed_collision_for_another_sport_does_not_apply():
    """The allowlist is keyed by sport; OSU in ncaab is a separate question."""
    rows = apply_resolved_collisions("ncaab", [
        _row("194", "OSU", "Ohio State Buckeyes"),
        _row("3161", "OSU", "Ohio State Newark Titans"),
    ])
    assert len(rows) == 2


def test_collisions_are_reported_with_the_names_involved():
    """A bare count is not actionable; the operator needs to know which."""
    found = colliding_abbreviations([
        _row("1", "OSU", "Ohio State Buckeyes"),
        _row("2", "OSU", "Ohio State Newark Titans"),
        _row("3", "ALA", "Alabama Crimson Tide"),
    ])
    assert set(found) == {"OSU"}
    assert set(found["OSU"]) == {"Ohio State Buckeyes", "Ohio State Newark Titans"}


def test_an_unreviewed_collision_refuses_to_write(monkeypatch, tmp_path):
    """The whole point of the guard: never guess which school wins."""
    import backend.scripts.refresh_team_tables as mod
    monkeypatch.setattr(mod, "fetch", lambda sport: [
        _row("10", "XYZ", "Alpha Team"),
        _row("11", "XYZ", "Beta Team"),
    ])
    monkeypatch.setattr(mod, "OUT_DIR", tmp_path)

    with pytest.raises(SystemExit) as e:
        main(["--sport", "ncaaf"])
    assert "XYZ" in str(e.value)
    assert "Alpha Team" in str(e.value) and "Beta Team" in str(e.value)
    assert not list(tmp_path.iterdir()), "refused, but wrote a file anyway"


def test_the_ohio_state_collision_is_the_one_on_record():
    """If this entry is ever removed, the ncaaf refresh starts refusing."""
    assert RESOLVED_COLLISIONS[("ncaaf", "OSU")] == "194"
