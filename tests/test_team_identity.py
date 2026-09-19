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
