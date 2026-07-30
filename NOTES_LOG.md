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