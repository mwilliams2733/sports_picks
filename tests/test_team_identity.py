from backend.team_identity import (
    ABBREVIATION_SPORTS,
    canonical_abbr,
    resolution_of,
)


def test_exact_display_name_resolves():
    assert canonical_abbr("ncaab", "Pennsylvania Quakers") == "PENN"


def test_state_is_spelled_out_before_matching():
    # The Odds API writes 'St' where ESPN writes 'State'.
    assert canonical_abbr("ncaab", "Michigan St Spartans") == "MSU"
    assert resolution_of("ncaab", "Michigan St Spartans")[1] == "normalised"


def test_hand_written_alias_resolves():
    assert canonical_abbr("ncaab", "GW Revolutionaries") == "GW"
    assert resolution_of("ncaab", "GW Revolutionaries")[1] == "alias"


def test_a_real_abbreviation_passes_through():
    assert canonical_abbr("ncaab", "PENN") == "PENN"
    assert resolution_of("ncaab", "PENN")[1] == "already_abbr"


def test_unknown_label_is_unresolved_not_guessed():
    assert canonical_abbr("ncaab", "Springfield Isotopes") is None
    assert resolution_of("ncaab", "Springfield Isotopes")[1] == "unresolved"


def test_st_is_not_expanded_inside_a_word():
    """Guard the rule directly.

    Going through canonical_abbr() would NOT test this: 'Stonehill Skyhawks'
    is an exact ESPN display name, so resolution stops at the first tier and
    never reaches the St rule. That version of this test passed even with the
    word boundaries deleted from the regex.
    """
    from backend.team_identity import _normalise

    assert _normalise("Stonehill Skyhawks") == "Stonehill Skyhawks"
    assert _normalise("St. John's Red Storm") == "St. John's Red Storm"
    assert _normalise("Michigan St Spartans") == "Michigan State Spartans"
    assert _normalise("North Dakota St Bison") == "North Dakota State Bison"


def test_stonehill_still_resolves_end_to_end():
    # Separate concern: that the exact tier handles it at all.
    assert canonical_abbr("ncaab", "Stonehill Skyhawks") == "STO"


def test_sports_without_a_table_resolve_nothing():
    assert canonical_abbr("mma", "Jon Jones") is None


def test_abbreviation_sports_excludes_combat_sports():
    assert "ncaab" in ABBREVIATION_SPORTS
    assert "mma" not in ABBREVIATION_SPORTS
    assert "boxing" not in ABBREVIATION_SPORTS


def test_every_abbreviation_in_the_snapshot_is_unique():
    from backend.team_identity import _table

    abbrs = [r["abbreviation"] for r in _table("ncaab")]
    assert len(abbrs) == len(set(abbrs)), "snapshot has duplicate abbreviations"


def test_exact_match_wins_over_a_looser_tier():
    # NB: resolution_of is not itself cached -- only the table loaders are --
    # so mutating _ALIASES takes effect immediately with no cache to clear.
    from backend import team_identity as ti

    ti._ALIASES["ncaab"]["Duke Blue Devils"] = "North Carolina Tar Heels"
    try:
        assert ti.resolution_of("ncaab", "Duke Blue Devils") == ("DUKE", "exact")
    finally:
        ti._ALIASES["ncaab"].pop("Duke Blue Devils")


def test_empty_label_is_unresolved():
    assert canonical_abbr("ncaab", "") is None


def test_nba_clippers_alias_resolves():
    """ESPN says 'LA Clippers'; the Odds API says 'Los Angeles Clippers'."""
    assert canonical_abbr("nba", "Los Angeles Clippers") == "LAC"
    assert resolution_of("nba", "Los Angeles Clippers")[1] == "alias"


def test_nba_lakers_are_not_abbreviated_by_the_same_rule():
    # ESPN keeps the Lakers' city in full -- the Clippers entry is a one-off,
    # not a rule about LA teams.
    assert canonical_abbr("nba", "Los Angeles Lakers") == "LAL"
    assert resolution_of("nba", "Los Angeles Lakers")[1] == "exact"


