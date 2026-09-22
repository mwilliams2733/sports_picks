"""Which PlayerStat fields settle a given prop market.

One definition, imported by both the analyser and the grader.

It used to be two: ``grader.MARKET_STAT_MAP`` and
``prop_analyzer.MARKET_TO_STAT``, retyped rather than shared. They had
already drifted apart by five keys when this module was written -- the
grader knew ``player_pass_tds`` and the analyser did not; the analyser knew
four alias spellings (``player_pass_yards``, ``player_rush_yards``,
``player_rec_yards``, ``player_touchdowns``) and the grader did not.

Neither half of that drift had bitten yet, and it is worth being precise
about why, because the reason is luck rather than design: every market
``odds_api.PROP_MARKETS`` actually requests for a supported sport happened to
sit in both maps. Had it not, the failure would have been silent and
one-sided -- a market the analyser knows but the grader does not produces
picks that can never be graded, which reads downstream as an ungraded pick
rather than as a bug.

The union is kept, including the alias spellings. They cost nothing, and a
market this file does not know is not a wrong answer but no answer: both
callers use ``.get`` and skip what they cannot resolve.

**Not covered: mlb.** ``PROP_MARKETS`` asks The Odds API for
``batter_hits``, ``batter_home_runs``, ``batter_total_bases`` and
``pitcher_strikeouts``, and none of the four appears here, because
``PlayerStat`` has no column any of them could settle against. Those props
are fetched and stored and can then be neither analysed nor graded.
Adding them is a schema change plus an mlb entry in
``prop_pipeline.build_default_collector``, which has none -- not a line in
this dict.
"""

#: Prop market key -> the PlayerStat field(s) whose sum settles it.
MARKET_STAT_MAP: dict[str, list[str]] = {
    # Basketball
    "player_points": ["points"],
    "player_rebounds": ["rebounds"],
    "player_assists": ["assists"],
    "player_threes": ["threes"],
    "player_blocks": ["blocks"],
    "player_steals": ["steals"],
    "player_turnovers": ["turnovers"],
    "player_points_rebounds_assists": ["points", "rebounds", "assists"],
    "player_points_rebounds": ["points", "rebounds"],
    "player_points_assists": ["points", "assists"],
    "player_rebounds_assists": ["rebounds", "assists"],
    # Football. The Odds API spells these "_yds"; the "_yards" spellings and
    # "player_touchdowns" are aliases carried over from the analyser's copy.
    "player_pass_yds": ["pass_yards"],
    "player_pass_yards": ["pass_yards"],
    "player_rush_yds": ["rush_yards"],
    "player_rush_yards": ["rush_yards"],
    "player_reception_yds": ["rec_yards"],
    "player_rec_yards": ["rec_yards"],
    "player_receptions": ["receptions"],
    "player_anytime_td": ["touchdowns"],
    "player_touchdowns": ["touchdowns"],
    # PlayerStat has one touchdowns column, so a passing touchdown settles
    # against the same field an anytime touchdown does. That is the existing
    # behaviour, carried over from the grader's copy rather than introduced
    # here; it is wrong for a quarterback and right for nobody else.
    "player_pass_tds": ["touchdowns"],
}
