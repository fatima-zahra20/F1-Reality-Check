# F1 Reality Check

An end-to-end Formula 1 analytics project: from raw API ingestion through statistical
diagnosis to a calibrated race-winner prediction model, published as an interactive
dashboard.

Built with SQL, Python, and a deliberate constraint — the project began with **no prior
F1 domain knowledge**, which forced every assumption to be verified empirically against
the data rather than assumed from familiarity with the sport.

> **Status:** descriptive and diagnostic phases complete. Predictive phase in progress.
> Dashboard not yet published.

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
| **Store** | SQLite (`DATA INGESTION/f1.db`, ~6.5 GB) |
| **Layers** | Bronze (raw ingested tables) → Silver (18 typed, PK-enforced tables) |
| **Grain** | Per driver, per lap, per session — down to ~3.7 Hz telemetry where available |

Scale of the silver layer:

| Table | Rows |
|---|---:|
| `silver_location` | 25,849,231 |
| `silver_car_data` | 9,365,942 |
| `silver_intervals` | 1,875,432 |
| `silver_position` | 281,801 |
| `silver_laps` | 217,692 |
| `silver_weather` | 42,915 |
| `silver_stints` | 31,033 |
| `silver_pit` | 26,791 |
| `silver_overtakes` | 20,065 |
| `silver_race_control` | 19,807 |
| `silver_team_radio` | 15,575 |
| `silver_drivers` | 9,949 |
| `silver_session_result` | 7,660 |
| `silver_championship_drivers` | 2,098 |
| `silver_starting_grid` | 1,814 |
| `silver_championship_teams` | 1,001 |
| `silver_sessions` | 490 |
| `silver_meetings` | 100 |

Full column-level documentation, including null counts, type decisions, and per-table
quirks, is in **[DATA_DICTIONARY.md](DATA_DICTIONARY.md)**.

### Effective sample size

A point worth stating plainly, because it constrains everything downstream:

| | Races with results |
|---|---:|
| 2023 | 21 |
| 2024 | 23 |
| 2025 | 22 |
| **Training total (2023–25)** | **66** |
| 2026 (in progress) | 11 |

At ~20 drivers per race this is ~1,320 driver-race rows — but the target is *who won*,
and there is one winner per race. So the **effective sample is 66 events, not 1,320**.
That supports roughly 4–6 predictors, not fifteen, and rules out high-variance models
like gradient boosting as a primary approach. The feature set was kept small
deliberately, and the diagnostic phase is what justified which features earned a place.

---

## Architecture

```
OpenF1 API
    │
    ▼
[ s01_ingest ]  ──▶  bronze tables
    │
    ▼
[ s02_build_silver ]  ──▶  18 typed, PK-enforced silver tables
    │
    ▼
[ s02b_caution_flags ]
    │
    ▼
[ s03_verify ]  ──▶  invariant gate: FAIL halts the run
    │
    ├──▶ [ s04_descriptive ]  ──▶  aggregate output tables
    ├──▶ [ s05_diagnostic ]   ──▶  recomputed statistics
    │
    ▼
[ s06_features ]  ──▶  training table (strictly trailing features)
    │
    ▼
[ s07_train ]  ──▶  model + metrics, versioned
    │
    ▼
[ s08_predict ]  ──▶  win probabilities for the next race
    │
    ▼
[ s09_export ]  ──▶  outputs/*.csv  ──▶  Tableau dashboard
```

Each step is an independent, idempotent Python entry point. `run_pipeline.py` sequences
them and exits non-zero on failure. The orchestrator is therefore a swappable detail —
currently Windows Task Scheduler, trivially replaceable with Airflow, Prefect, or
Dagster because the task boundaries are already clean.

**Scheduling is calendar-driven, not fixed-weekday.** A race weekend is bursty, not a
steady drip: results land early in the week, and grid position — the single strongest
individual predictor — does not exist until Saturday. Runs are therefore triggered off
the session calendar in `silver_sessions`.

**Telemetry is excluded from the scheduled run.** `silver_car_data` and
`silver_location` account for ~35M of the database's rows but cover only 32 of 490
sessions, making them unusable as model features. They are refreshed manually on
demand.

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

