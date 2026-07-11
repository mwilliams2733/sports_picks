// Mirrors backend.pipeline.full_pipeline.ALL_SPORTS, plus the 'all' filter
// option that only makes sense in the UI. Kept as a plain frontend constant
// rather than fetched from an API — no backend endpoint currently serves it.
export const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf', 'mlb', 'boxing', 'mma'] as const;

export type Sport = (typeof SPORTS)[number];
