
## Diagnostic Analysis Why did it happen?
 
Built primarily in Jupyter (pandas + scipy/statsmodels + matplotlib/seaborn), pulling from the same silver tables via SQL, then layering statistical tests and regressions on top. Each question below builds directly on its descriptive counterpart.
 
### Data prep checklist (before any correlation/regression)
- [X] Exclude or explicitly flag Safety Car / Red Flag / Yellow-flag-affected laps before using raw `lap_duration` in any pace model (confirmed distortion: 2023 Azerbaijan Sprint, laps 2–5).
- [X] Use `duration_race_seconds`, not `duration` (doesn't exist as a plain column  split during silver build; see earlier finding).
- [X] Remember `stop_duration` has zero coverage in 2023, partial from 2024  scope any `stop_duration`-based diagnostic to 2024+, or substitute `lane_duration`.
- [X] Remember the 7 confirmed 2023 ingestion gaps in `silver_session_result` (Bahrain Race, Azerbaijan Sprint, Hungarian/Belgian Qualifying, Mexico City Practice 3, Las Vegas Practice 1, Austrian/Qatar Sprint Qualifying)  these will show as missing rows, not zeros, in any join.
- [X] Team name drift year-over-year (e.g. AlphaTauri → RB → Racing Bulls)  apply a manual mapping before any multi-year team-level model.
### Pre-race, grid & setup
- [X] Why do some drivers/teams consistently qualify better than others is it car pace or driver skill? (compare teammates' qualifying deltas across a season  same car, isolates driver effect)
- [X] How strongly does grid position actually predict finishing position? (simple linear regression, R², correlation coefficient)
- [X] Does grid position's predictive power vary by circuit type (street vs. permanent, high-overtaking vs. processional)? (subgroup regression / interaction term)
### The start, lap 1
- [X] Do certain grid positions systematically gain or lose more places on lap 1? (regression: lap1_swing ~ grid_position)
- [X] Is lap-1 chaos (overtakes, incidents) more frequent at certain circuits? (grouped counts by circuit, chi-square)
### Race pace, lap by lap
- [X] What factors explain lap-time variation within a stint  tyre age, compound, track temperature, lap number (fuel-load proxy)? (multiple regression: lap_time ~ tyre_age + compound + track_temp + lap_number)
- [X] Does tyre degradation rate (slope of lap time vs. tyre age) differ by compound or by team? (compare regression slopes across groups / ANOVA)
- [X] Do anomalous laps cluster around a specific cause (Safety Car vs. genuine mistake) more for some drivers/teams than others? (categorize causes, chi-square)
- [X] Is a driver's sector strength consistent across multiple races (a real skill signal), or does it vary too much to be meaningful? (variance/consistency check across sessions)
### Tyre strategy
- [X] When teammates' strategies diverge, what predicts which one pays off  pit timing, track position at the stop, or pure pace? (logistic regression: better-finisher ~ predictors)
- [X] Is there a statistically real advantage to a specific strategy (fewer stops, a particular compound order) at a given circuit, or does it wash out once you control for starting position? (t-test / ANOVA comparing outcomes by strategy group)
### Pit stops
- [X] Is pit stop duration genuinely different by team (a crew-skill effect), or does it wash out once accounting for stop count/race chaos? (ANOVA across teams, or team as a regression dummy variable)
- [X] Does a slow pit stop reliably cost track position, or does pack density/traffic matter more? (correlate stop_duration against the position-swing data)
- [X] Are disaster stops (Tukey-fence outliers) random, or concentrated in specific teams or circuits? (chi-square)
### Position dynamics across the race
- [X] What predicts overtakes made  starting position, pace delta vs. the car ahead, tyre delta? (multiple regression)
- [X] Are the biggest position swings mostly explained by pit cycles and Safety Cars (as suspected from the Norris case), or is there a residual, unexplained portion once those are controlled for? (regression with pit-stop/Safety-Car dummy variables, examine residuals)
### Gaps & race context
- [X] Does spending more time "fighting" (within the 1.0s DRS zone) correlate with more overtakes attempted or made? (correlation)
- [X] Does being lapped correlate more with reliability issues (damage, mechanical) or a pure pace deficit? (compare group differences lapped vs. not, by cause)
### Incidents & external context
- [X] Does DNF/incident rate correlate with circuit type, team, or weather conditions? (chi-square: DNF vs. circuit type / team / rainfall)
- [X] Are certain teams' cars statistically more fragile, or is it concentrated in specific drivers? (compare DNF rate by team vs. by driver)
- [X] Does rain increase the *variance* of finishing positions across the field, not just average pace? (compare position variance in wet vs. dry races F-test / Levene's test)
- [X] Do specific teams or drivers statistically outperform their own dry-weather baseline in wet races? (paired comparison, same driver/team, wet vs. dry)
### Team radio
- [X] Does radio message frequency or clustering correlate with race outcome (incidents, position swings, points)? (correlation between message clustering and same-window events)
### Finish & outcome
- [X] Decompose a team's championship position: how much is explained by pace, how much by reliability, how much by strategy execution? (multiple regression: season points ~ pace_metric + dnf_rate + strategy_metric  direct bridge into the predictive/feature-engineering phase)
- [X] Is grid-to-finish net gain/loss statistically different by team (some teams race better than they qualify, or vice versa)? (ANOVA / regression)
### Driver vs teammate
- [X] Is a driver's advantage over their teammate (qualifying, race pace, points) statistically significant across a full season, or within normal race-to-race noise? (paired t-test across all races in a season)
- [X] Which factor  qualifying pace, race pace, reliability, or strategy execution  explains the most of the points gap between teammates? (regression decomposition)


 