// Confidence stars are hidden from the UI (owner decision 2026-09-28,
// implemented 2026-09-29) until a definition is validated. This is the one
// switch every star-based display and filter checks -- do not add a second
// one. Flipping it back to true un-hides everything at once: ConfidenceStars
// itself, the Track Record confidence breakdown, the Player Props /
// Today's Picks confidence columns and the Player Props minimum-confidence
// filter, and the FAQ's confidence-star explainer.
//
// The underlying confidence data and backend fields are untouched -- this
// only controls whether stars are drawn.
export const SHOW_STARS = false;
