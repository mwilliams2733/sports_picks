"""What school is this string?

One definition, used by both the repair script and the live odds pipeline.

The Odds API names teams by display name ("Pennsylvania Quakers"); ESPN and
our own team rows key on an abbreviation ("PENN"). This module is the only
place that bridges the two. It is deliberately pure -- no session, no network,
no I/O beyond reading a committed snapshot once -- so it is cheap to test and
cannot behave differently in production than it does under pytest.
"""

from __future__ import annotations

import functools
import json
import pathlib
import re
import unicodedata

_DATA = pathlib.Path(__file__).resolve().parent / "data"

#: Sports whose identity is an abbreviation. Combat sports are excluded on
#: purpose: a fighter's name *is* the identity, so a display-name team row is
#: correct there and must not be "repaired".
ABBREVIATION_SPORTS = frozenset({"nba", "nfl", "ncaab", "ncaaf", "mlb"})

#: Schools whose Odds API label differs from ESPN's display name by more than
#: the St/State rule. Hand-written and deliberately short: every entry is a
#: claim that two strings name the same school, and a wrong entry silently
#: merges two programmes. Add only what has been checked by eye.
_ALIASES: dict[str, dict[str, str]] = {
    "ncaab": {
        "Prairie View Panthers": "Prairie View A&M Panthers",
        "LIU Sharks": "Long Island University Sharks",
        "Cal Baptist Lancers": "California Baptist Lancers",
        "Seattle Redhawks": "Seattle U Redhawks",
        "GW Revolutionaries": "George Washington Revolutionaries",
    },
    "nba": {
        # The Odds API spells the city out; ESPN does not. Same franchise --
        # note ESPN keeps "Los Angeles Lakers" in full, so this is a genuine
        # one-off rather than a rule about LA teams.
        "Los Angeles Clippers": "LA Clippers",
    },
}

_ST = re.compile(r"\bSt\b(?!\.)")


@functools.lru_cache(maxsize=None)
def _table(sport: str) -> tuple[dict[str, str], ...]:
    path = _DATA / f"{sport}_teams.json"
    if not path.exists():
        return ()
    return tuple(json.loads(path.read_text(encoding="utf-8")))


@functools.lru_cache(maxsize=None)
def _by_display(sport: str) -> dict[str, str]:
    return {r["display_name"]: r["abbreviation"] for r in _table(sport)}


def _fold(label: str) -> str:
    """Drop accents and apostrophes so two spellings of one name compare equal.

    ESPN writes "Louisiana Ragin' Cajuns" and "San Jose State Spartans" (with
    an acute e); the Odds API strips both marks. Folding is safe only because
    it merges nothing: across all 954 teams in the committed snapshots it
    produces zero collisions, and a test asserts that so a future snapshot
    cannot quietly break it.
    """
    decomposed = unicodedata.normalize("NFD", label)
    without_marks = "".join(
        c for c in decomposed if unicodedata.category(c) != "Mn"
    )
    return without_marks.replace("’", "").replace("'", "")


@functools.lru_cache(maxsize=None)
def _by_folded(sport: str) -> dict[str, str]:
    return {_fold(r["display_name"]): r["abbreviation"] for r in _table(sport)}


@functools.lru_cache(maxsize=None)
def _abbrs(sport: str) -> frozenset[str]:
    return frozenset(r["abbreviation"] for r in _table(sport))


def _normalise(label: str) -> str:
    """Spell out an abbreviated 'St'.

    The Odds API writes "Michigan St Spartans" where ESPN writes "Michigan
    State Spartans". The word boundaries keep "Stonehill" intact, and the
    negative lookahead keeps "St. John's" intact -- both are real ESPN
    display names that a naive replace would corrupt.

    Tested directly rather than through ``resolution_of``: an exact-matching
    label returns at the first tier and never reaches this rule, so an
    end-to-end test of it passes whatever the regex does.
    """
    return _ST.sub("State", label)


@functools.lru_cache(maxsize=None)
def _espn_ids(sport: str) -> dict[str, str]:
    return {r["abbreviation"]: r["espn_id"] for r in _table(sport)}


def espn_id_for(sport: str, abbreviation: str) -> str | None:
    """ESPN's own numeric team id for an abbreviation.

    Read from the committed snapshot rather than fetched, so it is offline,
    instant, and cannot disagree with the abbreviations the rest of the
    pipeline resolves to -- both come from the same file.

    Returns None for an unknown abbreviation or a sport with no snapshot
    (mma and boxing have no team concept). A miss usually means the snapshot
    is stale: re-run ``backend.scripts.refresh_team_tables --sport <sport>``.
    """
    return _espn_ids(sport).get(abbreviation)


def resolution_of(sport: str, label: str) -> tuple[str | None, str]:
    """Resolve ``label`` to an ESPN abbreviation, reporting how.

    Tiers run most-exact first and stop at the first hit, so a label that
    matches exactly is never reinterpreted by a looser rule.
    """
    if not label:
        return None, "unresolved"
    if label in _abbrs(sport):
        return label, "already_abbr"

    display = _by_display(sport)
    if label in display:
        return display[label], "exact"

    normalised = _normalise(label)
    if normalised in display:
        return display[normalised], "normalised"

    folded = _by_folded(sport).get(_fold(label))
    if folded:
        return folded, "folded"

    alias = _ALIASES.get(sport, {}).get(label)
    if alias and alias in display:
        return display[alias], "alias"

    return None, "unresolved"


def canonical_abbr(sport: str, label: str) -> str | None:
    """The ESPN abbreviation for ``label``, or None if we cannot tell."""
    return resolution_of(sport, label)[0]