The full question bank, with completion status and the running notes log, is in
**[EDA_descriptive_questions.md](EDA_descriptive_questions.md)**.

---

## Key findings

These are the diagnostic results that shaped the feature set.

**Pace dominates everything.** Session-normalized mean race pace (driver mean lap minus
session median) explains ~40–52% of team race-points variance on its own. Adding
reliability or strategy metrics contributes essentially nothing once pace is controlled
— all three are highly correlated because all three measure the same latent variable:
car quality.

**Grid position is the strongest single race-level predictor** (R² = 0.590, slope
0.682), and this relationship does *not* vary significantly by circuit type (p = 0.612)
— contradicting the intuition that street circuits should punish a poor grid slot more
heavily.

**Reliability is a car effect, not a driver effect.** DNF rate is team-specific
(Williams 22.2% worst, McLaren 5.5% best). Williams fragility held across three
different drivers. The cleanest evidence is a natural experiment: Carlos Sainz, the same
driver, recorded an 11.9% DNF rate at Ferrari and 20.7% at Williams.

**At driver level, qualifying pace outranks race pace.** Ranking predictors of the
points gap between teammates by standardized coefficient: qualifying pace (2.194) >
race pace (1.565) > reliability (1.366) > pit strategy (0.368, p = 0.250, not
significant).

**One-stop strategies beat two-stop by ~0.68 places** at the same starting position
(ANCOVA, p = 0.006), confirmed by two independent methods.

**Overtakes are gap-driven.** Conversion rate is ~16.7%; gap to the car ahead is the
dominant predictor (logistic coefficient −1.32), with tyre delta also significant
(−0.054).

**Being lapped is pure pace deficit, not unreliability.** Team is the strongest
predictor of lapping rate — Ferrari 0.8% versus Sauber 53.2%.

**Wet-weather advantage exists but is not yet statistically demonstrable.** Ferrari,
McLaren, Mercedes and the RB lineage show positive `wet_advantage`; Alpine and Sauber
negative. This remains **descriptive only**: a maximum of 15 wet races per entity against
the ~30 required for 80% power at a two-position effect size. Reported as a candidate
feature with its limitation stated, not as a result.

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

**Features** (kept deliberately small given 66 events):

1. `session_normalized_team_pace` — trailing, the dominant predictor
2. `grid_position` — post-qualifying stage only
3. `wet_advantage × race_had_rain` — interaction term
4. `rolling_dnf_rate` — trailing reliability, capturing trend rather than season average
5. `circuit_type` — categorical

**Validation is rolling-origin, not a random split.** Eight held-out 2026 races is far
too few to distinguish skill from luck. Instead the model trains on 2023 and predicts
2024 race by race, then trains on 2023–24 and predicts 2025, and so on — always
predicting forward, never using future information. This yields ~45–50 genuinely
out-of-sample predictions while respecting time order.

**Evaluation uses Brier score and calibration curves, not accuracy.** With 20 classes
and a favourite who wins roughly 40% of the time, accuracy is close to meaningless. The
question that matters is whether a stated 30% happens about 30% of the time.

**Baseline first.** A well-specified multinomial logit is the primary model. Anything
more complex must beat it on out-of-sample Brier score to earn inclusion — and with 66
events, it very likely will not.

---

## Repository structure

```
F1-Reality-Check/
├── DATA INGESTION/
│   ├── f1.db                      SQLite store (gitignored)
│   └── openf1_ingestion.py        API ingestion
├── SCHEMA MODELING/
│   └── to_silver.sql              bronze → silver build
├── DATA PROFILING/
│   ├── EDA_01–07.sql              PK verification, column profiling,
│   │                              completeness, domain integrity,
│   │                              consistency, cardinality, temporal coverage
│   └── EDA_08.ipynb
├── DESCRIPTIVE ANALYTICS/         10 SQL files, one per story-of-a-race theme
├── DIAGNOSTIC ANALYTICS/          7 notebooks (statistical tests + regressions)
├── pipeline/
│   ├── config.py                  single source of truth for all paths
│   └── s03_verify.py              invariant gate
├── outputs/                       pipeline outputs consumed by Tableau
├── models/                        serialized models + metrics, versioned
├── logs/
├── data_prep.py                   shared loaders and cleaning utilities
├── DATA_DICTIONARY.md             column-level documentation, all 18 tables
├── EDA_descriptive_questions.md   question bank + notes log
├── environment.yml
└── requirements.txt
```