def test_an_all_star_team_stays_unresolved():
    """'Team Stripes' is a real All-Star roster, not a defect.

    It must land in the unresolved bucket so the repair leaves it alone.
    """
    assert canonical_abbr("nba", "Team Stripes") is None
    assert canonical_abbr("nba", "STRIPES") is None


def test_apostrophes_are_folded_before_matching():
    """ESPN writes "Louisiana Ragin' Cajuns"; the Odds API drops the apostrophe."""
    assert canonical_abbr("ncaaf", "Louisiana Ragin Cajuns") == "UL"
    assert resolution_of("ncaaf", "Louisiana Ragin Cajuns")[1] == "folded"


def test_accents_are_folded_before_matching():
    """ESPN writes "San Jose State Spartans" with an acute accent."""
    assert canonical_abbr("ncaaf", "San Jose State Spartans") == "SJSU"


def test_an_exact_name_still_wins_over_folding():
    # The exact tier must not be bypassed by the looser folded tier.
    assert resolution_of("ncaaf", "Louisiana Ragin' Cajuns")[1] == "exact"


def test_folding_introduces_no_collisions_in_any_snapshot():
    """Two distinct schools must never fold to the same key.

    Folding is only safe because it does not merge anything: verified across
    all 954 teams in the committed snapshots.
    """
    from backend.team_identity import _fold, _table

    for sport in ("ncaaf", "ncaab", "nba", "nfl", "mlb"):
        rows = _table(sport)
        if not rows:
            continue
        folded = [_fold(r["display_name"]) for r in rows]
        assert len(folded) == len(set(folded)), f"{sport} has a folding collision"


def test_the_other_sports_have_snapshots_now():
    """Without one, ABBREVIATION_SPORTS members silently skip every game."""
    from backend.team_identity import ABBREVIATION_SPORTS, _table

    for sport in ABBREVIATION_SPORTS:
        assert len(_table(sport)) > 0, f"{sport} has no committed team snapshot"


# --------------------------------------------------------------------------
# ncaaf aliases. The Odds API spells some schools out where ESPN abbreviates,
# and vice versa. Each of these was observed unresolved in a production odds
# fetch on 2026-09-19, skipping the game rather than creating an unmatchable
# row.
# --------------------------------------------------------------------------

import pytest

from backend.team_identity import canonical_abbr, resolution_of


@pytest.mark.parametrize("odds_name", [
    "UMass Minutemen",                  # ESPN: Massachusetts Minutemen
    "Southeastern Louisiana Lions",     # ESPN: SE Louisiana Lions
    "Appalachian State Mountaineers",   # ESPN: App State Mountaineers
])
def test_an_ncaaf_school_the_odds_api_names_differently_resolves(odds_name):
    abbr, how = resolution_of("ncaaf", odds_name)
    assert abbr is not None, f"{odds_name} still unresolved ({how})"


@pytest.mark.parametrize("odds_name", [
    "Sam Houston State Bearkats",       # truncated out of the 500-team page
    "Southern Mississippi Golden Eagles",
])
def test_a_school_beyond_the_first_page_resolves(odds_name):
    """These needed BOTH the paginated snapshot and an alias.

    ESPN calls them "Sam Houston Bearkats" and "Southern Miss Golden Eagles",
    so an alias alone would have pointed at a name the 500-team snapshot did
    not contain -- and a dead alias fails silently.
    """
    assert canonical_abbr("ncaaf", odds_name) is not None


def test_the_aliases_point_at_names_that_actually_exist():
    """An alias naming a display_name absent from the snapshot is dead.

    resolution_of falls through to unresolved when the target is missing, so
    a typo'd alias fails silently rather than raising.
    """
    from backend.team_identity import _ALIASES, _by_display
    for sport, table in _ALIASES.items():
        known = _by_display(sport)
        for label, target in table.items():
            assert target in known, f"{sport}: alias {label!r} -> {target!r} not in snapshot"
