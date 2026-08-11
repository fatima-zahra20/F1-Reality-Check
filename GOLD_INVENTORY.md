# Gold Layer Inventory

*Produced 2026-08-11 by `pipeline/audit_consumer_rules.py`. Re-run it any time;
it is read-only and opens no database.*

## Why this document exists

Gold is meant to hold one processed, conformed view that every analytical,
diagnostic and predictive question reads from, and that must serve both the
current Streamlit dashboard and the application that will replace it.

Its specification does not need inventing. It already exists, scattered across
the codebase as filters each consumer wrote for itself. This is that
specification, collected.

The premise: a decision like *which laps count as racing laps* is a property of
the data, not of whoever happens to be querying it. Wherever such a decision is
made at a call site, it can differ from the same decision made elsewhere, and
nothing will ever report the divergence.

## What was scanned

54 files, 21,864 lines: pipeline scripts, dashboard modules, `data_prep.py`, the
seven diagnostic notebooks, the descriptive SQL library, and the profiling files.

Sources are weighted, because they do not carry equal authority.

| Tag | Meaning |
|---|---|
| `pipeline` | Produces data others depend on. A variant here is load-bearing. |
| `consumer` | Dashboard and `data_prep`. Reads data; should not define truth. |
| `notebook` | The diagnostic analyses. Their variants *are* the stated method. |
| `sql` | The descriptive query library. |
| `explore` | Profiling and EDA. Throwaway by nature: listed, never counted. |

Two kinds of decision, tallied separately so the problem is not overstated:

- **conflict** — variants are competing answers to one question. Every extra
  variant is a place the project silently disagrees with itself.
- **spread** — one rule, or legitimately different scopes for different
  questions. The count measures duplication, not conflict.

## Summary

| Decision | Kind | Variants | Sites | State |
|---|---|---|---|---|
| Valid racing lap | conflict | 5 | 14 | **Resolved in `gold_lap`** |
| Excluded teams | conflict | 3 | 19 | **3 competing rules** |
| `stop_duration` year scope | conflict | 2 | 3 | **2 competing rules** |
| Corrupt `n_gear` | conflict | 0 | 0 | **Never enforced** |
| Pit duration outliers | conflict | 1 | 1 | One filter, in one file |
| Phantom stints | conflict | 1 | 4 | One rule, applied in 4 of many places |
| Race scoping | spread | 5 | 116 | Same join rewritten 116 times |
| Neutralisation flags | spread | 6 | 121 | **Correct already. The template.** |
| Team name normalization | spread | 2 | 30 | One rule, applied by hand 30 times |
| Future calendar | spread | 2 | 9 | Two spellings |

Decisions in genuine conflict: **3**.

## Where this stands after gold was built

`gold_f1.db` now exists: **17 tables, 739,055 rows, 149.4 MB**, from
`pipeline/s07_build_gold.py`. Three dimensions, nine facts, five aggregates.

**8 of the 10 decisions above are now defined in gold.** That is not the same as
finished. A definition with call sites still beside it means the authoritative
answer exists and nothing reads it yet, so the audit reports the two states
separately and the goal is "OWNED BY GOLD with no call site left".

Still unowned, deliberately:

- **Excluded teams.** Three competing rules across 19 sites, and `config.py`'s
  `EXCLUDED_TEAMS` never reaches `s05_diagnostic`, which redefines it locally.
  Unowned because excluding Cadillac is a *modelling* decision (no 2023-25 history,
  so trailing features are undefined), not a property of the data, and gold must not
  encode either consumer's view. It belongs in the training assembly.
- **Corrupt `n_gear`.** Lives in `car_data`, which exists in bronze only and covers
  32 of 490 sessions. Nothing gold can own until that is promoted.

The four open design questions are now three-quarters settled. Gold is **wide**,
it **flags rather than filters**, and the **training matrix stays outside**. What
remains is whether trailing features live in gold, which is the next real decision
and the one that touches the predictive work.

### Verification

34 checks pass: row parity against every silver source, primary key uniqueness on
all 11 keyed tables, conformed flags reproducing the hand-rolled filters exactly,
and aggregates summing back to their facts. Both differences against the published
serving layer were traced to cause rather than accepted:

| Difference | Cause |
|---|---|
| `fact_lap` has 55 fewer race laps | `s04_descriptive.py:498` drops null `date_start`, which `merge_asof` needs. Gold keeps and flags them. |
| `dim_race` has 15 fewer races | It scopes to completed races with laps and results. Gold keeps the calendar with `has_laps` and `is_cancelled`. |

Speed on five questions the consumers actually ask: **1,676ms to 291ms, 5.8x**.
The gain is concentrated in the lap-heavy ones (caution share by circuit went
527ms to 1ms by becoming a table read); grid versus finish improved only 2.6x.

## The findings

### 1. Valid racing lap has no owner

**Resolved 2026-08-11 in `pipeline/s07_build_gold.py`. The measurement is below;
the outcome was not the one this section originally recommended.**

