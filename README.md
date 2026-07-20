# F1 Reality Check: Question Bank
 
## Data Maturity Model
 
This project's analysis follows the four stages of the analytics maturity model:
 
The descriptive layer is pure facts about what happened in a race,no explanations. 
The diagnostic layer below reuses that same "story of a race" structure, but every question now asks *why*, and leans on statistics (correlation, regression, t-tests, ANOVA, chi-square) rather than a single query returning a fact. Parenthetical notes mark which statistical tool a question is expected to need  kept in as a memory aid, not a rigid prescription.
 
 
## Scope: The Story of a Race, Driver & Team Level
 
Goal: reconstruct the full chronological story of a **race** , once at the **driver** level and once at the **team** level (comparing teammates).
 
### Pre-race, grid & setup
- [x] What grid position did the driver start from, and what lap time earned it?
- [x] Which drivers have a grid position but no recorded qualifying lap time, and how many of those are genuine DNS cases vs. started-but-crashed/red-flagged/deleted-lap cases?
- [x] Team-level: what was the combined grid position of both cars (front-row lockout vs split across the field)?
### The start, lap 1
- [x] How did each driver's position change from their qualifying grid slot to the end of lap 1, and how many places did they gain or lose?
- [x] Was the driver involved in any lap-1 overtakes (as overtaker or overtaken)?
- [x] Did any race control flag/incident fire in the opening laps involving this driver?
### Race pace, lap by lap
- [x] What was the driver's lap time trend across the race (improving, degrading, flat)?
- [x] How does the driver's pace compare to their teammate, lap by lap?
- [x] Were there specific laps with anomalous times (red flag, traffic, mistake), and where do they fall in the race?
- [x] What were the driver's sector strengths/weaknesses (which sector were they consistently fastest/slowest in)?
- [x] What was the driver's fastest lap of the race, and on which lap number/tyre compound did it occur?
### Tyre strategy
- [x] How many stints did the driver run, on which compounds, and for how many laps each?
- [x] What was the driver's tyre age at the start of each stint?
- [x] Team-level: did both cars run the same strategy (compound sequence) or diverge?
### Pit stops
- [x] How many pit stops did the driver make, on which laps?
- [x] What was the stop duration and total lane duration for each stop?
- [x] Did any stop go unusually long (a "disaster stop")?
- [x] Team-level: how does average pit stop duration compare between the two drivers/cars?
### Position dynamics across the race
- [x] How did the driver's position evolve over the full race distance (a position-vs-lap trace)?
- [x] How many total overtakes did the driver make, and how many suffered?
- [x] At what points in the race did the biggest position swings happen (start, pit cycles, restarts)?
### Gaps & race context
- [x] How did the driver's gap to the leader evolve over the race?
- [x] How did the driver's gap to the car ahead/behind (interval) evolve, fighting, isolated, or lapped?
- [x] Was the driver lapped by the leader at any point, and when?
### Incidents & external context
- [x] What race control events (flags, safety car, DRS status) occurred during the driver's race, and did any coincide with their pit stops or position changes?
- [x] Was the driver specifically named in any race control message (penalty, investigation, warning)?
- [x] What were the weather conditions during the race, and did they change mid-race (rain arriving, track drying)?
### Team radio
- [x] How many radio messages were sent for this driver during the race, and at what points do they cluster (may signal key moments even without transcription)?
### Finish & outcome
- [x] What was the final classified position, and how does it compare to grid position (net gain/loss)?
- [x] Did the driver finish, DNF, DNS, or get DSQ'd, and if DNF, at what lap?
- [x] How many points did the driver score?
- [x] What was the gap to the winner at the finish?
- [x] Team-level: combined points haul for the race, and how it moved the constructor standings (`points_start` -> `points_current`).
### Driver vs teammate (team-level lens)
- [x] Who out-qualified whom, and by how much?
- [x] Who scored more points, and by how much?
- [x] Whose race had more incidents/pit stops/lost time?
- [x] Did the two cars' strategies converge or split, and which paid off?



 
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
- [ ] Does spending more time "fighting" (within the 1.0s DRS zone) correlate with more overtakes attempted or made? (correlation)
- [ ] Does being lapped correlate more with reliability issues (damage, mechanical) or a pure pace deficit? (compare group differences lapped vs. not, by cause)
### Incidents & external context
- [ ] Does DNF/incident rate correlate with circuit type, team, or weather conditions? (chi-square: DNF vs. circuit type / team / rainfall)
- [ ] Are certain teams' cars statistically more fragile, or is it concentrated in specific drivers? (compare DNF rate by team vs. by driver)
- [ ] Does rain increase the *variance* of finishing positions across the field, not just average pace? (compare position variance in wet vs. dry races F-test / Levene's test)
- [ ] Do specific teams or drivers statistically outperform their own dry-weather baseline in wet races? (paired comparison, same driver/team, wet vs. dry)
### Team radio
- [ ] Does radio message frequency or clustering correlate with race outcome (incidents, position swings, points)? (correlation between message clustering and same-window events)
### Finish & outcome
- [ ] Decompose a team's championship position: how much is explained by pace, how much by reliability, how much by strategy execution? (multiple regression: season points ~ pace_metric + dnf_rate + strategy_metric  direct bridge into the predictive/feature-engineering phase)
- [ ] Is grid-to-finish net gain/loss statistically different by team (some teams race better than they qualify, or vice versa)? (ANOVA / regression)
### Driver vs teammate
- [ ] Is a driver's advantage over their teammate (qualifying, race pace, points) statistically significant across a full season, or within normal race-to-race noise? (paired t-test across all races in a season)
- [ ] Which factor  qualifying pace, race pace, reliability, or strategy execution  explains the most of the points gap between teammates? (regression decomposition)


 
## Notes & Decisions Log
 
*Track filter/scope decisions, data quality findings, and reusable patterns here as they come up.*
 
1. **`silver_starting_grid` scope ,data dictionary is wrong.** Scoped to `session_name IN ('Qualifying', 'Sprint Qualifying')`, not Race/Sprint as documented.
2. **Composite-key join rule.** Any table keyed on `(session_key, driver_number)` must be joined on both columns `session_key` alone silently fans out (SQLite won't error).
3. **`session_name`, not `session_type`, for grid↔race pairing.** Pair on `('Race', 'Qualifying')` or `('Sprint', 'Sprint Qualifying')`  `session_type` groups both qualifying types together and will fan out a grid join.
4. **Non-driver-keyed tables also fan out.** `silver_race_control` has many rows per session with no `driver_number` requirement  use a correlated subquery, not a direct join, when pulling it into a per-driver query.
5. **`silver_overtakes.position` is one-sided** "position gained by the overtaker" only, not a general resulting position for both drivers.
6. **DNS logic.** `dns = 1` -> `lap_duration IS NULL` always holds; the reverse doesn't (crashes/red flags/deleted laps also produce nulls without being DNS).
7. **`stop_duration` coverage gap.** Zero coverage in 2023 across all session types; partial coverage from 2024 onward. Use `lane_duration`/`pit_duration` for anything spanning 2023.
8. **`lane_duration` and `pit_duration` are identical in every row where both are populated** (confirmed: 20,745/20,745)  cause unconfirmed, kept as separate columns anyway.
9. **Tukey fence for disaster stops**: `Q3 + 1.5 × IQR` on `stop_duration` ≈ 4.9 seconds, derived from the full-distribution stats, not a guessed round number.
10. **Race control glimpse has two confirmed causes for a blank result**: (1) an event was happening but the message wasn't tagged to that exact lap number (Safety Car spanning multiple laps confirmed via Albon, 2023 Azerbaijan Sprint lap 4), or (2) nothing needed logging because the issue was a private mechanical problem with no track-wide flag warranted (confirmed via Stroll, 2023 US GP Sprint lap 16, brake failure/DNF).
11. **DRS "fighting" threshold** = ≤1.0s gap to the car ahead  the official F1 DRS detection zone, borrowed from the sport's own rules rather than a statistically derived cutoff.
12. **Position-swing threshold** = ≥3 places in one lap a reasoned cutoff, not statistical (position deltas are small bounded integers, so Tukey/IQR fences don't fit well; 1–2 place changes are normal racing, 3+ is almost always tied to a specific event).
13. **`silver_session_result` has zero rows for 7 specific, non-cancelled 2023 sessions**: Bahrain Race, Azerbaijan Sprint, Hungarian Qualifying, Belgian Qualifying, Mexico City Practice 3, Las Vegas Practice 1, Austrian Sprint Qualifying, Qatar Sprint Qualifying confirmed via `is_cancelled = 0` that these sessions genuinely took place. This is a real ingestion gap, not a data-model artifact. (Separately, Emilia Romagna's full 5-session zero-count is *not* a gap  that meeting has `is_cancelled = 1`, correctly reflecting the real-world flood cancellation.)
14. **`silver_session_result.duration` doesn't exist as a plain column.** Silver build split it into `duration_race_seconds` (REAL) and `duration_quali_json` (TEXT) to resolve the ambiguity the data dictionary flagged. Use `duration_race_seconds` for Race-session total time.
