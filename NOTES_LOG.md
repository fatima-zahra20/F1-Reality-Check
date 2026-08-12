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


## Open questions

### A. `caution_flag` under-detects Safety Car periods
*Raised 2026-07-26*

`load_laps()` builds `caution_flag` by joining `silver_race_control` on exact
`(session_key, lap_number)`. But per #24, SC messages aren't tagged to every lap they
span — so `caution_flag = 0` does **not** guarantee a clean lap.

It also over-flags: `YELLOW` is included, but yellows are frequently sector-scoped
(`scope = 'Sector'`) and may barely affect a lap time.

**Fix:** derive a range-based flag from SC deployment and clearing messages, marking
every lap in between; and separate sector-scoped from track-scoped flags.

**Priority: high.** Session-normalized pace is the strongest feature in the model, and
contaminated laps degrade it directly.

### B. Virtual Safety Car is not detected at all
*Carried from the diagnostic phase*

The position-swing regression used `category = 'SafetyCar'`. VSC appears under a
different category or only in the free-text `message` field, and was never included.
Adding it would reduce the unexplained-big-swings residual (1,366 rows). Requires text
parsing.

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