Adopting the documented `60-300` window would have been wrong. The floor is inert
(zero laps of 239,102 are under 60s, and the fastest lap in the dataset is 63.971s)
and the ceiling turned out to be redundant with `neutralised`: on race laps already
green and not pit-out, `60-200` removed 11 further laps, ten of which were lap 1 of
the 2025 Belgian Grand Prix. That was not an outlier population, it was an undetected
safety-car start (NOTES_LOG #48). After fixing it the window removes one lap in 81,689.

`gold_lap` therefore carries `is_valid_lap` (`lap_duration IS NOT NULL`),
`is_representative_lap` (valid, green, not pit-out) and `pace_ratio`, with no duration
window anywhere. `is_representative_lap` is row-for-row identical to the filter
`s05_diagnostic` writes by hand, so migration moves no published number.

The racemap `1.15x` and the `story_driver` Tukey fence were confirmed as legitimate
local exceptions rather than drift: they drop 10 to 12% of laps, which makes them
"representative pace" filters for a specific question, not validity rules. They stay
at their call sites, now stated as exceptions.

*Original finding, kept for the record.* Five competing definitions across 14 sites:

| Rule | Where |
|---|---|
| `BETWEEN 60 AND 200` | 9 sites, four diagnostic notebooks |
| `BETWEEN 50 AND 400` | `pipeline/s02b_caution_flags.py:332` |
| `<= 1.15 * session best` | `pipeline/s05c_racemap.py:174` |
| Tukey fence, computed per call | `dashboard/story_driver.py:200-202` |
| `> 0` | `dashboard/race_map.py:227` |

`DATA_DICTIONARY.md` recommends `BETWEEN 60 AND 300`. **Zero call sites use it.**

The consequence worth noting: `s02b_caution_flags` generates the neutralisation
flags that every lap analysis depends on, over a `50-400` population, while the
analyses consuming those flags use `60-200`. The flags and the laps they describe
are computed over different populations.

**Decision needed:** one canonical validity flag in gold, plus which of the
remaining filters are legitimate local exceptions. The racemap and telemetry
filters almost certainly are, and should be stated as exceptions rather than left
to look like drift.

### 2. Two constants only look single-sourced

`EXCLUDED_TEAMS` is defined **twice**:

- `pipeline/config.py:72`
- `pipeline/s05_diagnostic.py:94`, which redefines it locally instead of importing

It is used at `s05_diagnostic.py:127`. Adding a team to the config list would not
reach the diagnostic layer. Separately, 15 notebook sites hardcode
`team_name != 'Cadillac'` as a literal.

`STOP_DURATION_MIN_YEAR = 2024` is defined at `pipeline/config.py:73` and
**imported nowhere**. The two places that actually scope by year hardcode it:
`s02b_caution_flags.py:330` and `team_driver_outcome.ipynb`.

**Decision needed:** both become gold columns or gold-applied filters, and the
constants either drive them or are deleted.

### 3. Filters the dictionary promised were never written

`DATA_DICTIONARY.md` says "filter in gold" four times. Gold was never built, so:

- **Corrupt `n_gear` (>8, ~600 rows):** enforced nowhere. Only reference is
  `DATA PROFILING/EDA_05.sql:228`.
- **Pit duration outliers (up to 16,921s):** one filter, at
  `DESCRIPTIVE ANALYTICS/pit_stops_05.sql:36`, a Tukey fence at `> 4.9`.
- **Phantom stints (`lap_end < lap_start`, 24 rows):** `lap_end >= lap_start`
  applied at 4 weighted sites, absent from every other stint query.

**Decision needed:** each becomes a gold filter or a gold flag. A flag is
probably better than a filter, since it preserves the row and lets a consumer
opt in, which matters for the red-flag cases that are real events rather than
errors.

### 4. Race scoping is rewritten 116 times

Not a conflict: `Race`, `Qualifying`, `Sprint` and `Sprint Qualifying` are
legitimately different scopes. The finding is the duplication. The same join from
laps to `silver_sessions` to `silver_meetings`, with the same scope predicate, is
written out 116 times across pipeline, notebooks and SQL.

**Decision needed:** gold carries session context on the fact rows, so the join
disappears rather than being repeated.

### 5. Team normalization is a call-site duty

`normalize_team_names` is applied by hand at 21 sites, with 9 more definitions or
imports. `s03_verify.py:433` warns on every single run:

> apply normalize_team_names() before any multi-year team grouping

That warning exists only because nothing enforces it. In gold, names are already
conformed and the warning can be deleted.

### 6. You already solved this once, correctly

Neutralisation is **121 read sites against one definition**: materialized in
`silver_lap_flags` by `s02b_caution_flags.py`, with `s03_verify.py` check [17]
confirming it has not gone stale.

That is exactly the pattern gold generalizes. It is not a new idea to prove. It
is your own idea, applied to the nine other rows in this table.

## Open design questions

These change the schema, so they are settled before any gold table is written.

1. **How wide?** A processed mirror keeping silver's shape is a different thing
   from wide analysis-ready facts with team, circuit and weather already joined
   on. The 116 race-scoping sites argue for wide.
2. **Do trailing features live in gold?** Gold must feed predictive questions.
   Whether it holds the trailing features themselves, with the assembled training
   matrix built outside it, is undecided.
3. **Filter or flag?** Filtering makes gold smaller and safer. Flagging preserves
   real events like red-flag laps and lets each consumer choose. Probably flag,
   with a default view that filters.
4. **Is Streamlit's logic throwaway or a specification?** Gold must serve both the
   current dashboard and the future app, so it must not encode either one's view
   decisions. Note that `story_race.py`, `story_driver.py` and `story_team.py`
   currently hold analytical prose as hardcoded strings, which is work that will
   be paid for twice unless it moves into the layer.

## What this audit cannot see

- Rules expressed in ways the patterns miss: a filter split across lines, a
  threshold held in a variable, a filter applied in pandas after a broad query.
- Whether a given filter is *correct*, only whether it is *consistent*.
- Anything in the Streamlit views that shapes results through selection rather
  than through a WHERE clause.

Treat the counts as a floor, not a total.

## Re-running

```
python pipeline\audit_consumer_rules.py
```

Site counts should fall toward one as gold absorbs each rule. A decision reaching
"single-sourced" has been genuinely centralised rather than merely intended to be.
