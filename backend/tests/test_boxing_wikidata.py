"""Tests for boxing Wikidata SPARQL seed loader."""
import re

import pytest


def test_parse_sparql_results_extracts_records():
    """Standard SPARQL JSON binding shape → list of fighter dicts."""
    from backend.collectors.boxing_wikidata import parse_sparql_results

    sample = {
        "results": {
            "bindings": [
                {
                    "fighter": {"value": "http://www.wikidata.org/entity/Q3027894"},
                    "fighterLabel": {"value": "Mike Tyson"},
                    "wins": {"value": "50"},
                    "losses": {"value": "6"},
                    "draws": {"value": "0"},
                },
            ]
        }
    }
    fighters = parse_sparql_results(sample)
    assert len(fighters) == 1
    assert fighters[0]["name"] == "Mike Tyson"
    assert fighters[0]["wikidata_qid"] == "Q3027894"
    assert fighters[0]["wins"] == 50
    assert fighters[0]["losses"] == 6
    assert fighters[0]["draws"] == 0


def test_parse_sparql_results_handles_missing_optional_fields():
    """Wikidata's W/L/D properties are OPTIONAL; missing values must default to 0."""
    from backend.collectors.boxing_wikidata import parse_sparql_results

    sample = {
        "results": {
            "bindings": [
                {
                    "fighter": {"value": "http://www.wikidata.org/entity/Q123"},
                    "fighterLabel": {"value": "Unknown Boxer"},
                    # No wins/losses/draws keys at all.
                },
            ]
        }
    }
    fighters = parse_sparql_results(sample)
    assert len(fighters) == 1
    assert fighters[0]["wins"] == 0
    assert fighters[0]["losses"] == 0
    assert fighters[0]["draws"] == 0


def test_parse_sparql_results_handles_non_numeric_values():
    """SPARQL string bindings that aren't valid ints (e.g. quantity literals
    with units) must be treated as 0, not crash."""
    from backend.collectors.boxing_wikidata import parse_sparql_results

    sample = {
        "results": {
            "bindings": [
                {
                    "fighter": {"value": "http://www.wikidata.org/entity/Q456"},
                    "fighterLabel": {"value": "Garbage Data Boxer"},
                    "wins": {"value": "50 wins"},  # quantity-with-units, int() fails
                    "losses": {"value": "n/a"},
                    "draws": {"value": ""},
                },
            ]
        }
    }
    fighters = parse_sparql_results(sample)
    assert fighters[0]["wins"] == 0
    assert fighters[0]["losses"] == 0
    assert fighters[0]["draws"] == 0


def test_parse_sparql_results_returns_empty_for_empty_payload():
    """No bindings at all → empty list, not error."""
    from backend.collectors.boxing_wikidata import parse_sparql_results

    assert parse_sparql_results({}) == []
    assert parse_sparql_results({"results": {}}) == []
    assert parse_sparql_results({"results": {"bindings": []}}) == []


@pytest.mark.asyncio
async def test_fetch_boxer_records_hits_wikidata_endpoint(httpx_mock):
    """End-to-end fetch: hit the SPARQL endpoint, decode JSON, return parsed list."""
    from backend.collectors.boxing_wikidata import fetch_boxer_records

    httpx_mock.add_response(
        url=re.compile(r"https://query\.wikidata\.org/sparql.*"),
        json={
            "results": {
                "bindings": [
                    {
                        "fighter": {"value": "http://www.wikidata.org/entity/Q3027894"},
                        "fighterLabel": {"value": "Mike Tyson"},
                        "wins": {"value": "50"},
                        "losses": {"value": "6"},
                        "draws": {"value": "0"},
                    },
                ]
            }
        },
    )

    fighters = await fetch_boxer_records()
    assert len(fighters) == 1
    assert fighters[0]["name"] == "Mike Tyson"
