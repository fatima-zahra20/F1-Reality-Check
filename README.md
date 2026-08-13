# F1 Reality Check

An end-to-end Formula 1 analytics project: from raw API ingestion through statistical
diagnosis to a calibrated race-winner prediction model, published as an interactive
dashboard.

Built with SQL, Python, and a deliberate constraint — the project began with **no prior
F1 domain knowledge**, which forced every assumption to be verified empirically against
the data rather than assumed from familiarity with the sport.

> **Status:** descriptive and diagnostic phases complete. Predictive phase in progress.
> Dashboard not yet published.
>
> Figures below were re-verified against the diagnostic notebooks' stored outputs on
> 2026-07-27, after the ingestion backfill recovered 320,127 previously-missing rows.

---

## Table of contents

- [What this project does](#what-this-project-does)
- [The question it answers](#the-question-it-answers)
- [Data](#data)
- [Architecture](#architecture)
- [Analytical phases](#analytical-phases)
- [Key findings](#key-findings)
- [Modeling approach](#modeling-approach)
- [Repository structure](#repository-structure)
- [Running the pipeline](#running-the-pipeline)
- [Data quality register](#data-quality-register)
- [Methodological principles](#methodological-principles)
- [Roadmap](#roadmap)

---

## What this project does

Formula 1 produces an enormous amount of public telemetry and timing data. This project
takes that raw data and works through the full analytics maturity model — **descriptive
→ diagnostic → predictive** — with each stage building on the evidence established by
the last.

The output is twofold:

1. **A win-probability model** that produces a calibrated probability distribution over
   the driver field for an upcoming race, in two variants: one usable before the
   weekend starts, and a sharper one available once qualifying has set the grid.
2. **A published analytical record** showing not just the predictions but the diagnostic
   work behind them, and — importantly — an honest track record of how the model has
   performed on races it had never seen.

---

## The question it answers

> *Who is going to win the next race, and how confident should we actually be?*

The naive approach trains twenty independent "will this driver win?" classifiers and
produces impossible outputs — probabilities that sum to well over 100%. Exactly one
driver wins a race, so the model must output a **probability distribution over the
field**, summing to 1.0. That constraint dictates the model class (multinomial /
conditional logit rather than independent binary classifiers) and is treated as a
first-order design decision rather than a detail.

---

## Data

| | |
|---|---|
| **Source** | [OpenF1 API](https://openf1.org) — unofficial public F1 data |
| **Coverage** | 2023 – 2026 |
| **Store** | SQLite, three files: `bronze_f1.db` 3.0 GB, `f1.db` 368 MB (silver), `gold_f1.db` 158 MB |
| **Layers** | Bronze (raw) → Silver (18 typed, PK-enforced) → Gold (18 conformed, what analysis reads) |
| **Grain** | Per driver, per lap, per session, down to ~3.7 Hz telemetry where available |

Scale of the silver layer. *Counts as of 2026-08-12; `s03_verify` reports them live on
every run.*

| Table | Rows |
|---|---:|
| `silver_intervals` | 2,131,182 |
| `silver_position` | 310,397 |
| `silver_laps` | 239,102 |
| `silver_weather` | 47,726 |
| `silver_stints` | 34,567 |
| `silver_pit` | 29,573 |
| `silver_race_control` | 22,423 |
| `silver_overtakes` | 22,438 |
| `silver_team_radio` | 15,575 |
| `silver_drivers` | 9,949 |
| `silver_session_result` | 8,447 |
| `silver_championship_drivers` | 2,098 |
| `silver_starting_grid` | 2,064 |
| `silver_championship_teams` | 1,001 |
| `silver_sessions` | 490 |
| `silver_meetings` | 100 |

Counts as reported by `pipeline/s03_verify.py` on 2026-07-27. `silver_pit` and
`silver_team_radio` are the two tables still awaiting a silver rebuild — 4,080 rows
were recovered into bronze for them after this run (see [Roadmap](#roadmap)).

Full column-level documentation, including null counts, type decisions, and per-table
quirks, is in **[DATA_DICTIONARY.md](DATA_DICTIONARY.md)**.

### Effective sample size

A point worth stating plainly, because it constrains everything downstream:

| | Races with results |
|---|---:|
| 2023 | 22 |
| 2024 | 24 |
| 2025 | 24 |
| **Training total (2023–25)** | **70** |
| 2026 (in progress) | 11 |

At ~20 drivers per race this is ~1,400 driver-race rows — but the target is *who won*,
and there is one winner per race. So the **effective sample is 70 events, not 1,400**.
That supports roughly 4–6 predictors, not fifteen, and rules out high-variance models
like gradient boosting as a primary approach. The feature set was kept small
deliberately, and the diagnostic phase is what justified which features earned a place.

These counts come from the verification gate's live check (every non-cancelled Race
session that has both laps and results), not from a hardcoded list. They rose from 66
to 70 when the backfill recovered four previously-missing races.

---

## Architecture

### What exists and runs

```
OpenF1 API
    │
    ▼
[ s01_ingest / s01_backfill ]  ──▶  bronze_f1.db      3.0 GB, raw
    │
    ▼
[ s02_build_silver ]  ──▶  f1.db                      368 MB, 18 typed, PK-enforced
    │
    ▼
[ s02b_caution_flags ]  ──▶  silver_caution_periods, silver_lap_flags
    │
    ▼
[ s03_verify ]  ──▶  22-check invariant gate: FAIL halts the run
    │
    ▼
[ s07_build_gold ]  ──▶  gold_f1.db                   158 MB, 18 conformed tables
    │                    every question below reads this, not silver
    │
    ├──▶ [ s04_descriptive ]  ──▶  7 fact/dim tables, written into the bundle
    ├──▶ [ s05_diagnostic ]   ──▶  29 statistical tests           ──▶ csv
    ├──▶ [ s05b_perfect ]     ──▶  perfect-lap model              ──▶ csv
    ├──▶ [ s05c_racemap ]     ──▶  circuit geometry (bronze telemetry) ──▶ csv
    └──▶ [ s05d_telemetry ]   ──▶  tow and DRS effects            ──▶ csv
                   │
                   ▼
        [ s06_publish ]  ──▶  dashboard.db (21 tables) ──▶ gzip ──▶ GitHub Release
                                                                        │
                                                                        ▼
                                                          Streamlit app downloads it
```

`s07` runs **before** `s04` and `s05` and halts the run if it fails. Gold is fully
derived, so a run that rebuilt silver and skipped gold would analyse the previous week's
data and report success. That is the same failure shape as the scheduled task dying
silently for four months (NOTES_LOG #43).

### What does not exist yet

The predictive layer. A training table, a model, and a prediction step are the next
phase and are **not built**. Earlier versions of this diagram showed them as
`s06_features` / `s07_train` / `s08_predict` / `s09_export`, which was aspirational and
also collided with the real `s07_build_gold`. When they arrive they will read gold and
assemble the training matrix **outside** the database, because which seasons, which lag
and which target are modelling decisions gold must not take sides on.

Each step is an independent, idempotent Python entry point. `run_pipeline.py` sequences
them and exits non-zero on failure. The orchestrator is therefore a swappable detail,
currently Windows Task Scheduler, trivially replaceable with Airflow, Prefect or
Dagster because the task boundaries are already clean.

**Scheduling is calendar-driven, not fixed-weekday.** A race weekend is bursty, not a
steady drip: results land early in the week, and grid position, the single strongest
individual predictor, does not exist until Saturday. Runs are therefore triggered off
the session calendar in `silver_sessions`.

**Telemetry lives in bronze only.** `car_data` (9.4M rows) and `location` (25.8M) cover
32 of 490 sessions, so they cannot be model features or appear in season-wide
aggregates. Their silver copies were **dropped in the 2026-07-28 split**, which took
`f1.db` from 6.4 GB to 352 MB. `s05b`, `s05c` and `s05d` read them from bronze directly.
They are excluded from the scheduled run and refreshed manually.

---

## Analytical phases

| Phase | Question | Status |
|---|---|---|
| **IDA / profiling** | Is the data trustworthy? | Complete |
| **Descriptive** | What happened? | Complete |
| **Diagnostic** | Why did it happen? | Complete |
| **Predictive** | What will happen? | In progress |
| **Prescriptive** | What should be done? | Out of scope |

The descriptive and diagnostic layers are both organised around the same spine — the
chronological *story of a race*, examined once at driver level and once at team level:
grid and setup → lap 1 → race pace → tyre strategy → pit stops → position dynamics →
gaps and race context → incidents → team radio → finish and outcome → driver vs
teammate.

The descriptive layer answers each stage factually. The diagnostic layer revisits every
stage asking *why*, using regression, ANOVA/ANCOVA, logistic regression, chi-square,
paired t-tests, correlation, and variance tests.

The full question banks, with completion status, are in
**[DESCRIPTIVE ANALYTICS/descriptive_question_bank.md](DESCRIPTIVE%20ANALYTICS/descriptive_question_bank.md)**
and
**[DIAGNOSTIC ANALYTICS/diagnostic_question_bank.md](DIAGNOSTIC%20ANALYTICS/diagnostic_question_bank.md)**.
The running record of data-quality findings and methodological decisions is in
**[NOTES_LOG.md](NOTES_LOG.md)**.

---

## Key findings

These are the diagnostic results that shaped the feature set.

**Pace dominates everything.** Session-normalized mean race pace (driver mean lap minus
session median) explains ~37% of race-level and ~53% of season-level team points
variance on its own. Reliability carries a small but real independent effect at race
level (p = 0.029, standardized coefficient roughly 9× smaller than pace) and is not
detectable at season level (p = 0.693, n = 40). Pit strategy adds essentially nothing
(ΔR² = 0.001). All three are highly correlated because all three measure the same
latent variable: car quality.

**Grid position is the strongest single race-level predictor** (R² = 0.593, slope
0.684), and this relationship does *not* vary significantly by circuit type (p = 0.683)
— contradicting the intuition that street circuits should punish a poor grid slot more
heavily.

**Reliability is a car effect, not a driver effect.** DNF rate is team-specific
(Williams 21.5% worst, McLaren 6.3% best; χ² = 24.7, p = 0.003). Williams fragility held
across three different drivers, all in the 17.6–25.0% band. The cleanest evidence is a
natural experiment: Carlos Sainz, the same driver, recorded an 11.4% DNF rate at Ferrari
and 17.6% at Williams — a directional match to each team's own rate, not an exact one.

**At driver level, qualifying pace outranks race pace.** Ranking predictors of the
points gap between teammates by standardized coefficient: qualifying pace (2.058) >
race pace (1.656) ≈ reliability (1.644) > pit strategy (0.602, p = 0.047). Race pace and
reliability are close enough that this data cannot rank them against each other.

**Fewer pit stops win.** At the same starting position, one-stop strategies beat
two-stop by ~0.58 places (ANCOVA, p = 0.006) and three-plus-stop by ~0.61 places
(p = 0.032). Confirmed by two independent methods — the teammate-level logistic
regression finds the same direction (coefficient −1.51, p < 0.001).

**Overtakes are gap-driven.** Conversion rate is ~17.5%; gap to the car ahead is the
dominant predictor (logistic coefficient −1.355), with tyre delta also significant
(−0.050). n = 38,381 opportunities.

**Being lapped is pure pace deficit, not unreliability.** Team is the strongest
predictor of lapping rate — Ferrari 1.4% versus Sauber 51.4% — driven by a 0.885 s/lap
pace deficit relative to session median.

**Wet-weather advantage exists but is not yet statistically demonstrable.** Sauber,
Alpine and Haas show negative `wet_advantage` (gaining more places in the wet); Ferrari,
RB, McLaren and Mercedes positive. This remains **descriptive only**: a maximum of 17 wet
races per entity against the ~30 required for 80% power at a two-position effect size,
and the metric is confounded by typical grid position (back-of-grid entrants have more
places available to gain in any conditions). Reported as a candidate feature with its
limitations stated, not as a result.

---

## Modeling approach

**Target.** A probability distribution over the driver field for a single race.
Constructor-level win probability is derived from the driver distribution.

**Two prediction stages, defined by feature availability.** This distinction is
deliberate and central:

| Stage | When | Features available |
|---|---|---|
| Pre-weekend | Mon–Thu | Rolling form only — no grid, no practice |
| Post-qualifying | Sat evening | Adds grid position |

Publishing both, and showing how the distribution shifts once the grid is set,
demonstrates handling of the most common silent failure in prediction projects:
**leakage** — training on features that would not have existed at the moment of
prediction.

**Features** (kept deliberately small given 70 events):

1. `session_normalized_team_pace` — trailing, the dominant predictor
2. `grid_position` — post-qualifying stage only
3. `wet_advantage × race_had_rain` — interaction term, **not yet leakage-safe** (see below)
4. `rolling_dnf_rate` — trailing reliability, capturing trend rather than season average
5. `circuit_type` — categorical

**Open issue on feature 3.** `race_had_rain` is a race-day observation. It does not exist
at the pre-weekend stage, and does not exist at the post-qualifying stage either. As
written, this term leaks. It needs either a forecast input, a rain-probability prior by
circuit and month, or removal from both stages — a decision to be made before the
training table is built, not inside it.

**Validation is rolling-origin, not a random split.** Eleven held-out 2026 races is far
too few to distinguish skill from luck. Instead the model trains on 2023 and predicts
2024 race by race, then trains on 2023–24 and predicts 2025, and so on — always
predicting forward, never using future information. This yields ~48 genuinely
out-of-sample predictions within the training seasons, ~59 including 2026, while
respecting time order.

**Evaluation uses Brier score and calibration curves, not accuracy.** With 20 classes
and a favourite who wins roughly 40% of the time, accuracy is close to meaningless. The
question that matters is whether a stated 30% happens about 30% of the time.

**Baseline first.** A well-specified multinomial logit is the primary model. Anything
more complex must beat it on out-of-sample Brier score to earn inclusion — and with 70
events, it very likely will not.

---

## Repository structure

```
F1-Reality-Check/
├── DATA INGESTION/
│   ├── bronze_f1.db               raw API output, 3.0 GB (gitignored)
│   ├── f1.db                      silver, 368 MB (gitignored)
│   ├── gold_f1.db                 gold, 158 MB (gitignored, rebuilt by s07)
│   └── openf1_ingestion.py        API ingestion
├── SCHEMA MODELING/
│   └── to_silver.sql              reference only; s02 is the executable build
├── DATA PROFILING/
│   ├── EDA_01-07.sql              PK verification, column profiling,
│   │                              completeness, domain integrity,
│   │                              consistency, cardinality, temporal coverage
│   └── EDA_08.ipynb
├── DESCRIPTIVE ANALYTICS/         10 SQL files, one per story-of-a-race theme
├── DIAGNOSTIC ANALYTICS/          7 notebooks (statistical tests + regressions)
├── dashboard/                     Streamlit app: views, story pages, race map
├── pipeline/
│   ├── config.py                  single source of truth for all paths
│   ├── run_pipeline.py            sequences everything, exits non-zero on failure
│   ├── s01_ingest.py              weekly incremental ingest
│   ├── s01_backfill.py            targeted re-ingestion of failed fetches
│   ├── s02_build_silver.py        bronze -> silver build driver
│   ├── s02b_caution_flags.py      range-based SC / VSC / RED periods
│   ├── s03_verify.py              22-check invariant gate
│   ├── s07_build_gold.py          silver -> gold, 18 conformed tables
│   ├── s04_descriptive.py         7 fact/dim tables, written into the bundle
│   ├── s05_diagnostic.py          29 statistical tests
│   ├── s05b_perfect.py            perfect-lap model
│   ├── s05c_racemap.py            circuit geometry from bronze telemetry
│   ├── s05d_telemetry.py          tow and DRS effects
│   ├── s06_publish.py             bundle + upload to the GitHub Release
│   └── audit_consumer_rules.py    which decisions gold owns vs re-decided
├── outputs/dashboard/             the bundle, plus 18 analysis-output CSVs
├── models/                        serialized models + metrics, versioned
├── logs/
├── data_prep.py                   shared loaders and cleaning utilities
├── streamlit_app.py               dashboard entrypoint
├── DATA_DICTIONARY.md             column-level documentation, silver and gold
├── GOLD_INVENTORY.md              the audit that motivated the gold layer
├── NOTES_LOG.md                   data-quality findings + decisions
├── environment-pipeline.yml
└── requirements.txt
```

`s07` is numbered after `s06` but **runs before `s04` and `s05`**, because they read
gold. The numbering is historical: `s07` was going to be a training step.

The predictive layer is not yet written.

---

## Running the pipeline

**Environment.** Anaconda Python 3.13.9 with pandas 2.3.3, scipy 1.16.3,
statsmodels 0.14.5, scikit-learn 1.7.2. Versions are pinned — pandas is deliberately
held at 2.x, since 3.x introduces breaking changes to code validated on 2.3.3.

```bash
conda env create -f environment-pipeline.yml
conda activate f1-reality-check
```

or with pip:

```bash
pip install -r requirements.txt
```

**No database is in version control.** All three are regenerable output, not source, and
`*.db` is gitignored. Rebuild from the API:

```bash
python "DATA INGESTION/openf1_ingestion.py"     # first-time ingest
python pipeline/s01_backfill.py                 # dry run, shows the plan
python pipeline/s01_backfill.py --execute       # add --include-optional for pit/team_radio
python pipeline/s01_backfill.py --recheck-empty # an "empty" verdict is not permanent
python pipeline/s02_build_silver.py
python pipeline/s02b_caution_flags.py           # derived caution tables
python pipeline/s07_build_gold.py --execute     # gold: what analysis reads
```

**Verify before trusting anything.** The gate re-checks every invariant established
during profiling and exits non-zero on failure:

```bash
python pipeline/s03_verify.py
```

It reports three tiers: **FAIL** (an invariant broke, the pipeline must stop), **WARN**
(a known, accepted quirk worth re-seeing each run), and **INFO** (drift monitoring,
row counts and coverage, logged and diffed week over week).

**Or run the whole thing.** `run_pipeline.py` sequences everything, skips the serving
layers if the gate fails, and stops if gold fails:

```bash
python pipeline/run_pipeline.py                  # dry run: plans ingest, builds nothing
python pipeline/run_pipeline.py --execute
python pipeline/run_pipeline.py --execute --publish   # also refresh the live dashboard
```

`--publish` needs `GITHUB_TOKEN` with `contents:write`. Note that the deployed Streamlit
app caches the downloaded bundle in its container's temp directory, so **publishing takes
effect on Reboot, not on Clear cache**.

**Which layer to query.** Silver is the source of truth about what the API said. Gold is
where the decisions live, and is what every analysis should read. Two audits keep that
honest:

```bash
python pipeline/audit_consumer_rules.py    # which decisions gold owns vs re-decided at call sites
python pipeline/s07_build_gold.py --list   # the 18 gold tables
```

---

## Data quality register

these gaps were found and fixed, not standing

**Caution periods were wrong in five separate ways, and every one of them left real
neutralised laps recorded as green-flag racing.** Found 2026-08-11 and 2026-08-12. The
recurring shape: race control announces the event **in prose under
`category='Other'`** rather than as a flag, so a parser looking for flags never sees it.

| # | Bug | Effect |
|---|---|---|
| 1 | `RED FLAG - RACE SUSPENDED` as message text, not `flag='RED'` | 20 pre-season red flags missed; Monaco 2026 ran 17 laps of 2,260s each as green |
| 2 | Unclosed periods closed at the **scheduled** session end | any race that overran closed early; 18 periods had `date_end < date_start`, 36 more silently truncated |
| 3 | Restarts inferred with an untested `RESTART_FACTOR = 2.0` | moving it to 2.5 shifted one restart by 25 minutes, decided by a single car |
| 4 | A race that **starts** behind the safety car sends no deployment message | Spa 2025 ran its first four laps at 1.57-1.86x pace, all 80 recorded green |
| 5 | A red flag resuming with a **standing start** closed before the grid formed | Monaco 2026 lap 70, sixteen cars at 2.02x, unflagged |

Bug 3 was **replaced rather than tuned**: a constant that changes the answer is a
decision in disguise. The rule became the median across cars of (first lap started after
the stoppage + its duration) minus one session-median lap. Sensitivity across
`MIN_CARS` 2/3/5/8 is **0.0s**, against 1,530-2,824s for the rule it replaced.

`neutralised` went from 17,076 laps to 11,830, and the flags now behave as their names
claim: green 1.00x, VSC 1.23x, SC 1.45x, red 1.93x. **Any analysis that filtered on
`neutralised` before 2026-08-11 was built on a contaminated population.**

**Bug 6, found 2026-08-13:** a red flag is always followed by a **formation lap**, and it
was recorded as green. Monaco (bug 5) was one instance of it. Fixed per car by time, since
the formation lap falls on different lap *numbers* for different cars: **163 laps newly
flagged, 0 lost**, and 0 of 29 verdicts changed. Gate check [21] fell from 28 unexplained
lap-events to 24.

**A gate check now guards the other direction.** Check [22] fails the run when a lap
flagged as neutralised ran at or faster than its session's green median. Every caution fix
before this was validated by throwaway scripts, and one widening of the restart rule
flagged 697 racing laps while the gate still reported PASS. Over-flagging is the more
dangerous direction: a wrongly flagged lap simply disappears from every analysis and
nothing downstream can tell it from a real one.

**One is known, measured and still open.** The safety car withdrawal lap is partly green:
`SAFETY CAR IN THIS LAP` means the car leaves at the *end* of the lap, but the period
closes at the message. 35 laps over 12 races, biased toward the back of the grid because
the leaders have already crossed the line, and caught by no outlier filter. It is not a
rule change: cars begin the same lap up to 154 seconds apart, so no single timestamp is
correct for all of them, and two attempts were reverted for flagging more racing laps than
they caught. Detail in `DATA_DICTIONARY` and NOTES_LOG #52.

**A duration window was hiding bug 4.** `DATA_DICTIONARY` recommended
`lap_duration BETWEEN 60 AND 300`, used at no call site. Its floor can never fire (zero
laps of 239,102 are under 60s; the fastest ever recorded here is 63.971s) and its ceiling
removed 11 race laps of 81,769, **ten of which were lap 1 of the 2025 Belgian Grand
Prix**. It was a patch over a caution bug, covering about a tenth of it. Withdrawn;
`gold_lap.is_representative_lap` replaces it.

**A missing teammate was being treated as a zero.** `fact_driver_race` built its teammate
deltas as `own - (groupby_sum - own)`, and pandas `transform("sum")` skips NaN. A
teammate who retired therefore contributed 0 rather than nothing, so the published
`teammate_finish_delta` equalled the driver's **own finishing position** on 162 of 162
rows where the teammate did not finish. Sainz finished 4th in Bahrain 2023 and it read as
beating Leclerc by four places. Also 46 pace deltas and 3 grid deltas. Fixed 2026-08-12
by requiring both cars to have recorded the value.

**A driver number is not a driver.** 34 of 57 numbers in this dataset belong to more than
one person: #1 is Verstappen 2023-25, Paul Aron for one 2023 session, and Norris in 2026.
Keying a driver dimension on `driver_number` alone merged Verstappen's races into
Norris's row. `dim_driver` is grained by `(driver_number, year)` and `gold_driver` by
`(driver_number, full_name)`; neither is a safe join key on its own, so facts resolve the
driver per session through `gold_entry`.

**Silent type corruption.** `silver_session_result.duration` and `gap_to_leader` mixed
scalar and JSON values in the raw data. `CAST(... AS REAL)` corrupted these without
error. Resolved by splitting into five dedicated typed columns. The verification gate
treats their absence as a hard FAIL, because a regression here would produce
confident-looking but wrong output rather than a crash.

**`silver_starting_grid` scope is not what the docs imply.** It covers Qualifying and
Sprint Qualifying sessions, not Race and Sprint.

**Composite key correction.** `silver_overtakes` requires a four-column primary key —
`(session_key, date, overtaking_driver_number, overtaken_driver_number)`. The
three-column hypothesis is violated by 2,225 rows, because a driver can pass several
cars at the same recorded timestamp in a first-lap melee.

**`stop_duration` is thin, and `STOP_DURATION_MIN_YEAR = 2024` overstates it.** That
constant reads as "usable from 2024". Race-only coverage is 2023 0%, 2024 18.1%, 2025
**85.5%**, 2026 33.8%, so comparing 2024 with 2025 on this column compares an 18% sample
against an 85% one. `gold_pit` carries `has_stop_duration` per row instead of a year
cutoff. Overall coverage across all session types is 3.5% (1,038 of 29,573 rows).

**Use `pit_duration`, not `lane_duration`.** They are byte-identical across all 22,898
populated rows, maximum absolute difference 0.0. `lane_duration` is not carried into gold.

**Pit duration "outliers" are cautions, not errors.** The 16,921-second maximum used to
be listed as something to filter. 96.4% of race stops over 60 seconds happened under a
caution and 92% under a red flag, with the extreme tail being Zandvoort 2023 laps 63-64,
the whole field parked during a stoppage. Scope the session type, join the lap's own
caution flag, and green race stops sit at a 23.3s median and a 41.2s 99th percentile.
`gold_pit` has no duration threshold anywhere.

**Ingestion cached failures as completions — the root cause, now fixed.** The original
`openf1_ingestion.py` recorded an (endpoint, session) pair as complete whether the fetch
succeeded, returned genuinely empty, or failed, because `fetch()` returned `[]` in all
three cases. Any transient network error — or any fetch issued *before* a session had
taken place — became a permanent, invisible gap. Found 2026-07-27: 282 affected pairs
across all four seasons, including three 2026 races fetched on 30 June before they were
run. `s01_backfill.py` records a three-state outcome (`ok` / `empty` / `failed`) and
never marks a failure terminal. **Recovered 320,127 rows.**

**All eleven `silver_session_result` gaps and all three `silver_laps` gaps are closed.**
The eight 2023 result gaps, the three Race gaps (9507 Miami 2024, 9928 Hungary 2025,
9869 São Paulo 2025) and the three lap gaps (9165 Singapore 2023, 9655 Qatar 2024, 9858
Las Vegas 2025) were all products of the resumability bug, not upstream absence. The
gate now asserts the live invariant — every non-cancelled Race/Sprint older than three
days must have both laps and results — rather than a hardcoded list of known gaps, so
the next failure is caught automatically.

**HTTP 404 is not a definitive answer from this API.** Verified 2026-07-27: during a run
that was also drawing 429s, the `pit` endpoint returned 404 for sessions that answer 200
with rows when queried unhurried — 2024 Japanese GP (54 rows) and 2025 Las Vegas GP (23)
were both recorded as permanently empty on that basis. OpenF1 emits 404 as a
load-shedding symptom. Treat 404 as retryable, and pace requests well below 1/second.

**`pit` and `team_radio` are optional endpoints in the backfill** and were not covered by
the first pass. 14 Race sessions had full laps and 40–85 stints but zero pit rows. The
2026 races have since been recovered into bronze; the older seasons appear genuinely
absent upstream. Awaiting a silver rebuild.

**`session_key` is not monotonic with date.** São Paulo (9869, November 2025) has a
lower key than Hungary (9928, August 2025). Never use it as a chronological proxy —
critical when building trailing features, where accidental ordering by key would leak
future information.

**`silver_sessions` contains the full future calendar.** 126 sessions registered for
2026 but only 44 with results. Any `WHERE year = 2026` filter silently includes races
that have not happened. This also means the upcoming schedule is already available
locally for prediction.

**Team name drift across seasons.** Fifteen raw team names collapse to a smaller set of
constructors: AlphaTauri → RB → Racing Bulls, and Alfa Romeo → Kick Sauber → Audi.
`normalize_team_names()` handles this and must be applied before any multi-year team
aggregation. Cadillac is deliberately *not* mapped — a genuinely new 2026 constructor,
not a rename — and is excluded from comparative analyses on sample-size grounds.

**`country_code` in `silver_drivers` is NULL for 2025–2026.** The API stopped
populating it.

**DB Browser silently swallows INSERT errors.** Constraint violations must be surfaced
through Python's `sqlite3` module.

---

## Methodological principles

Established during the diagnostic phase and applied throughout.

**Thresholds are derived, never guessed.** Tukey fences are computed fresh per dataset
rather than hardcoded. Where a domain rule exists it is preferred to a statistical one —
the "fighting" threshold is 1.0s because that is F1's own DRS detection window, not
because it looked reasonable.

**Sample thresholds come from power calculations.** This repeatedly meant declining to
claim a result: driver-level DNF analysis needs n = 233 starts per driver, roughly ten
seasons; wet-weather specialisation needs 30 wet races against ~15 available. Both are
reported as descriptive with the limitation stated.

**Bonferroni correction** applied for multiple comparisons throughout.

**Session-median normalization** for any cross-circuit pace comparison — raw lap times
are confounded by circuit mix.

**Verify, never trust documentation.** The data dictionary was wrong at least twice.
Every assumption, filter, and threshold got a confirming query before being accepted.

**Composite keys join on all columns.** SQLite will not error on a partial key join, it
will silently fan out.

**Filtering belongs downstream.** The silver layer preserves raw reality — a
3,510-second "lap" is a car sitting under a red flag, and it stays. Filtering happens in
the diagnostic and predictive layers, where the choice is explicit and documented.

---

## Roadmap

**Complete**
- Fixed the ingestion resumability bug; recovered 320,127 rows and +4 training races
- Replaced the exact-lap `caution_flag` with range-based `silver_caution_periods` /
  `silver_lap_flags`, separating SC (85) / VSC (112) / RED (225). This also corrected a
  latent error: VSC was never missing from the data — it lives inside
  `category='SafetyCar'` under two message spellings, so earlier analyses counted every
  VSC as a full Safety Car
- Re-verified all diagnostic notebook conclusions against their stored outputs

- Fixed 404/429 handling in `s01_backfill.py`; 429 now honours `Retry-After`, and an
  `empty` verdict is no longer permanent (`--recheck-empty`)
- Extended the gate to 21 checks: per-endpoint coverage, a run-over-run coverage
  snapshot, bronze-versus-silver divergence, and `[21]`, which watches for the one
  signature a missed caution always has regardless of spelling, the whole field slowing
  at once
- Found and fixed **five** caution-detection bugs (see the data quality register)
- Built the **gold layer**: 18 conformed tables, `s07_build_gold.py`
- Migrated `s04_descriptive` and `s05_diagnostic` onto gold. Verified as a pure refactor:
  0 of 29 verdicts, 42 coefficients and 189 group statistics moved
- Cut 7 duplicated CSVs and 25.2 MB; dropped 4 bundle tables nothing read

**Near term**
- **Migrate the remaining consumers onto gold.** 8 of 10 modelling decisions are now
  defined there, but the old call sites still sit beside them: 26 per-test queries in
  `s05_diagnostic`, the seven diagnostic notebooks, and the dashboard modules.
  `audit_consumer_rules.py` measures the gap
- **Resolve the last three conflicts:** `EXCLUDED_TEAMS` defined twice and hardcoded at
  15 more sites, `STOP_DURATION_MIN_YEAR` imported nowhere, and corrupt `n_gear` enforced
  nowhere
- **`LAP_OUTLIER_FACTOR = 2.0`** rests on a comment claiming it removes red-flag queues.
  It runs after `neutralised = 0`, so it cannot. Decide what it is actually for
- Decide the leakage-sensitive design questions before building the training table: a
  canonical race-ordering table (`session_key` is not monotonic with date), a per-feature
  "known as of when" contract, the `race_had_rain` leak, cold-start policy for new
  entrants, and whether Sprints count as training events
- Build the training table with strictly trailing features, **outside** the database
- Baseline multinomial model, rolling-origin validation, calibration analysis
- A public prediction track record on the dashboard

**Later**
- Transcribe team radio (15,575 audio URLs, currently no text) to enable content-level
  rather than volume-level analysis
- Rebuild the position-swing and anomaly-cause analyses on `silver_lap_flags` so SC and
  VSC are separated and multi-lap neutralisations are fully covered
- Encode competing pit stops on adjacent laps — identified as the dominant unmeasured
  factor in whether a slow stop actually costs track position
- Fix the `driver_number` → `full_name` fan-out in the wet-weather driver table
- Revisit wet-weather analysis as the 2026 season adds wet races toward the power
  threshold (17 available, ~30 needed)

---

*This project uses unofficial data from the OpenF1 API and is not associated with,
endorsed by, or connected to Formula 1, the FIA, or any F1 team.*