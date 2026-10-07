# Criteria backlog

Handicapping ideas to test against the model. Add one whenever you think of it
-- a sentence is enough. Claude picks them up from here.

## How an idea gets in

The model's weight against the market is 0.00 for NFL and MLB: where they
disagree, the line wins. So a criterion only helps if it tells us something
**the closing line does not already know**. Each idea is measured before it is
wired in, with a read-only experiment script (`backend/scripts/*_experiment.py`)
against historical games and their closing prices.

The more specific the idea, the more testable it is. "Weather matters" is hard
to test; "totals with 15+ mph wind close too high" is a single regression.

Statuses: **untested** -> **testing** -> **no edge** / **better projections,
no proven edge** / **shipped**.

## Ideas

| # | Idea | Sport / market | Status | Result | Evidence |
|---|---|---|---|---|---|
| 1a | Injuries: starting QB out -> bet the side or total | NFL sides, totals | no edge | Close prices it fully: +0.87 pts left over (95% CI -0.85..+2.60), 302 absences. ATS 117-116 either way. First game of an absence no different (p 0.31). Even hindsight of who lost the job adds nothing (p 0.28). | `injury_experiment.py`, 2026-10-06 |
| 1b | Injuries: teammate out -> player props | NFL rush / rec yards | **shipped** (projections; football props stay tracking-only) | With leaders by yards, as production must define them: rushing leader out, other backs +26% (p 0.0005, n 204); receiving leader out, other receivers +17% (p 6e-6, n 634); passing leader out, receivers -7% (p 0.03). By touches the effects are +44% / +12% / -8%. Not yet tested against prop lines; re-check once this season has enough injury cases. Live status comes from ESPN team rosters. | `injury_experiment.py --leader-basis yards`, `football_injuries.py`, 2026-10-06 |
| 1c | Injuries: NBA star out -> teammates' props | NBA points / rebounds / assists | better projections, no proven edge | 2025-26 regular season, ESPN box scores: when the leading scorer sits (17.4% of team-games), teammates gain points +12.4% of baseline (95% +9.9..+14.9%, p 4e-22), rebounds +6.4% (p 3e-5), assists +7.9% (p 5e-4). Prop lines: only 33 matched Overs with the star out (hit 17/33), too few to read. | `nba_injury_experiment.py`, 2026-10-07 |
| 2a | Weather -> totals | NFL totals | **promising, not established** | **Rain/snow: total lands 4.3 pts under the close** (95% CI -7.8..-0.8, p 0.02; -4.27 with wind and cold controlled). Under 41-18, +19.3u. But: 4 features tried (corrected p about 0.08), 2025 went 7-7, and these are observed kickoff conditions, not forecasts. Wind: -0.16 pts/mph (p 0.14); 15+ mph Under 37-29, n.s. Cold: nothing (Under 22-26). | `weather_experiment.py`, 2026-10-06 |
| 2a' | Rain -> Under, using FORECASTS (the bettable version) | NFL totals | **tracking forward** | Open-Meteo archived forecasts, wet = >= 1.0 mm over the game's first 3 hours (fixed before fetching): total 7.6 under the close (95% CI -11.6..-3.7), **Under 26-8**, +15.6u, every season positive (2025 4-2). The threshold makes it monotonic: 0.5 mm -5.2, 2.0 mm -8.9. Same games the idea came from, so not independent. Live from the 2026-10-06 merge as `weather_rain_under` tracking picks; the weekly review reports them separately. | `weather_forecast_backtest.py`, 2026-10-06 |
| 2b | Weather -> player props | NFL pass / rec / rush yds | **shipped** (projections; props tracking-only) | Forecast-based: windy 15+, passing -15% and receiving -15%; wet, passing -14% and receiving -10%; rushing n.s. Observed-weather run had rain -14% / -15% and wind -10% on passing. | `weather_forecast_backtest.py`, `football_weather.py`, 2026-10-06 |
| 2c | Dome team on the road in the cold | NFL sides | no edge | -0.82 pts (p 0.74), fading the dome team 10-12. Only 22 games. | `weather_experiment.py`, 2026-10-06 |
| 3a | Travel distance / time zones / body clock | NFL sides | no edge | Distance -0.47 pts per 1,000 mi (p 0.42); tz east -0.11/hr (p 0.67); West Coast team before 11:00 body-clock time +0.49 (p 0.72, n 87, ATS 43-43). Distance and time zones barely predict the margin even without the line (p 0.28 / 0.43). The body-clock games are mostly good West Coast teams, which the line already favours by 1.6 pts. | `travel_experiment.py`, 2026-10-06 |
| 3b | Rest difference (byes, short weeks) | NFL sides | no edge (real, priced) | +0.38 pts per day of rest edge on the margin (p 0.012); the line moves +0.23/day (p 0.003); left over +0.14 (p 0.28). Backing a 3+ day rest edge: 124-113, -0.3u. Note `schedule_fatigue` is basketball-only, so NFL rest was never "already in". | `travel_experiment.py`, 2026-10-06 |

## Already measured

| Idea | Status | Result | Evidence |
|---|---|---|---|
| Off-market prices (bet the book out of line with the other 10) | no edge on its own | Moneylines at >= 2% EV vs the other books: rare (NFL 6 bets in 32 games, MLB 4 in 92) and worth about 0 at the close (NFL +0.31%, 95% -7.4..+8.4; MLB -2.40%), vs -4% for a typical quote. **But taking the best of 11 prices cuts the cost of a bet sharply:** NFL moneyline -4.09% -> -0.43%, spread -4.27% -> -2.07%, total -4.41% -> -2.74% (EV at close). | `price_shopping_experiment.py`, line_snapshots since 2026-09-23, 2026-10-07 |
| Timing: bet early, before the line moves toward the model | NFL / MLB moneylines | **not established; re-test as data builds** | Open = first fetch after both teams' previous games (no hindsight). NFL (32 games): the line does not move toward the model, c +0.025 (95% -0.06..+0.11, p 0.57); the model's side at the best open price -0.88% EV at close vs -1.84% at the close and about -1.6% for any side (no-model control). MLB (86 games): slight move toward the model, c +0.097 (p 0.04), the model's side at open -0.29% vs about -1.7% for any side. Secondary sport and two tests: a hint only. | `timing_experiment.py`, line_snapshots since 2026-09-23, 2026-10-07 |
| NBA model vs the closing spread | no edge | 2025-26, last 30% out of sample (376 games, every score matched): model adds +0.16 beyond the line (95% CI -0.19..+0.52, p 0.37); Brier model 0.1696 vs market 0.1594; betting its disagreements 188-186. Late season is the model's best case. | `nba_market_experiment.py`, public Kaggle closing spreads, 2026-10-06 |
| Team strength (EPA/play) | no edge | Adds nothing to the NFL close: coefficient -0.36, p 0.18, 1,171 games. Rules out team-level box-score ratings generally. | `epa_experiment.py`, 2026-09-22 |
| Turnover margin | no edge | Adds nothing to the close: +0.09, p 0.78, 1,187 games. Best variant 51.6% ATS, below 52.4% break-even. | `turnover_experiment.py`, 2026-10-04 |
| Opponent pass / run defense | better projections, no proven edge | Predicts player yards (c about 0.5, p < 1e-7) and is wired into NFL props at half weight. Prop lines show no sign of missing it (27 games). | `prop_matchup_experiment.py`, 2026-10-04 |
| Common opponents | not built | Opponent-adjusted scoring already covers it; few shared opponents early in a season. | 2026-10-04 |
| Defense, form, home/away | in the model | Team ratings, Elo, recent form, home advantage. | |
