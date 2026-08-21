# Notes Log

A running record of data quality findings, methodological decisions, and reusable
patterns for the F1 Reality Check project.

**What belongs here:** anything that was expensive to discover and would be expensive to
rediscover. Data that isn't what it claimed to be, thresholds and why they were chosen,
scope decisions, and tooling gotchas.

**What doesn't:** analytical results. Those live in the question banks and the README.

Each entry is dated where known and states the evidence, not just the conclusion.


## Table of contents

- [Data quality — schema and types](#data-quality--schema-and-types)
- [Data quality — coverage and gaps](#data-quality--coverage-and-gaps)
- [Data quality — semantics](#data-quality--semantics)
- [Join and query patterns](#join-and-query-patterns)
- [Threshold and scope decisions](#threshold-and-scope-decisions)
- [Tooling gotchas](#tooling-gotchas)
- [Open questions](#open-questions)


## Data quality — schema and types
openf1_ingestion.py cached failures as completions. fetch() returned [] on timeout, HTTP error, and genuine empty response alike. ingest_per_session() then wrote that to _ingestion_progress regardless, so already_fetched() skipped the pair forever. Any transient network failure, or any fetch issued before a session had taken place, became a permanent, invisible gap. Found 2026-07-27: 282 affected (endpoint, session) pairs across all four seasons, including three 2026 races fetched on 30 June before they were run. Fixed in s01_backfill.py by recording a three-state outcome (ok / empty / failed) and never marking a failure terminal. Recovered 320,127 rows.
### 1. `silver_session_result.duration` mixed scalars and JSON — silently corrupted by CAST
*Phase: IDA*

The raw column held a scalar (total race time) for Race sessions and a JSON object
(per-phase fastest laps) for Qualifying. `CAST(... AS REAL)` returned partial garbage
without erroring. Same problem in `gap_to_leader`, which mixed seconds with lap-deficit
strings (`"+1 LAP"`).

**Resolution:** split into five typed columns during the silver build —
`duration_race_seconds`, `duration_quali_json`, `gap_to_leader_seconds`,
`gap_to_leader_laps`, `gap_to_leader_quali_json`. There is no plain `duration` column in
silver.

**Why it matters:** this is the project's worst failure mode — wrong numbers with no
error. The verification gate treats missing split columns as a hard FAIL.

### 2. `silver_intervals` needed the same split
*Phase: IDA*

`interval` and `gap_to_leader` mixed numeric strings with lap-deficit strings. Split into
`interval_seconds` / `interval_laps` and `gap_to_leader_seconds` / `gap_to_leader_laps`.
Exactly one of each pair is non-null per row.

**Consequence for analysis:** averaging gaps naively drops lapped drivers, because their
`_seconds` value is NULL. Being lapped by the leader is common (~9% of rows, 170,696);
being lapped by the car directly ahead is rare (65 rows).

### 3. `silver_overtakes` primary key is four columns, not three
*Phase: IDA*

The initial hypothesis `(session_key, date, overtaking_driver_number)` is violated by
2,018 rows. Cause: a driver can pass several cars at the same recorded timestamp a
first-lap melee where timing resolution can't separate the passes.

**Real PK:** `(session_key, date, overtaking_driver_number, overtaken_driver_number)`.

### 4. `silver_weather` had 88 fully-identical duplicate rows
*Phase: IDA*

Raw had 43,003 rows; 42,915 distinct. Deduplicated with `SELECT DISTINCT` on insert.
Verified still clean as of 2026-07-26.

### 5. `silver_position` and `silver_race_control` required synthetic PKs
*Phase: IDA*

`silver_position` has 68 same-driver-same-second duplicate timestamps.
`silver_race_control` has 42 timestamp collisions from simultaneous flag events. Both
use `INTEGER PRIMARY KEY AUTOINCREMENT` with indexes on the natural access pattern.

### 6. One session has NULL `team_name` despite a NOT NULL intent
*Phase: IDA*

14 rows, isolated to 2023 Hungarian GP Practice 1 — a young-driver test session with no
team assigned upstream. `normalize_team_names()` drops these; there is no meaningful
team bucket to map them to.


## Data quality — coverage and gaps

### 7. Eleven ingestion gaps in `silver_session_result`
*Eight found during IDA; three found 2026-07-26 , all were recovered, and that the cause was the resumability bug, not upstream absence.*

**2023 (8 sessions):** Bahrain Race, Azerbaijan Sprint, Hungarian Qualifying, Belgian
Qualifying, Mexico City Practice 3, Las Vegas Practice 1, Austrian Sprint Qualifying,
Qatar Sprint Qualifying.

**Previously undocumented (3 Race sessions):**

| session_key | Race | Date |
|---|---|---|
| 9507 | Miami Grand Prix | 2024-05-05 |
| 9928 | Hungarian Grand Prix | 2025-08-03 |
| 9869 | São Paulo Grand Prix | 2025-11-09 |

All confirmed `is_cancelled = 0` — genuinely missing, not cancelled events.

**Critically, these three are results-only gaps.** Laps, pits, stints and positions are
all fully populated:

| session_key | laps | pits | stints | positions | results |
|---|---:|---:|---:|---:|---:|
| 9507 | 1,112 | 28 | 48 | 403 | 0 |
| 9869 | 1,252 | 37 | 57 | 759 | 0 |
| 9928 | 1,369 | 29 | 49 | 482 | 0 |

That pattern indicates a single failed endpoint during ingestion, not a lost session 
so targeted re-ingestion of the results endpoint should recover them. Worth doing: they
are 3 of 69 possible training races.

**Validation route after backfill:** `silver_championship_drivers` snapshots points
before and after each session, so the points delta across those sessions is an
independent check on the recovered figures.

**Note:** these were found by running `check_missing_sessions()` across all seasons  a
function that already existed but had never been run for 2024/2025 `'Race'`. Argument for
the gate running automatically rather than on recall.

### 8. Three `silver_laps` gaps
*Phase: IDA*
The three lap gaps weren't permanently missing; they recovered too. Explicitly note this corrected the earlier hypothesis that they were never available upstream.
session_keys 9165 (Singapore 2023), 9655 (Qatar 2024), 9858 (Las Vegas 2025).

Unlike #7 these have persisted across the whole project, suggesting the data was never
available upstream rather than a transient failure.

### 9. Emilia Romagna 2023 is not a gap
*Phase: IDA*

All five sessions show zero rows, but `is_cancelled = 1` correctly reflecting the
real-world flood cancellation. Three meetings across 2023–2026 are cancelled.

### 10. `stop_duration` is effectively unusable in every season
*Coverage measured 2026-07-26  earlier notes understated this*

| Year | Stops with `stop_duration` | Coverage |
|---|---|---:|
| 2023 | 0 / 5,235 | 0.0% |
| 2024 | 124 / 8,559 | 1.4% |
| 2025 | 705 / 8,946 | 7.9% |
| 2026 | 132 / 4,051 | 3.3% |

Earlier notes described this as "zero in 2023, partial from 2024," which implied 2024+
was workable. It isn't.

**Use `lane_duration` instead** identical to `pit_duration` in all 20,745 rows where
both are populated (cause of the duplication unconfirmed; kept as separate columns).

**Action required:** any diagnostic finding that used `stop_duration` scoped to 2024+
rested on ~1.4% coverage and should be revisited. This affects the "pit stop duration is
team-specific" result.

### 11. `country_code` in `silver_drivers` is NULL for 2025–2026
*Phase: IDA*

The API stopped populating it. 5,240 nulls overall  the majority of rows.

### 12. Telemetry covers only 32 of 490 sessions
*Phase: IDA*

`silver_car_data` (9,365,942 rows) and `silver_location` (25,849,231 rows) are ~35M of
the database's 6.5 GB but cover a small minority of sessions.

**Consequence:** unusable as model features  there would be no coverage for the vast
majority of races. Excluded from the scheduled pipeline entirely; refreshed manually on
demand. Useful only for one-off deep dives.

### 13. `silver_sessions` contains the full future calendar
*Found 2026-07-26*

126 sessions registered for 2026 but only 44 with results; 8 races completed. The latest
row is Abu Dhabi, 2026-12-06 four months in the future.

**Consequence:** any `WHERE year = 2026` filter silently includes races that haven't
happened. Filter on the presence of results, not on year.

**Upside:** the upcoming race schedule is already available locally, so prediction
doesn't need an external calendar source.

### 14. True modeling sample size
*Established 2026-07-26*

| Year | Sessions scheduled | With results | Races completed |
|---|---:|---:|---:|
| 2023 | 118 | 105 | 22 |
| 2024 | 123 | 115 | 24 |
| 2025 | 123 | 117 | 24 |
| 2026 | 126 | 44  | 11 |

**66 training races, 8 in 2026.** For winner prediction the effective sample is races,
not driver-rows  one winner per race means 66 events, not ~1,320. At roughly 10–15
events per predictor this supports 4–6 features, and rules out gradient boosting as a
primary model.

**Consequence for validation:** 8 held-out races cannot distinguish skill from luck. Use
rolling-origin cross-validation instead.


## Data quality — semantics

### 15. `silver_starting_grid` scope contradicts the data dictionary
*Phase: descriptive*

It covers **Qualifying and Sprint Qualifying** sessions — not Race and Sprint as
documented. Confirmed empirically: 1,430 rows under `'Qualifying'`, 384 under
`'Sprint Qualifying'`.

### 16. `session_key` is not monotonic with date
*Found 2026-07-26*

São Paulo (9869, November 2025) has a **lower** key than Hungary (9928, August 2025).

**Never use `session_key` as a chronological proxy.** Sort on `date_start`. Critical when
building trailing features ordering by key would leak future information into the past.

### 17. `rainfall` is a state, not an amount
*Phase: IDA*

A 0/1 flag per weather sample, not millimetres. Cannot be summed. To measure how wet a
session was: `SUM(rainfall) / COUNT(*)` for the proportion of samples with rain. ~4% of
all samples show rain.

### 18. Extreme values in `silver_laps` and `silver_pit` are real
*Phase: IDA*

A 3,510-second lap is a car sitting under a red flag. A `pit_duration` of 16,921 seconds
(~4.7 hours) is a car in the pit lane during a red-flag suspension.

**These stay in silver.** Silver preserves reality; filtering belongs downstream where
the choice is explicit and documented.

### 19. `lap_end < lap_start` in `silver_stints` is legitimate
*Phase: IDA*

24 rows, two patterns: `lap_end = lap_start - 1` means the driver retired or pitted
before completing a started lap; `lap_start = 1, lap_end = 0` are phantom stints from
cancelled sessions. Filter with `WHERE lap_end >= lap_start` for real stints.

### 20. Not all "overtakes" are on-track passes
*Phase: descriptive*

`silver_overtakes` includes pit-cycle position gains and post-race penalty swaps. The
API's own docs warn the data may be incomplete. Cross-reference `silver_position`
timestamps to isolate genuine racing passes.

### 21. `silver_overtakes.position` is one-sided
*Phase: descriptive*

It records the position **gained by the overtaker** only  not a resulting position for
both drivers.

### 22. DNS logic is one-directional
*Phase: descriptive*

`dns = 1` implies `lap_duration IS NULL` always. The reverse does **not** hold  crashes,
red flags, and deleted laps also produce nulls without being a DNS.

### 23. Team name drift across seasons
*Phase: IDA, extended 2026*

Fifteen raw team names collapse to a smaller constructor set. Two rename chains:

- AlphaTauri → RB → Racing Bulls → `RB Family`
- Alfa Romeo → Kick Sauber → Audi → `Sauber Family`

Handled by `normalize_team_names()`. **Must be applied before any multi-year team
aggregation** or one constructor splits into three.

**Cadillac is deliberately not mapped**  a genuinely new 2026 constructor, not a
rename and is excluded from comparative analyses on sample-size grounds (n ≈ 8–10
across most tables).

### 24. Blank race control results have two confirmed causes
*Phase: descriptive*

When a lap has an incident but no race control message: either (a) the event wasn't
tagged to that exact lap a Safety Car spanning multiple laps, confirmed via Albon,
2023 Azerbaijan Sprint lap 4; or (b) nothing needed logging because it was a private
mechanical problem warranting no track-wide flag, confirmed via Stroll, 2023 US GP
Sprint lap 16, brake failure and DNF.


### 24b. Championship standings are snapshotted before post-race penalties
*Phase: descriptive*

`silver_championship_teams.points_current` minus `points_start` does **not** equal
the points a team actually scored in that race. Across 78 races, 12 disagree.

Two causes, both confirmed:

- **Sprint weekends (5 of the 12).** The delta covers the whole weekend, so it
  includes sprint points banked before the Grand Prix.
- **Post-race penalties (the other 7).** The standings are recorded as the session
  ends, before stewards apply disqualifications. 2024 Belgian GP: Russell carries
  `dsq = 1` and 0 points in `silver_session_result`, yet Mercedes' championship
  delta is 43, which is Hamilton's 25 plus Russell's original 18. 2025 Las Vegas:
  both McLaren cars disqualified and scored 0, but the delta reads 30.

**Use each for what it is.** `fact_driver_race.points` is the final classification
and is what "points scored in this race" means. `fact_championship` is the standings
as published at session end, and is what "where the team sat in the table" means.
Never derive one from the other. `fact_championship.points_gained` is therefore
labelled as a weekend/championship movement in the dashboard, not as race points.

Coverage note: 2026 has standings for 8 of 11 races, and 30 rows have a null
`position_start` (season openers, where there is no prior standing).

## Join and query patterns

### 25. Composite keys must be joined on every column
*Phase: descriptive*

Any table keyed on `(session_key, driver_number)` must join on **both**. Joining on
`session_key` alone silently fans out  SQLite will not error, the row count will just be
wrong. This has caused real bugs in this project.

### 26. Pair grid to race on `session_name`, not `session_type`
*Phase: descriptive*

`session_type` groups Qualifying and Sprint Qualifying together and will fan out a grid
join. Pair on `('Race', 'Qualifying')` or `('Sprint', 'Sprint Qualifying')`.

### 27. Non-driver-keyed tables also fan out
*Phase: descriptive*

`silver_race_control` has many rows per session with no `driver_number` requirement. Use
a correlated subquery, not a direct join, when pulling it into a per-driver query.

### 28. Timestamp and time-series patterns
*Phase: diagnostic*

- `format='ISO8601'` for `pd.to_datetime()` on silver timestamps — they mix formats
- `merge_asof` for nearest-timestamp lookups (position, intervals, weather joined to lap
  boundaries)

### 29. Separate detail-level from summary-level queries
*Phase: descriptive*

Rather than merging both grains into one query with mixed aggregation. Clearer, and
avoids accidental fan-out.


## Threshold and scope decisions

### 30. Tukey fences derived fresh per dataset, never hardcoded
*Phase: diagnostic*

`Q3 + 1.5 × IQR` computed on the actual distribution in scope. For 2024+ pit stop data
this gave 4.65s; an earlier full-distribution calculation gave ~4.9s. The number changes
with the data, which is the point.

### 31. Minimum sample sizes come from power calculations, not round numbers
*Phase: diagnostic*

This repeatedly meant **declining to claim a result**:

- Driver-level DNF analysis needs n = 233 starts per driver for 80% power at the
  team-level effect size — roughly ten seasons. Not achievable. Reported as descriptive.
- Wet-weather specialisation needs 30 wet races per entity; ~15 available. Reported as
  descriptive with the limitation stated.

### 32. DRS "fighting" threshold = 1.0s
*Phase: diagnostic*

Borrowed from F1's own DRS detection window rather than derived statistically. Domain
rules are preferred to invented cutoffs where they exist.

### 33. Position-swing threshold = 3 places in one lap
*Phase: diagnostic*

Reasoned, not statistical. Position deltas are small bounded integers, so Tukey/IQR
fences fit poorly. 1–2 place changes are normal racing; 3+ is almost always tied to a
specific event.

### 34. Bonferroni correction throughout
*Phase: diagnostic*

α = 0.05 / n_tests for any family of comparisons.

### 35. Session-median normalization for all cross-circuit pace comparison
*Phase: diagnostic*

Raw lap times are confounded by circuit mix. Driver mean lap minus session median is the
comparable unit — and became the strongest single feature in the model.

### 36. Filtering belongs in the diagnostic and predictive layers
*Phase: descriptive*

The descriptive layer preserves raw data as-is, including Safety Car laps. Filtering is a
modelling choice and should be explicit where the model is built, not baked into
foundational queries.

### 37. VIF checks and standardized coefficients
*Phase: diagnostic*

VIF before interpreting any multiple-regression coefficient. Standardized coefficients
when comparing predictors measured on different scales.

### 38. Event-level rebuilds when race averages hide the phenomenon
*Phase: diagnostic*

Learned from the overtakes analysis: aggregating to race level averaged away the effect
entirely. Some questions require the event grain.


## Tooling gotchas

### 39. DB Browser silently swallows INSERT errors
*Phase: IDA*

Constraint violations don't surface. Use Python's `sqlite3` module to see real errors.

### 40. No module-level database connections
*Decided 2026-07-26*

`data_prep.py` originally opened a read-write connection at import time and never closed
it. Problems: an accidental `to_sql()` could mutate the database; the connection kept
`-wal`/`-shm` files alive for the process lifetime; it caused lock contention with DB
Browser; and it wasn't thread-safe.

**Replaced with** `get_connection()`, a context manager that opens **read-only** by
default and closes on exit. Pass `read_only=False` only from an explicit write step.

This pattern also probably explains a stray 5.8 MB `f1.db-wal` found at the project root
with no accompanying database — an earlier version connecting with a relative path from a
different working directory, which caused SQLite to create an empty database there.
**A relative-path connection that finds nothing creates an empty database rather than
erroring**, and queries against it return zero rows silently.

### 41. Path anchoring
*Phase: diagnostic*

Use `Path(__file__).resolve().parent` rather than relative paths, so behaviour doesn't
depend on the working directory. All pipeline paths now live in `pipeline/config.py` as
the single source of truth.

### 42. Environment pinned to Anaconda Python 3.13.9
*Decided 2026-07-26*

pandas 2.3.3, scipy 1.16.3, statsmodels 0.14.5, scikit-learn 1.7.2 — the environment the
diagnostic phase actually ran in.

A stray `.venv` on Python 3.14 with **pandas 3.0.3** was found in the project and
removed. Pandas 3.x has breaking changes; activating that environment and re-running the
diagnostic notebooks would have produced failures or different numbers that looked like
data problems. Versions are pinned in `environment-pipeline.yml` and `requirements.txt`
specifically to prevent a future `conda update` from doing this silently.


### 43. The scheduled task never ran, for four months, silently
*Found 2026-08-10*

The weekly pipeline runs from Windows Task Scheduler, not from anything in this repo, so
nothing in version control records that it exists. Its action had been entered as an
unquoted path, and Task Scheduler split it at the space in the user folder name:

```
execute: C:\Users\Fatima
args:    zahra\Projects\F1-Reality-Check\run_weekly.bat
```

Every run since 2026-08-03 returned `0x80070002`, "the system cannot find the file
specified", in milliseconds. `run_weekly.bat` was never reached.

**Why nobody noticed.** Task History was disabled, so the failures left no visible trace.
And the batch file ends with a `MessageBox` success popup, which can only appear if the
batch file runs, so its absence read as "no news" rather than "never started". The only
evidence was indirect: no `logs/pipeline_*.log` newer than 2026-07-29.

**Resolution:** quote the full path in Program/script and leave Add arguments empty.
Re-enable Task History. If the pipeline appears not to have run, check
`(Get-ScheduledTask -TaskName 'F1 Reality Check Pipeline' | Get-ScheduledTaskInfo)`
for `LastTaskResult`, and treat "no new log file" as a failure signal in its own right.

### 44. A backfill leaves silver behind bronze, and nothing says so
*Found 2026-08-10*

`run_pipeline.py` decides whether to rebuild silver from the row count it parses out of
**`s01_ingest`**'s output. `s01_backfill.py` is not a pipeline step: it writes straight
into bronze. So a backfill can recover any quantity of data and the pipeline will still
conclude there is nothing new to rebuild.

This is not hypothetical. On 2026-07-27 a backfill recovered 324,207 rows into bronze.
The diagnostic notebooks were then executed against a silver that did not yet contain
them, and their stored conclusions drifted from the dashboard's without anything
reporting a fault. Every invariant in the gate passed the whole time, because every
invariant was true: silver was internally consistent, just built on less data.

**Resolution:** `s02_build_silver.py` now records the bronze row count it read into
`_silver_build_state`, and gate check [20] compares that against bronze on every run.
Bronze larger than the recorded figure means a rebuild is owed, and the check names the
tables and prints the command. Comparing raw counts would not work, because the silver
build types, dedupes and filters, so silver is legitimately smaller by a ratio nobody had
written down.

**After any manual `s01_backfill.py` run, rebuild silver.** The gate will now say so, but
only on its next run.

### 45. An `empty` verdict from OpenF1 is not permanent
*Found 2026-08-10*

`s01_backfill.py` records a definitive `empty` and never re-queries it, which is right for
the common case: HTTP 404 with `{"detail":"No results found."}` is OpenF1's genuine "no
data" answer, confirmed against races that really had no pit stops.

But OpenF1 backfills its own data. The 2023 Belgian Grand Prix qualifying classification
was recorded `empty` on a 404 in July 2026 and returned 20 rows when asked again in
August. Terminal-forever quietly locks in whatever was missing upstream at first contact.

**Resolution:** `--recheck-empty` re-queries confirmed-empty pairs. Worth an occasional
run, not a routine one. Of 80 such pairs, 79 were still genuinely empty.

Two related API facts, measured the same day. A successful response carries **no**
rate-limit headers, so the ceiling cannot be read in advance. A **429 does** carry
`Retry-After`, and OpenF1 asks for 60 seconds. A misspelled endpoint returns a 404
byte-identical to the real "no data" answer, which is why only endpoints on a known list
are allowed a terminal verdict.

### 46. A calendar change grows bronze while ingest truthfully reports zero new rows
*Found 2026-08-10*

`s01_ingest.py` refreshes the global tables (`meetings`, `sessions`, `drivers`) by **full
replace**, and those rows are not counted in the `rows inserted:` figure it prints, which
tracks per-session endpoint fetches only. `run_pipeline.py` used that figure alone to
decide whether to rebuild silver.

So when the F1 calendar changes, bronze takes the new rows, ingest correctly reports
`new rows: 0`, and silver is never rebuilt. If the new sessions are in the future there is
nothing to fetch for them either, so no other counter moves.

Caught on the first real run after gate check [20] was added. OpenF1 published meeting
1308, the 2026 Bahrain Grand Prix, with five sessions on 2 to 4 October. Bronze went to
101 meetings and 495 sessions, silver stayed at 100 and 490, and the gate stopped the
pipeline before the serving layers could be built on the mismatch.

**Resolution:** `run_pipeline.py` now has a second, independent rebuild trigger. Alongside
`rows_inserted()` it calls `stale_tables()`, which compares bronze against
`_silver_build_state` and rebuilds when bronze is ahead. This also covers anything
`s01_backfill.py` wrote, per entry 44, since that never passes through the ingest counter
either.

The general lesson, and it is the third time this project has hit it: **a step reporting
"nothing happened" is not evidence that nothing happened.** Prefer comparing recorded
state over parsing what a step chose to say about itself.

### 47. Caution periods were wrong in three separate ways
*Found and fixed 2026-08-11, while trying to choose a lap-validity rule*

`silver_lap_flags.neutralised` is read at 121 sites and is the one decision this project
had single-sourced. It was also wrong.

**(a) The fallback end was the SCHEDULED session end.** `build_periods` closed an
unterminated period with `silver_sessions.date_end`. But **326 of 495 sessions run past
their scheduled end**, and a red-flagged race always does. Eighteen periods therefore
ended before they began, durations to **-2,226 seconds**, and the lap-to-period overlap
join matched nothing at all. Melbourne 2023 lap 57: ten cars averaging 1,997 seconds,
every one recorded as green-flag racing.

The eighteen negatives were the visible half. Another 36 periods closed the same way were
merely **truncated**, leaving the tail of a real caution flagged green at times that look
entirely plausible and would never surface in an outlier search.

**(b) Extending to the TRUE session end was worse.** That was the first fix, and it
over-corrected badly. Monaco 2024 logs a RED FLAG at 13:04:08 and never logs the restart,
yet the race resumed and ran to full distance. The period ran 2.4 hours and flagged
**all 1,237 laps of the race**. Caught only because the newly flagged laps had a median
ratio of 1.00x against green-flag pace, which is not what a caution lap looks like.

The laps themselves carry the answer, but **not** in the obvious way, and the obvious way
was tried and rejected on evidence.

*Rejected: "the first lap after the stoppage that runs at a plausible pace."* It is
decided by whichever single car does something unusual, and it is not robust to the
cutoff it requires. Japan 2024: after the red flag the whole field records lap 2 at about
1,711s (17.5x the session median) because they were parked, while **car 22 alone records
203.186s (2.08x)**. Moving the cutoff from 2.0x to 2.5x admits that one lap and drags the
inferred restart **25 minutes** earlier. Requiring several cars to agree did not rescue
it either:

| Rule | Cutoffs tested | Worst-case disagreement |
|---|---|---|
| First car past cutoff | 1.5x to 10x | 1,530s |
| Requiring 2 cars | 1.5x to 10x | 2,824s |
| Requiring 3 cars | 1.5x to 10x | 2,648s |
| Requiring 5 cars | 1.5x to 10x | 2,228s |

A constant that moves the answer by 25 minutes is a decision in disguise, not a
parameter.

*Adopted: a quantity that needs no cutoff.* A car stopped by a red flag is still on a
lap, and that lap does not end until the race restarts and the car completes it. Its end
time is therefore an observation of the restart.

```
restart = median over cars of (first lap started after the stoppage + its duration)
          minus one session-median lap
```

The median across cars is what makes it robust: no single car can move it and no
threshold decides who is included. Recorded as `closed_by = 'restart_inferred'`, used 19
times. Its two remaining free choices were tested and neither decides the answer:

| Choice | Range tested | Max effect |
|---|---|---|
| `MIN_CARS_FOR_RESTART` | 2, 3, 5, 8 | **0.0s** (every stoppage has 10 to 20 cars, so it never binds) |
| "minus one lap" | 0 to 2.0x median | **113.6s** |

Stable to within about one lap, which is the resolution the flag needs, against 1,530s
or worse for the rejected rule.

**(c) A third spelling of "red flag" was never detected.** From 2026, OpenF1 also logs
suspensions as `category='Other'`, no `flag` column, message `RED FLAG - RACE SUSPENDED`.
There are 21, and one is the **Monaco 2026 race**, whose stoppage was invisible: 17 laps
of roughly 2,260 seconds sat in the data flagged as green.

Matched on the message START, not a substring: 27 messages contain `RED FLAG
INFRINGEMENT`, a stewards' note about a driver, and matching those would invent periods.
Same shape as the VSC problem in item A: an event hiding under a category that looks
unrelated.

**Evidence the fix is an improvement rather than a change**

| | Before | After |
|---|---|---|
| Laps flagged `red_flag` | 3,540 at **1.01x** session median | 122 at **1.93x** |
| Flagged laps not actually slowed (<1.05x) | 3,123 (**37.6%**) | ~180 (under 4%) |
| Clean race laps, standard deviation | 24.707s | **11.933s** |
| Periods with impossible duration | 18 | 0 |
| Longest period | 17,956s | 5,910s |

Flags now behave as their names claim: green 1.00x the session median, VSC 1.23x,
Safety Car 1.45x, red 1.93x. Those match the figures recorded independently in open
question A.

**Verified before publishing:** none of the 29 diagnostic tests changed its verdict.
Eleven moved numerically, mostly larger samples, and none crossed 0.05.

A flag carried by 3,540 laps running at green-flag pace was not measuring anything. The
standard deviation halving is contamination leaving the clean-lap population; the mean
barely moved, which is what removing outliers looks like rather than shifting a
distribution.

`neutralised` went from 17,076 laps to 11,484.

**Still open.** 410 unflagged race laps run over 1.30x the session median. Some are wet
weather, traffic or damage rather than missed cautions, and they have not been separated.
One green lap remains over 3x: Australia 2026 lap 33, a single car at 1,168s. That one is
correct to leave unflagged, because one car stopping is not a session-wide caution. It is
what a per-lap validity flag is for, not what a caution flag is for.

**Consequence:** any analysis that filtered on `neutralised` before this date was built
on a contaminated population and needs re-running.

### 48. A race that starts behind the safety car is never flagged

*2026-08-11. Found while auditing the "valid lap" rule, not while looking for it.*

The fourth caution bug, and the third instance of one recurring failure: the event is
announced in prose under `category='Other'` instead of as a flag, so the parser never
sees it.

Every other caution opens on `SAFETY CAR DEPLOYED`. When the race *starts* behind the
safety car, the car is already on track before the session begins and that message is
never sent. No period is opened, so the laps are recorded as green-flag racing.

Spa 2025 ran its first four laps at 1.57 to 1.86x its own green pace with all 80 of them
green. Race control said `RACE WILL START BEHIND THE SAFETY CAR` at 14:15:01 and
`ROLLING START` at 14:29:35; the red-flag period was closed at 14:20:00 and lap 1 begins
65 milliseconds later.

**How the end of the period was chosen.** The start is unambiguous. The end is not, so
three candidate rules were scored against the four sessions carrying the announcement,
using the per-car lap tables as ground truth:

| Rule | Fires on | Correct |
|---|---|---|
| (a) first `ROLLING`/`STANDING START` message after the session starts | 2 of 4 | Spa exact, Miami 2 laps of 3 |
| (b) the restart_finder statistic, seeded at the session start | 4 of 4 | 1 of 4 |
| (c) end of the last field-wide slow lap | n/a | needs a slowness threshold |

(b) invented periods for the two sessions that need none. (c) would have reintroduced
exactly the kind of free constant that #47 removed. (a) was adopted: it is silent unless
race control logged both ends, and where it fires it never over-flags.

Result: 2 periods, 118 laps. `neutralised` 11,679 to 11,797. **0 of 29 diagnostic
verdicts flipped**; 8 moved numerically, the largest being T11b p 0.0069 to 0.0104, still
significant.

**Known residual, deliberately left.** Miami 2025 sprint lap 3 and Suzuka 2024 race lap 3
are both a safety car coming in mid-lap while the closing message predates it. Under-
flagging two lap-events was preferred to rule (b)'s two invented periods.

**Zandvoort 2023 is not a fifth bug.** Its laps 1-3 look identical on the median but the
per-car spread gives it away: 86.8 to 119.0s, bimodal as cars pit for wet tyres. Under a
safety car the field is bunched; in the wet it disperses. Correctly unflagged.

**The general defence.** A missed caution has one signature that does not depend on
knowing the vocabulary: the entire field slows at once. `s03_verify` check [21] now
measures that directly, in medians rather than means, and reports 29 lap-events across
11 sessions as a WARN. It warns rather than fails because rain produces the same median.

### 49. The 60-300 second "valid lap" window was a patch over #48

*2026-08-11. The first conformed column in the gold layer.*

`GOLD_INVENTORY.md` found "which laps count" answered five ways across 14 sites, with the
documented `BETWEEN 60 AND 300` used at none of them. The obvious move was to adopt the
documented rule. Measuring it first showed that would have repeated the RESTART_FACTOR
mistake.

**The floor is inert.** Zero laps of 239,102 are under 60 seconds. The fastest lap in the
dataset is 63.971s at Spielberg, so the floor sits below the physical limit of the sport
and can never fire. Every rule's floor is decoration.

**The ceiling was redundant.** On race laps already not neutralised and not pit-out, the
`60-200` window removed 11 further laps of 81,769. **Ten of the eleven were lap 1 of the
2025 Belgian Grand Prix**, which is not an outlier population, it is #48. After fixing
that detection the window removes **exactly one lap in 81,689**.

So the window was never a validity rule. It was a patch over a caution-detection bug, and
it was covering roughly a tenth of it.

**What replaced it**, in `pipeline/s07_build_gold.py`:

| Column | Definition | Rows |
|---|---|---|
| `is_valid_lap` | `lap_duration IS NOT NULL`, the lap completed and was timed | 229,873 (96.1%) |
| `is_representative_lap` | valid, not neutralised, not pit-out | 195,898 (81.9%) |
| `pace_ratio` | `lap_duration / session green median` | all |

`is_representative_lap` is **row-for-row identical** to the filter `s05_diagnostic`
writes by hand, so migrating a consumer to it moves no published number.

`pace_ratio` is the flag-not-filter principle applied to the one case where filtering was
still doing something: Melbourne 2026 car 18 lap 33 now reads 13.8x instead of being
silently deleted. Adopting gold therefore moves exactly one team-year mean, Aston Martin
2026, by 1.2568s. That is a visible consequence of keeping the lap, not a regression.

**Rejected:** sector-sum consistency as a third validity term. Only 50 laps of 212,033
disagree with their own sectors by over a second, and the tolerance would be one more
free parameter to defend for no measurable gain.

### 50. The gold layer, and the four things measuring changed

*2026-08-11. 17 tables, 739,055 rows, 149.4 MB, in `pipeline/s07_build_gold.py`.*

Three dimensions, nine facts, five aggregates. The layer obeys three rules: it flags
rather than filters, it holds no constant that decides an answer, and it does not hold
the training matrix.

**A driver number is not a driver, and I built that table wrong first.** `gold_driver`
was keyed on `driver_number` with the most recent name winning. **34 of 57 numbers
belong to more than one person**: number 1 is Verstappen 2023-2025, Paul Aron for one
2023 session and Norris in 2026; number 3 is Ricciardo, then O'Sullivan, then
Verstappen. Joining that table would have relabelled every Verstappen lap as Norris,
and nothing would have reported it. Re-keyed on `(driver_number, full_name)`, 97 rows,
with `shares_number` flagging the ambiguous ones. `(driver_number, year)`, which
`dim_driver` uses, is also not unique: number 1 in 2023 is two people.

**`lane_duration` is `pit_duration` twice.** Byte-identical across all 22,898 populated
rows, maximum absolute difference 0.0. Only one is carried.

**The pit duration outliers are red flags, not errors.** The inventory listed "up to
16,921s" as something gold should filter. 96.4% of race stops over 60 seconds happened
under a caution, 92% under a red flag, and the extreme tail is Zandvoort 2023 laps 63-64
with the field parked in the pit lane. Scope the session type, attach the lap's own
caution flag, and green race stops sit at a 23.3s median with a 41.2s 99th percentile.
No fence is baked in anywhere. The Tukey fence at `pit_stops_05.sql` was re-derived and
is population dependent (36.76s over all race stops, 29.65s over green ones), so it is a
local choice and stays at its call site.

**`STOP_DURATION_MIN_YEAR = 2024` overstates what exists.** It reads as "usable from
2024". Actual race coverage is 0%, 18.1%, 85.5%, 33.8% by year. Comparing 2024 with 2025
on that column compares an 18% sample with an 85% one. Gold carries
`has_stop_duration` rather than a year cutoff.

**One measurement I got wrong and caught.** A first pass reported 13,959 of 34,567
stints overlapping the previous one. Flagging 40% of a table as broken is almost always
the test being wrong: a stint ends on the lap the car pits and the next begins on that
same lap, so `lap_start <= prev_end` matches the convention. Only `<` is a real overlap.
**42, not 13,959**, plus 56 coverage gaps, all in practice and testing.

**Verification.** 34 checks, all passing: row parity against every silver source, primary
key uniqueness on all 11 keyed tables, the conformed flags reproducing the hand-rolled
filters exactly, and aggregates summing back to their facts. Two differences against the
published serving layer were chased to their cause rather than accepted:

- `fact_lap` has 55 fewer race laps because `s04_descriptive.py:498` drops laps with a
  null `date_start`, which `merge_asof` needs a key for. Gold keeps them flagged.
- `dim_race` has 15 fewer races because it scopes to completed races with laps and
  results. Gold keeps the whole calendar with `has_laps` and `is_cancelled`.

**Speed**, on five questions the dashboard and notebooks actually ask: 1,676ms to 291ms
in total, a 5.8x improvement. Caution share by circuit went 527ms to 1ms because it
became a table read. Grid versus finish improved only 2.6x, so the aggregates earn their
place on the lap-heavy questions rather than uniformly.

**Not carried raw:** `silver_team_radio` (audio URLs with no transcription, per open
question D). `silver_intervals` and `silver_position` enter at the two grains anything
asks for rather than as 2.4M raw rows: one reading per lap on `gold_lap`, and a
close-running share in `gold_agg_interval`.

### 51. Migrating the consumers onto gold, and what that surfaced

*2026-08-12.*

**`s05_diagnostic` first, because its filter was already proven identical**, so any
movement would be a bug caught cheaply rather than a judgement call. Result: **nothing
moved**. 29 verdicts, p-values, statistics and n; 42 coefficients across 7 numeric
columns; 189 group statistics across 4. All identical to 1e-9.

`RACE_SCOPE`'s four-part predicate with two EXISTS subqueries became
`WHERE is_analysable = 1`, verified to select the identical 81 races. `normalize_teams`
became a no-op. `lane_duration` became `pit_duration`, safe only because they were
byte-identical.

**A correction.** I had said `is_representative_lap` was "row-for-row identical to the
filter s05 writes by hand". That was true of its SQL clause, which is what I compared,
but `load_clean_laps` then applies `LAP_OUTLIER_FACTOR = 2.0` and a null-team drop in
pandas. The real population is 81,677, not 81,689. A gap of 12 laps, but I overstated it.

**`LAP_OUTLIER_FACTOR` is a sixth valid-lap rule resting on a claim the caution fix
invalidated.** Its comment says it removes "red-flag queues"; it runs after
`neutralised = 0`, so red flags are already gone. What it removes now is 12 laps, and
**six are Monaco 2026 lap 70, where sixteen cars ran at 2.02x with nothing flagged**.
So it is partly compensating for a missed caution, exactly as the 60-200s window was
(#49). Left at 2.0 deliberately: fixing that caution and moving this threshold in one
step would make attribution impossible. Sensitivity: 1.5x drops 118, 1.8x drops 28,
2.0x drops 12, 2.5x drops 1.

**Lap-start state moved into gold**, which removed the last non-telemetry reason to
reach past it. `position`, both gaps and both lap-gap counts are now columns, verified
**100.0000% identical to the published fact_lap on all 90,053 race laps** with matching
null patterns.

That needed a decision nobody had noticed needed making. `s04` read these with
`direction="backward"`, `s05` with `"nearest"`. Backward is the state as the lap began;
nearest can return a sample from AFTER it started, which for a predictive feature is
lookahead. Backward chosen. The two agree on 98.7% of laps; 328 of 88,652 fall on the
other side of the 2.0s threshold one test filters on.

**A real bug in published data: a missing teammate was being treated as a zero.**

`fact_driver_race` built its teammate deltas as `own - (groupby_sum - own)`, and pandas
`transform("sum")` skips NaN. A teammate who did not finish therefore contributed 0
rather than nothing, and the delta came out equal to the driver's OWN finishing
position, on **162 of 162 rows** where the teammate retired. Sainz finished 4th in
Bahrain 2023 and it read as beating Leclerc by four places. Leclerc retired.
`story_driver.py:73` renders that as an "Against teammate" metric.

Also 46 pace deltas and 3 grid deltas. `teammate_points_delta` was unaffected, because a
DNF genuinely scores zero, so treating it as zero happened to be right there. Fixed in
both `s04` and gold by requiring both cars to have recorded the value; the call sites
already guard on `pd.notna`, so the metric stops rendering rather than breaking.

**Four bundle tables nothing reads.** `perfect_lap`, `perfect_race`,
`perfect_lap_record`, `perfect_lap_model`: 3,070 rows, 754 KB, checked against every way
a name can reach a query (FROM, JOIN, subquery, f-string, quoted, bare). The
perfect-lap page was built on `fact_lap` plus `map_measured_xy` instead. Dropped from
the bundle, still computed, because the planned application wants that feature.

**Six of my own errors, caught by comparing rather than assuming:**

| Error | How it showed |
|---|---|
| `n_stints` filled with 0 | zero stints is impossible for a car that started; 28 rows asserted it |
| `n_stints` inherited through a lap-grained table | silently dropped 3 drivers with a stint but no laps |
| `lap_vs_median` off the single-pass median | differed on 23,046 of 90,053 laps while looking like the same column |
| `gold_driver` keyed on `driver_number` | 34 of 57 numbers are shared; every Verstappen lap resolved to Norris |
| coefficient comparison joined on `(test_id, model)` | fanned 42 rows to 212 and reported 874 phantom changes |
| group comparison keyed on `generated_at` | a fresh timestamp every run, so it matched nothing |

The last two mean my first verification run reported movement that did not exist. Worth
recording: a verification that is wrong in the alarming direction still wastes the same
amount of trust.

**The CSVs.** `s04` now writes its seven tables straight into `dashboard.db` instead of
to CSV, which `s06` then read back. That copy existed twice with nothing checking the
halves agreed. 7 CSVs and 25.2 MB deleted; 18 remain, all genuine analysis or model
output. Bundle 25 tables to 21, gz 12.8 MB to 12.3 MB.

**`s07_build_gold` is wired into `run_pipeline` before `s04` and `s05`, and stops the
run if it fails.** Without that, a week that rebuilt silver and skipped gold would
analyse the previous week's data and report success, which is the same shape as
NOTES_LOG #43 and #44.

**Verified end to end:** gate PASS with 0 FAIL, 32 of 32 dashboard queries resolve
against the rebuilt bundle, bundle matches its table list exactly, and the 29 verdicts
are unchanged after a full pipeline run.


### 52. A restart is a lap, not a timestamp
*2026-08-12*

Check [21] left 14 green laps whose field median sits at or above 1.30x the session green
median. Splitting them by field spread and by whether a caution is already flagged on the
neighbouring lap gives a clean 7 and 7:

| | laps | |
|---|---|---|
| adjacent to a flagged period | **7** | Zandvoort 66, Mexico City 36, Suzuka 3, Jeddah 20, Montreal 29 and 58, Imola 53 |
| no adjacent flag, wet race | 7 | Zandvoort 2 and 3, Monte Carlo 54 to 59 |

Seven out of seven of the unexplained ones touch an existing period. **The remaining
error is in period boundaries, not in events the pipeline missed entirely.** They fall
into two families.

**Family A, the formation lap after a red flag. FIXED.** The RED period is closed at the
inferred restart, but the formation lap runs after it:

| race | period ends | next lap | that lap | lap after |
|---|---|---|---|---|
| Zandvoort 2023 | 15:13:07 | 66 at 15:16:39 | 130.6s | 88.7s |
| Mexico City 2023 | 21:14:15 | 36 at 21:15:10 | 138.2s | 84.1s |
| Suzuka 2024 | 05:33:12 | 3 at 05:34:24 | 153.0s | 99.0s |

Each logs a STANDING or ROLLING START PROCEDURE message, and in Mexico and Suzuka that
message fires **before** the period end, so the #51 Monaco rule cannot reach them. Monaco
was one instance of a general bug: after a red flag there is always a formation lap and
it is never racing.

The rule is **per car and by time**: the first lap a car starts after the period ends.
Not by lap number, because at Zandvoort the formation lap is a different lap number for
different cars. Cars parked through the stoppage were mid-lap-65 and resume on 66; cars
that started lap 65 after the restart have 65 as their formation lap, which is why lap
65's start times span 42 minutes. Any rule keyed on one shared lap number is wrong for
one of the two groups. Scoped to races, since only a race restarts this way, and to
periods closed by `restart_inferred`, `start_procedure` or `green_flag`, so a race that
ended under red flag is untouched.

**A second, separate defect in the same rule.** Zandvoort would not clear from the
formation-lap rule alone, because its useful procedure message,
`SAFETY CAR WILL ENTER PITS: ROLLING START PROCEDURE` at 15:17:24, sits *after* the
inferred restart while an earlier procedure message sits *before* it, and `fallback_end`
tested only the first message after the stoppage. So it saw the early one, concluded there
was nothing to extend to, and stopped.

Widening it to "the earliest message still after the inferred restart" was tried, and
**failed catastrophically on the first attempt**: `before` was unbounded on that call path,
so in a multi-red-flag race the first stoppage reached the second restart's message.
Melbourne 2023 extended its first period across laps 10 to 26 and **697 racing laps were
flagged as neutralised**. Reverted within the same run.

It was then reinstated correctly by adding the two bounds it had been missing, neither of
them a tunable number: `before` is the next red flag in the session, and `limit` is the
effective session end. **The ordering matters and is recorded in the code**: bound first,
widen second. Widening first is what produced the 697 laps.

**Result of both together:** 163 laps newly flagged, `neutralised` 11,830 to 11,995.
161 of the 163 are clearly slow (median 1.18x, max 2.27x). **0 laps lost a flag.** All
three targets cleared: Zandvoort 66, Mexico City 36, Suzuka 3. Gate check [21] fell from
28 unexplained lap-events to 24.

**One residual, left deliberately.** Monaco 2026 lap 71 is flagged for 2 cars at 1.02x
and 1.03x. For them the period end fell
mid-lap-70, so lap 70 was already caught by overlap and lap 71 is one lap too far.
Distinguishing "the lap spanning the period end is the stoppage lap" from "it is the
formation lap" needs a duration threshold, which is the mistake `RESTART_FACTOR` already
made once. Two laps just after a standing start are arguably not representative racing
anyway, so this is left alone deliberately.

**Verdict impact:** 0 of 29 test verdicts flipped. One coefficient crossed 0.05 and changed
sign: T18's `tyre_age`, from -0.0001 (p=0.93) to **+0.0061**, with R² up from 11.2% to
12.4%. That is the fix working rather than a problem: contaminated restart laps were
dragging the term to zero. It is still an order of magnitude below T11a's within-stint
+0.058 to +0.079 s/lap, which is the point the caveat already makes. The published
narrative switched branches on its own, because the wording had been written for both
outcomes rather than for the result of the day.

**A gate check for the other direction, added the same day.** Check [22] fails the run when
a lap flagged as neutralised ran at or faster than its session's green median. Every
caution fix up to this point had been validated by ad-hoc scripts that no longer exist, and
the 697-lap Melbourne regression above was caught by one of those while the gate reported
PASS. Over-flagging is the more dangerous direction: a missed caution leaves a conspicuously
slow lap that check [21] finds, whereas a wrongly flagged lap disappears from every analysis
and nothing downstream can distinguish it from a real one. It is a FAIL rather than a WARN
because, unlike a field-wide slowdown, a neutralised lap at full racing pace has no innocent
explanation. Verified to have teeth rather than to pass blindly: 32 laps currently qualify
individually, the largest lap-event holds 3 cars, and the check fires at 5.

**`LAP_OUTLIER_FACTOR` consolidated into config**, having been declared four times, in
`s04`, `s05`, `s05b` and `s05d`, with nothing checking the copies agreed. Same duplication
as `EXCLUDED_TEAMS` in #51. Number-neutral. The constant itself is now flagged as under
review in config: its stated rationale no longer holds, and it catches 0 of the 35 laps left
green by family B.

**Family B, the safety car withdrawal lap. MEASURED, ATTEMPTED, REVERTED.**
`SAFETY CAR IN THIS LAP` is treated as the moment the period ends. It announces that the
car withdraws at the **end** of the current lap, so the rest of that lap is still
neutralised. It shows as partial flagging: Jeddah 8 of 19 cars, Montreal 2 of 15, Imola
12 of 18.

Cost, measured before attempting anything: **35 laps across 12 races.** None are removed
by `LAP_OUTLIER_FACTOR`, whose cut is 2.0x and whose ratios here top out at 1.50. The
affected cars are **not a random sample**: when the message fires the leaders have already
crossed the line, so the cars still on the lap are the ones at the back. It lands on Zhou,
Ocon, Bottas, Lawson, Stroll and Bortoleto, which means the error **makes slow cars look
slower**, the direction least likely to be questioned. Largest effect on any driver's race
median is 0.313s (Bottas, Suzuka 2026); typical is under 0.05s.

**It is only that small because every consumer uses medians. If anything switches to a
mean, this gets materially worse.**

**Why the fix was reverted.** Two leader definitions were tried and both failed the
acceptance tests:

- the car in P1 in the position feed at the message time. The leader is frequently
  **pitting** when the safety car is called in, and a pit lap ends long after the restart,
  so the period stretched past the green
- among laps in progress, the highest lap number and then the earliest end, which is the
  first car on the leading lap to cross the line

The second produced **byte-identical output** to the first, which is what gave the real
answer. At Montreal 2024, **lap 58 start times spread across 154 seconds for 15 cars**.
The field is not bunched. If cars begin the same lap 154 seconds apart then no single
period-end timestamp is correct for all of them, and no better leader definition helps.

Both attempts flagged 34 laps at racing pace in order to catch 35 behind the safety car,
including car 1's lap 59 at Montreal at 78.4s against an 87.4s baseline. **The fix was
worse than the bug**, and the wrong flags would have been indistinguishable from right
ones afterwards. Reverted; all 239,102 lap flags and 445 periods restored exactly.

Fixing B properly means making `build_lap_flags` operate on lap numbers per car rather
than on time windows. That is a rewrite, not a rule change. Do not retry it as one.

**Family C, a race that ENDS under caution. FIXED.** Found by finally comparing the gate's
own [21] list against the ones I had triaged, and discovering **14 of 24 had never been
looked at**, because every triage script I wrote filtered to races at a tighter threshold
than the check uses. The loudest unexplained slowdown in the whole dataset was in that gap.

Montreal 2025: safety car deployed on lap 67, `CHEQUERED FLAG` at 19:34:56, no green flag
and no closing message in between, because racing never resumed. The period was closed
after 123s instead of running the 444s to the finish, leaving laps 68, 69 and 70 at 1.39x,
1.58x and 1.60x green pace recorded as racing. `ALL CARS TO FOLLOW THE SAFETY CAR THROUGH
THE PIT LANE` confirms it.

The cause is a priority inversion in `fallback_end`: it preferred an inferred restart over
the session end whenever `restart_finder` returned anything, and **`restart_finder` cannot
tell "racing resumed" from "cars kept circulating behind the safety car"**, because both
produce completed laps. So it invented a restart that never happened.

The test is structural, not a threshold: no green track flag and no safety car closing
message after the deployment means racing never resumed. **RED is excluded deliberately**,
because a suspended race that resumes often logs neither signal, which is the whole reason
`restart_finder` exists (#47, Monaco 2024). Applying this to RED would reintroduce that bug.

Closed at the **chequered flag**, not at `effective_session_end`. The latter is the last
evidence of any activity and runs long past the finish as stewards' decisions arrive:
Melbourne 2024's VSC would have been extended by 2,209s rather than 26s, a 38-minute VSC.
No lap is misflagged either way, since no lap exists after the finish, but
`duration_seconds` is published.

**Result:** 7 periods extended, all SC or VSC, none shortened, no RED touched. 3 races
(Montreal 2025, Melbourne 2024, Baku 2024), 3 practices and 1 sprint. 42 race laps newly
flagged, **0 at racing pace** (min ratio 1.13), **0 laps lost a flag**. Longest extension
679s, down from 2,209s before the chequered bound. 0 of 29 verdicts flipped, 0 coefficients
crossed 0.05, 0 sign changes.

**Check [21] narrowed to races.** It had covered Sprints since it was written, but nothing
in this project analyses a Sprint, so those findings could not move a published number and
sat untriaged for weeks while looking like work. Two are genuine and still in the data
(Austin 2025 laps 18-19 at 1.50x, Miami 2025 lap 3 at 1.44x); the docstring records them
and says to widen the scope back when that phase starts.

**The lesson worth keeping:** a check whose scope is wider than any triage script will
accumulate findings nobody reads, and a gate people learn to skim is worse than no gate.
Scope the check to the scope of the work.


### 53. One folder called dashboard, and what running it twice found
*2026-08-14*

The project had two folders named `dashboard`: the app's hand-written source, and a
build output under `outputs/`. Same name, opposite lifecycles. The bundle now lives at
`dashboard/data/dashboard.db`, beside the app it serves, and `outputs/` no longer exists.

**The path was declared six times.** `OUTPUTS_DIR / "dashboard"` appeared in `s04`, `s05`,
`s05b`, `s05c`, `s05d` and `s06`, so moving it meant six edits and hoping none was missed.
That is the same shape as `EXCLUDED_TEAMS` (declared twice) and `LAP_OUTLIER_FACTOR`
(declared four times). Now one definition, in `pipeline/serving.py`.

**Every dataset was written to disk twice.** `s05`, `s05b`, `s05c` and `s05d` wrote CSVs;
`s06` opened those CSVs and copied them into `dashboard.db`. The whole project contained
exactly one `read_csv`, and it was that line. 18 files regenerated each run to be read
once by the step that removed the need for them. All five steps now write straight into
the bundle.

**The perfect_\* tables are no longer produced.** #51 dropped them from the bundle but kept
computing them, on the grounds that a planned choose-a-lap feature wanted them. That
feature exists: `views/perfect.py` was built on `fact_lap`, the map geometry and the
`lap_factor_*` tables. The reader they were waiting for arrived and used something else.
The four builders stay, reachable with `--tables perfect_lap`.

**The coverage snapshot moved to `pipeline/`, not into the bundle folder.** It is the
gate's memory and it has to stay in git; `dashboard/data/` is ignored wholesale by the
`data/` rule, so putting it there would have dropped it from version control silently and
the check would have gone quiet without failing. `config.py` also had to stop calling
`mkdir` on `outputs/` on import, or the folder would have reappeared empty on every run.

**Running the pipeline twice is what earned this entry.** One run reproducing the previous
bundle proves nothing about determinism; it only proves the paths work. Two runs found
that **`s05c_racemap` does not reproduce itself**, which is recorded as open question G.

**Also found:** `run_pipeline` never called `s05b`, `s05c` or `s05d`, so five bundled
tables had been shipping data fitted five to six days stale. Not yet fixed.


### 54. The fourth headline gets its name back

*2026-08-19. Naming only. No model, filter, coefficient, chart or number changed, and
nothing was rebuilt or republished.*

**The page was called the wrong thing.** "Find perfect lap" described an answer it never
gives. It does not find a perfect lap; it takes a real one, decomposes it, and asks what
moving each factor would have been worth. That is a prescription, the fourth of the four
analytics headlines this dashboard is organised around. Three of the four were already
named after the question they answer, Analyse, Diagnose and Predict, and the fourth was
named after a superlative.

**What moved.** `dashboard/views/perfect.py` to `views/prescribe.py`, and
`pipeline/s05b_perfect.py` to `s05b_prescriptive.py`, both with `git mv` so
`git log --follow` still reaches the history. Sidebar entry "Find perfect lap" to
"Prescribe", page heading to "Prescribe a lap", and the landing page went from three
buttons to four so every headline has a front door. Ten widget keys renamed inside the
page for internal consistency; they are session-local and invisible.

**What deliberately did not move: the four `perfect_*` tables.** `perfect_lap`,
`perfect_race`, `perfect_lap_record` and `perfect_lap_model` rank the fastest lap and the
best race ever recorded. A superlative is a descriptive question. Nothing in them
prescribes anything, and the Prescribe page has never read one of them. Renaming them to
match their file would have put a wrong label on a right thing, which is worse than the
mismatch it fixed. Checked before deciding: the shipped bundle holds 21 tables and none of
them is `perfect_*`, so this was a free choice rather than a costly one.

**The rename found a stale comment, which is the part worth recording.** The orientation
block in `race_map.py` still opened "THERE IS NO NORTH HERE, AND THAT IS NOT AN
OVERSIGHT", fifteen lines above the code that draws N, E, S and W. It was true when
written and was never revisited when `fetch_circuit_north.py` made north available. A
comment that contradicts the code beneath it is worse than no comment, because it is
read as current. Rewritten to carry both the old conclusion and why it stopped holding.

**Also corrected:** the README listed the prescriptive phase as "Out of scope" while the
page was live, and `s07_build_gold.py` described its consumer as "the perfect-lap tool".


### 55. New data reaching the dashboard, in two halves

*2026-08-21. Pre-ship work. Two defects that combined to mean a new race could not
actually arrive on the live site without hand-holding at both ends.*

**Half one: the analysis was never refitted.** `run_pipeline` built three serving layers,
gold, descriptive and diagnostic, and stopped. `s05b`, `s05c` and `s05d` produce **ten of
the twenty-one bundled tables** and nothing ran them, so every publish shipped new laps
priced by coefficients fitted whenever those three were last run by hand. This is the
worst shape a staleness bug can take, because the rows are current and nothing on screen
looks wrong. Entry #53 recorded it as found and not yet fixed; this fixes it.

They read silver and bronze rather than gold, so they had no ordering requirement, and
`serving.write_table` replaces one named table at a time so no step can clobber another.
Verified by running all ten steps end to end: every step exit 0, all 21 tables present and
rewritten, checked against `s06_publish.DB_TABLES` rather than a hand-typed list.

**The cost is real and worth stating.** The three new steps add **6m 18s**, of which
`s05c` is 295.7s and almost all of that is one 4,545,724-row scan of bronze `location`.
The twice-weekly job goes from about 8 minutes to about 11. The docstring's claim that the
serving layers are "cheap" was true of the three that were there and is not true now, so
it was corrected rather than left to mislead.

**Half two: the app could not see a new publish.** `app_common.get_connection` cached the
downloaded bundle under a FIXED name and reused it whenever the file existed. Two
consequences, and the second is the bad one:

  publishing changed nothing until somebody clicked Reboot, and
  a download that arrived truncated satisfied "does it exist" and stayed broken
  for exactly as long.

Now stamped from the Release's own ETag, checked every `BUNDLE_TTL` (900s). The stamp goes
in the filename, so a new publish lands in a NEW file and sessions reading the old one are
never pulled out from under. Confirmed the header exists rather than assuming it:
`ETag: "0x8DEFD4063FC01E8"`, content-derived, so a republish of identical size is still
detected.

**`query` got the same TTL, and that is not incidental.** Refreshing the connection alone
would have moved the app to new data while every chart went on showing values cached from
the old bundle: the same bug wearing a different hat, and harder to see.

**Truncation needs no checksum.** gzip carries a CRC and an uncompressed length in its
trailer, so a cut-short stream raises on decompression. Downloads go to a `.part` file and
are renamed only after they open and hold the tables the app needs, so a partial file can
never be mistaken for a finished one.

**A failed refresh no longer takes the app down.** If the network fails and an earlier
bundle is on disk, it is served with a visible warning. Serving slightly old data beats an
error page on an app that worked a minute ago.

Sixteen checks, all passing, including a real 67 MB download from the live Release, a
deliberately truncated stream, a valid gzip of the wrong database, a 404, a connection
error, and pruning. Also confirmed a local run makes **zero** network calls: `LOCAL_DB`
short-circuits before any request, so development never depends on the internet.

**Still needed for a new race to appear unattended:** `--publish` in `run_weekly.bat` and
`GITHUB_TOKEN` as a persistent variable. Deliberately not done yet; publishing replaces
the data behind a public URL and should not be switched to automatic in the same change
that rewrote how it is fetched.


### 56. Sentences that stopped being true

*2026-08-21. Pre-ship work, continuing #55. Rows and models were made to follow the data
in #55; this is the prose that did not.*

**The rule applied, because these are not all the same kind of number.** Anything
describing current coverage or scale is computed. Anything that is a fitted coefficient is
read from the table it already lives in. Anything that is a one-off validation keeps its
value and gains a date, because recomputing a study on every page load would be both
wasteful and a misdescription of what it is.

**`coverage_gaps()` replaces five hand-written sentences** across `story_race`,
`story_driver` and `story_team`: "6 of 22 races in 2023" for pit stops, twice; "2,744
messages across 2023, but 217 across 2026" for radio, twice; "8 of 11 races in 2026" for
championship standings. Each was true when typed. The 2023 figures still are, that season
being closed. The 2026 ones went stale on the next race weekend.

Checked against raw counts before being trusted: `pit_stop` returns 6 of 22 in 2023,
matching the sentence it replaced exactly, which is what proves the query and not just
that it runs.

**The radio sentence changed meaning, deliberately.** It quoted message volume while
appearing under the heading "No team radio recorded for this race", so it answered a
question nobody had asked. It now reports coverage, which is what a reader who just hit an
empty panel actually wants.

**The `76%` was wrong by six points.** The same quantity is computed at the top of the
same page and displayed there, so one page stated two different numbers for one thing and
the wrong one was the one a reader met last. Computed, it is **70%**.

**"No race except one in 2026 has recorded car positions" was the sharpest example.** The
rebuild in #55 put the count at **six**: 2023 Bahrain, Saudi Arabia, Spain and Belgium,
plus 2026 Monaco and Hungary. The page had been asserting a scarcity the data no longer
supported, and would have gone on asserting it. Now counted from `map_coverage`.

The accuracy study beside it kept its 24 metres and 0.96 correlation and gained "Measured
in August 2026", with a closing line saying plainly that those are one study on one day
rather than live figures.

**Found while checking encodings, unrelated to any of the above.** `story_race.py:616`
read `Air {..}Â°C, track {..}Â°C`, a UTF-8 degree sign decoded once as latin-1. Present in
`HEAD`, so it has been shipping visible garbage on the race page for some time. Fixed, and
the whole dashboard swept for `Â`, `Ã` and the replacement character: clean.


### 57. What breaks when the data contains something new

*2026-08-21. Pre-ship work, completing the responsiveness thread. #55 made the analysis
follow the data and #56 made the prose follow it; this asks what happens when the data
contains a shape the code has never met.*

**The audit.** 68 `.iloc[0]` sites and 33 `min`/`max`/`idxmax` calls across the dashboard,
narrowed by a script that looks for an emptiness guard naming the same variable. The first
pass flagged 47 and was wrong about most of them: the common shape here is an inline
ternary, `x.iloc[0] if len(x) else default`, which a backwards-only window cannot see.
Widening it to include the line itself cut the list to 26, and reading those left **two**.

That ratio is the real result. This codebase already guards nearly everything, and both
survivors are cases where the guard was somewhere the pattern could not reach.

**One: an inner join that loses races silently.** `views/prescribe.py` merged `dim_race`
against `map_coverage` on the pandas default, `how="inner"`. `dim_race` comes from s04 and
`map_coverage` from s05c, built by different steps from different sources, with nothing
requiring them to agree. A race the first knows about and the second does not simply
**vanishes from the picker**: no warning, no gap, a season quietly one race short.

Now a left join. The subtle half is the fill: `bool(nan)` is True, so `not race.has_outline`
is FALSE for a missing value, and an unfilled column would have sent exactly the races with
no coverage into the map-drawing branch they were meant to skip. Filled to a real 0, they
land in the "no track map for this circuit" path the page already had.

Verified by inserting a race into a COPY of the bundle and running both joins: inner
returns 81 rows without it, left returns 82 with `has_outline == 0` and a usable note.

**Two: `int()` of a NaN on the first line of a page.** `views/diagnose.py` opened with
`int(tests.n.max())` in its caption. An empty `diag_tests`, or an all-null `n`, raises
ValueError, and because it sits above everything the whole page becomes a traceback rather
than losing one number. Confirmed both ways on a copy: the old expression raises, the new
one degrades to "every observation available to it".

**Checked and found already safe**, which is worth recording so it is not re-audited: team
colours are reached only through `.get(name, NEUTRAL)`, never by subscript; the compound
picker tests membership before `index()`; the team selector resets session state when a
team did not enter a race; `story_race` guards grid, intervals, pit stops and radio before
touching them; and `home.py` wraps its counts in try/except. There is exactly one `.merge`
in the dashboard, which is why finding it mattered.

**Not fixed, deliberately:** `analyse.py:43` renders "0 races, nan-nan" if `dim_race` is
empty. That is a broken bundle rather than new data, `app_common` already refuses to open
one missing its core tables, and a page whose every number would be empty is not made
better by tidying its subtitle.


### 58. The data says when it stops

*2026-08-21. Small, and the last of the pre-ship work.*

`render_footer` now carries a line reading **"Data through 26 July 2026, 81 races"**, on
every page, computed from `MAX(race_date)`.

Two reasons, and the second earned it. For a reader, it dates everything above it: a
dashboard that states findings without saying where the data stops invites being read as
current forever, and any sentence #56 missed is now at least read against a date instead
of as a claim about today. For the author, it is the only way to see what the deployed app
is actually serving. Confirming a publish had landed used to mean rebooting and hoping,
because a page showing last month's bundle looks exactly like a page showing this week's.

Returns an empty string rather than raising, and the footer renders without it. This runs
at the bottom of every page including ones already reporting a problem, and a footer is
never worth an error.

Deliberately the latest **race** date rather than a build timestamp: it answers "which
races are in here", which is the question a reader has. It does not distinguish a
republish that only refitted models, and that is a real limit rather than an oversight.


### 59. The reference lap becomes a decision, not a computation

*2026-08-21. Closes the user-visible half of open question G. The cause remains unknown.*

**The measured baseline first, because the old entry was too kind about it.** Six runs of
`s05c` on unchanged inputs: **four distinct geometry hashes**, and on one of them Suzuka
produced **no outline at all**. Not a different good lap, no map. A visitor picking Suzuka
would have been told the circuit has no track map.

**What did not work.** Everything open question G proposed as its next step: an explicit
total order over candidates, sorted `pairs` so the query text cannot vary, a stable sort
with unique tiebreakers, a fixed row order on the fetched positions. All implemented, all
defensible, and the six runs still gave four answers. The one genuine bug found along the
way, an unstable sort that changed which six candidates survived `head()`, is fixed and
was not the cause.

**What worked: stop choosing.** `circuit_outline_source.json` records which session,
driver and lap each circuit's outline is traced from. It is committed, read on every run,
and `--repick` is the only thing that rewrites it.

Which lap best represents a circuit is a **decision**, not a computation. `circuit_north.json`
already worked this way in this same module, and its reasoning transfers exactly: a
constant should not be re-derived on every run, because re-deriving it is a chance to get a
different answer. This is better design than what preceded it, not a workaround bolted on
top of a bug.

**Three safeguards, because pinning alone was not enough:**

A pinned lap that is **no longer a candidate** means the data behind it changed. That is
said loudly and the circuit is re-chosen, because a pin that stops matching is a fact
worth hearing.

A pinned lap that **fails to trace on a given run** falls back to the best available lap
rather than skipping the circuit. This one was caught before the test could find it:
Melbourne's pinned lap is *one of the four known-unstable candidates*, and without the
fallback a bad run would have left Melbourne with no map. That would have been a worse
regression than the instability being fixed. It logs a WARN, because a fallback is the one
remaining way two runs could differ and must not be absorbed silently.

A normal run **can never rewrite the pins**. Otherwise the file would quietly absorb
whatever that run happened to choose, and pinning would mean nothing.

**Verified:** six runs in separate processes, `distinct geometry hashes: 1`, `distinct
source hashes: 1`. 24 of 24 circuits traced from their pinned lap with no fallbacks, 81 of
81 races mapped.

**What this unblocks.** "Run it twice and diff it" works on this step again, which is the
technique the rest of the project is built on, and automatic publishing becomes safe to
turn on. Question G itself stays open: the sample-count flapping is real, unexplained, and
now unable to reach the bundle.


### 60. Two things a page reload got wrong

*2026-08-21. Both reported from use, which is why neither showed up in any check here.*

**Nothing survived a refresh.** Every choice lived in `st.session_state`, and a reload
starts a NEW session, so the theme snapped back to light and every picker went back to its
default. The URL is the one thing a reload carries, so that is where the state now lives.

`url_state.py` does it generally: `restore` seeds session state from the query string
before a widget is built, `remember` writes it back after. Both pages use it for season,
race, story, section, driver, team, lap, sector and view. The theme writes **both** values
rather than only `dark`, because recording only the non-default spells "light" as an
absent parameter, and an absent parameter is also what a first visit looks like. Choosing
light and reloading would then land on light by accident rather than by instruction.

**A restored value is checked against the real options.** A query string is user input: it
can be edited, a link can go stale, and a race that existed last month may not be in the
picker today. Handing a selectbox a value that is not among its options raises and takes
the page down, so a value that does not belong is dropped and the widget falls back to its
default. `None` round-trips as an empty parameter, because "All cars" is a chosen state
rather than an absent one, and an empty value only becomes `None` where `None` is
genuinely an option.

Two theme details took a second pass. `active()` settles the value once per session
including the light default, because leaving it unset makes "never chosen"
indistinguishable from "chose light", and the switch then reruns on every first load
trying to apply a theme that was already correct. And `_sync_chrome` compares against the
LIVE config rather than remembering what this session last wrote, because config is
process wide and another visitor's flip can move it underneath: reading the real value
makes it self correcting, and returning False when it already matches is what keeps a
fresh session from paying for a pointless rerun.

**Both race pickers opened on the wrong race.** `dim_race` is queried `ORDER BY year DESC,
round`, so within a season the rounds run ASCENDING and `options[0]` is round 1. Analyse
and Prescribe therefore both opened on the OLDEST race of the newest season. The season
picker was right, which is what made it look deliberate rather than broken.

`options[-1]` in `analyse.py`, `iloc[-1]` in `prescribe.py`. Checked against dates rather
than assumed: every season's final round is also its latest date, 2026 to Hungary, and
2023 to 2025 each to Abu Dhabi.

**A side effect worth having:** a link now reopens on the race, driver and lap the sender
was looking at, which a screenshot cannot do.

**Not carried:** the counterfactual sliders on Prescribe. They are a scratchpad rather
than a position in the data, and putting a dozen lever values in the address bar would
cost more legibility than it returns.


## Open questions

### A. `caution_flag` under-detects Safety Car periods
*Raised 2026-07-26. RESOLVED 2026-08-11, kept for the trail.*

The original problem: `load_laps()` built `caution_flag` by joining
`silver_race_control` on an exact `(session_key, lap_number)` match, so a Safety Car
spanning several laps flagged only the lap its message carried, and sector-scoped
yellows were counted as race-wide neutralisations.

**Resolved by `s02b_caution_flags`**, which derives range-based periods and writes
independent per-lap flags with sector yellows kept separate. Seven further bugs were
found and fixed in that machinery afterwards (#47, #48, #52); one remains open and
measured, the safety car withdrawal lap in #52. `neutralised` currently covers 12,070
laps. Do not reintroduce a lap-number join.

### B. Virtual Safety Car is not detected at all
*Carried from the diagnostic phase. RESOLVED 2026-08-11, kept for the trail.*

VSC was never missing from the data. It lives inside `category='SafetyCar'` under two
spellings, `VIRTUAL SAFETY CAR ...` and `VSC ...`, so earlier analyses silently counted
a VSC as a full Safety Car despite the two being very different events.

**Resolved**: `classify()` separates them and `vsc_flag` is now a first-class column
covering 3,116 laps. Measured pace confirms they are distinct: green 1.00x, VSC 1.23x,
SC 1.45x, red 1.93x.

### C. Competing pit stops on adjacent laps are unmeasured
*Carried from the diagnostic phase*

The "does a slow pit stop cost track position" analysis reached only R² = 0.025 and
identified undercut/overcut dynamics as the likely dominant unmeasured factor. Would
require encoding "did the car ahead also pit this lap?" as a predictor — feasible, not
yet attempted.

### D. Team radio has no transcriptions
*Carried from the diagnostic phase*

15,575 rows in `silver_team_radio`, all audio URLs. Current analysis can only use message
*volume* and clustering. A speech-to-text pass would enable classifying messages by type
(strategy, problem, encouragement) and rerunning the radio-outcome correlation on content
rather than count.

### E. Wet-weather analysis awaits more data
*Carried from the diagnostic phase*

Revisit as the 2026 season progresses. By season end (~24 races) some wet-race counts may
approach the 30 needed for 80% power.

### F. Circuit-specific strategy effects only partly explored
*Carried from the diagnostic phase*

Circuit has essentially no effect on median stop duration, but circuit-specific
*overtaking* rates and strategy outcomes weren't fully examined. Monaco versus Monza as
extreme opposites would be a targeted analysis worth running during feature engineering.

### G. `s05c_racemap` does not reproduce itself
*Raised 2026-08-14. OUTPUT STABILISED 2026-08-21, see #59. The cause below is still
not understood, so this stays open; what changed is that it no longer reaches the
bundle. Everything from here to "Next step" is the original entry, kept for the trail.*

Running the pipeline twice on unchanged inputs gives a different `map_circuit_outline`.
Every other table in the bundle is byte-identical across runs; this one is not.

**What varies.** One or two circuits out of 24 per run trace their outline from a
different reference lap. The row count never moves (9,600, 400 points x 24 circuits) and
neither does any other table. Observed:

| run pair | circuit | chosen lap A | chosen lap B |
|---|---|---|---|
| 1 vs 2 | 61 | drv 4, lap 12, 91.839s | drv 16, lap 18, 91.763s |
| earlier pair | 9 (Austin) | drv 1, lap 11, 92.143s | drv 4, lap 11, 92.214s |
| earlier pair | 19 (Spielberg) | drv 1, lap 17, 64.314s | drv 1, lap 14, 64.426s |

Outline points move by up to **9.75 m** in x and 9.49 m in y on a circuit several
kilometres long, and `time_fraction` by up to 0.0038.

**What has been ruled out**, each measured rather than reasoned about:

- the candidate list is identical across processes (same md5 over 144 rows, 3 processes)
- there are no ties on the sort key, so the unstable `sort_values` cannot reorder them
- `fetch_positions` returns the same 4,545,724 rows with the same content hash and the
  same row order across processes
- the single big OR-chain query is not dropping rows: for all 18 candidate laps at the
  three affected circuits, a targeted per-lap query and the bulk fetch agree exactly
- every candidate lap has 240 to 363 position samples against a `MIN_OUTLINE_SAMPLES`
  cutoff of 120, so on the measured evidence no candidate should ever be rejected
- `build_outlines` is stable when called twice on identical inputs in one process

That last point is the puzzle: identical inputs give identical outputs in-process, the
inputs are provably identical across processes, and yet the output differs between
processes.

**Why it is not urgent.** Each candidate is a clean fast qualifying lap at the same
circuit, so every outcome is a valid outline; the map is drawn from a different good lap,
not a wrong one. Nothing else reads `source_driver_number` or `source_lap_number`.

**Why it still matters.** A bundle that differs run to run cannot be verified by
comparison, which is the technique every other check in this project relies on. It also
means "republish and diff" cannot distinguish a real data change from noise.

**Next step when picked up:** make the choice deterministic by construction rather than
by finding the mechanism. Evaluate all candidates and select on an explicit total order
(sample count, then session_key, driver_number, lap_number) instead of taking the first
that traces, and replace the `pairs` set with a sorted list so the query text cannot vary.
Then run `s05c` four or more times and require one distinct geometry hash.

---

**2026-08-21. That next step was tried and DID NOT WORK.** All of it was implemented: the
total order, the sorted `pairs`, a stable sort with unique tiebreakers on the candidate
list, and a deterministic order imposed on the fetched rows. Six runs then produced **four
distinct geometry hashes**. The prediction in that paragraph was wrong, and it is left
above rather than edited so the wrong guess stays visible.

**One real bug was found on the way**, and it is fixed regardless: `pick_trace_candidates`
sorted on three keys with pandas' default quicksort, which is not stable. A tie there does
not merely reorder candidates, it changes WHICH SIX survive the `head(MAX_TRACE_ATTEMPTS)`.
It was not the cause, but it was wrong.

**The measurement that narrows it.** A diagnostic dumped every intermediate stage of two
separate processes and compared them:

| stage | agrees? |
|---|---|
| circuit list, sessions, candidate laps, candidate order | same |
| query text | same |
| position rows: count, content hash, order hash | **same** (4,545,724 rows) |
| per-candidate sample counts | **DIFFERS**, 4 of 144 |
| chosen lap, final geometry | same, in that pair |

So the position data is byte-identical and the candidate laps are byte-identical, and yet
`lap_segment`, a plain boolean mask over that data, counted a different number of rows.
Not slightly different: **all or nothing**. A lap with 310 samples in one run had **zero**
in the next, and the flips swap between a small fixed set of sessions rather than
scattering. That is the whole remaining mystery, and it contradicts the "no candidate
should ever be rejected" bullet above, which was measured on a run where they all passed.

**The entry above understated the severity.** The unpinned six-run baseline dropped
Suzuka's outline **entirely** on one run, not merely tracing it from a different lap. A
circuit with no outline shows "No track map for this circuit" to a visitor, so this was
user-visible after all, which the original "why it is not urgent" paragraph got wrong.

**Still open:** why identical inputs give different sample counts. The flips cluster in a
few sessions, which suggests something specific about those laps, perhaps a timestamp at
an exact boundary, rather than general noise. Worth pulling on, and no longer blocking
anything.