---

## Running the pipeline

**Environment.** Anaconda Python 3.13.9 with pandas 2.3.3, scipy 1.16.3,
statsmodels 0.14.5, scikit-learn 1.7.2. Versions are pinned — pandas is deliberately
held at 2.x, since 3.x introduces breaking changes to code validated on 2.3.3.

```bash
conda env create -f environment.yml
conda activate f1-reality-check
```

or with pip:

```bash
pip install -r requirements.txt
```

**The database is not in version control.** It is 6.5 GB of regenerable output, not
source. Rebuild it from the API:

```bash
python pipeline/s01_ingest.py
python pipeline/s02_build_silver.py
```

**Verify before trusting anything.** The gate re-checks every invariant established
during profiling and exits non-zero on failure:

```bash
python pipeline/s03_verify.py
```

It reports three tiers: **FAIL** (an invariant broke — the pipeline must stop), **WARN**
(a known, accepted quirk worth re-seeing each run), and **INFO** (drift monitoring —
row counts and coverage, logged and diffed week over week).

---

## Data quality register

these gaps were found and fixed, not standing

**Silent type corruption.** `silver_session_result.duration` and `gap_to_leader` mixed
scalar and JSON values in the raw data. `CAST(... AS REAL)` corrupted these without
error. Resolved by splitting into five dedicated typed columns. The verification gate
treats their absence as a hard FAIL, because a regression here would produce
confident-looking but wrong output rather than a crash.

**`silver_starting_grid` scope is not what the docs imply.** It covers Qualifying and
Sprint Qualifying sessions, not Race and Sprint.

**Composite key correction.** `silver_overtakes` requires a four-column primary key —
`(session_key, date, overtaking_driver_number, overtaken_driver_number)`. The
three-column hypothesis is violated by 2,018 rows, because a driver can pass several
cars at the same recorded timestamp in a first-lap melee.

**`stop_duration` is effectively unusable.** Measured coverage: 2023 0.0%, 2024 1.4%,
2025 7.9%, 2026 3.3%. Use `lane_duration`, which is identical to `pit_duration` in all
20,745 rows where both are populated.

**Eleven ingestion gaps in `silver_session_result`.** Eight in 2023, plus three
previously undocumented Race gaps found by systematic checking — session_key 9507
(Miami 2024), 9928 (Hungary 2025), 9869 (São Paulo 2025). All non-cancelled, so
genuinely missing. In all three, laps, pits, stints and positions are intact and only
results are absent, indicating a single failed endpoint rather than a lost session —
recoverable by targeted re-ingestion.

**Three `silver_laps` gaps** — session_keys 9165 (Singapore 2023), 9655 (Qatar 2024),
9858 (Las Vegas 2025).

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

**Near term**
- Backfill the three recoverable Race result gaps (+3 training races)
- Replace the exact-lap `caution_flag` with a range-based Safety Car flag; the current
  version under-detects SC periods and over-flags sector-scoped yellows, which
  contaminates the strongest feature
- Build the training table with strictly trailing features
- Baseline multinomial model, rolling-origin validation, calibration analysis
- Publish the dashboard, including a public prediction track record

**Later**
- Transcribe team radio (15,575 audio URLs, currently no text) to enable content-level
  rather than volume-level analysis
- Add Virtual Safety Car detection, which requires parsing the race control message text
- Encode competing pit stops on adjacent laps — identified as the dominant unmeasured
  factor in whether a slow stop actually costs track position
- Revisit wet-weather analysis as the 2026 season adds wet races toward the power
  threshold

---

*This project uses unofficial data from the OpenF1 API and is not associated with,
endorsed by, or connected to Formula 1, the FIA, or any F1 team.*