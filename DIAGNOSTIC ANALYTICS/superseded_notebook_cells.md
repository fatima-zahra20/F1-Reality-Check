# Superseded notebook cells

*Archived 2026-08-16, when the .ipynb_checkpoints folders were removed.*

Jupyter writes an autosave copy of every notebook into a hidden
`.ipynb_checkpoints` folder. Those copies were all older than the real
notebooks and were deleted. Most of their cells were simply earlier drafts
of cells that still exist. The cells below are the exception: they had no
close counterpart in the current notebook, so they are kept here rather
than thrown away.

**Read the numbers here as historical, not current.** They pre-date the
2026-07-27 backfill, which recovered 320,127 rows, and the caution-flag
rewrite, which reclassified roughly one lap in nine. `s05b` now models
80,921 laps where one cell below says 62,828. Do not quote these figures;
the notebooks and NOTES_LOG carry the current ones.

The one item here with present-day value is the Monte Carlo permutation
test code from `pit_stops`, whose result survives in the notebook's prose
but whose code does not.


---

## incidents.ipynb

*Closest match in the current notebook: 36%.*

```text
METHOD:
Population: all race starters (dns=0) across Race sessions 2023-2026, 
Cadillac excluded. n=1,460 driver-race rows, 180 DNFs (12.3% overall rate).

Three separate chi-square tests of independence:
  1. circuit_type x dnf (3 circuit categories x 2 outcomes)
  2. team_name x dnf (10 teams x 2 outcomes)
  3. race_had_rain x dnf (binary rainfall x 2 outcomes)

Rainfall defined as MAX(silver_weather.rainfall) per session -- binary flag 
for "did it rain at any point during this race." Captures wet-race exposure 
without requiring a threshold on duration or intensity.

RESULT 1 -- CIRCUIT TYPE: chi2=5.81, p=0.055, dof=2, expected cells<5: 0
BORDERLINE -- just above the standard significance threshold.

DNF rates:
  Street circuits:        14.7%  (highest)
  Permanent circuits:     11.6%  (middle)
  Temporary-Road:          5.0%  (lowest, but n=60 -- thin)

Direction is physically sensible (street circuits have walls instead of 
run-off areas, incidents more likely to end races rather than result in 
track excursions). But p=0.055 doesn't clear the standard threshold. 
Temporary-Road's low rate (5%) is unreliable given only 60 rows and 3 DNFs. 
Cannot conclude a statistically confirmed circuit-type effect, but the 
Street vs Permanent direction is worth noting as a plausible trend.

RESULT 2 -- TEAM: chi2=27.24, p=0.001, dof=9, expected cells<5: 0
SIGNIFICANT -- fully reliable, strong team effect confirmed.

DNF rates by team (sorted):
  Williams:        22.2%  -- highest, more than 1 in 5 starts ends in DNF
  Aston Martin:    17.9%
  Alpine:          13.0%
  RB Family:       12.4%
  Sauber Family:   12.4%
  Haas F1 Team:    11.5%
  Mercedes:        10.1%
  Red Bull Racing: 10.1%
  Ferrari:          8.2%
  McLaren:          5.5%  -- lowest, most reliable

Two clear reliability tiers visible in the bar chart (color-coded):
  HIGH DNF RATE (red bars, >15%): Williams, Aston Martin
  LOW DNF RATE (blue bars, <8%): McLaren, Ferrari
  MIDDLE CLUSTER (gray): remaining 6 teams, 10-13% range

Williams (22.2%) and McLaren (5.5%) represent the extremes -- a 4x 
difference in DNF rate between the most and least reliable teams. This 
is a statistically real, team-specific reliability effect, not random 
attrition.

RESULT 3 -- RAINFALL: chi2=0.18, p=0.672, dof=1, expected cells<5: 0
NOT SIGNIFICANT -- rain has essentially no association with DNF rate.

DNF rates: dry races 12.5%, wet races 11.4% -- virtually identical.
Counterintuitive but defensible: F1 has several mechanisms that offset 
the expected "rain = more incidents = more DNFs" story:
  - Race directors deploy Safety Cars and VSCs more aggressively in wet 
    conditions, reducing on-track incident severity
  - Teams and drivers adapt strategy conservatively in rain
  - Wet-race incidents often result in spins/recoveries rather than 
    terminal damage, since speeds are lower
  - Red flags (full race suspension) in extreme wet conditions can reset 
    the race, reducing accumulated attrition

OVERALL RANKING -- WHICH FACTOR MATTERS:
  1. TEAM: clearly significant (p=0.001). Some teams are genuinely more 
     fragile than others -- a real engineering/reliability effect.
  2. CIRCUIT TYPE: borderline (p=0.055). Plausible direction but not 
     statistically confirmed with this sample.
  3. RAINFALL: completely non-significant (p=0.672). Rain does not 
     increase DNF rate in this data.

CONVERGES WITH EARLIER FINDINGS:
The lapped-driver analysis showed Williams with the highest lapping rate 
(37.8%) and McLaren with one of the lowest (6.7%). The DNF analysis now 
adds a second dimension: Williams also has the highest DNF rate (22.2%) 
and McLaren the lowest (5.5%). Both measures -- lapping rate and DNF rate 
-- point at the same underlying story: Williams is consistently the most 
fragile team on pace AND reliability, while McLaren is the most robust.

METHODOLOGICAL NOTE:
Rainfall defined as "any rain during the session" rather than "majority 
wet" or "intensity-weighted." A stricter definition (e.g., >50% of 
samples showing rain) might yield a different result for "fully wet 
races" specifically. Left as a possible future refinement.
```


---

## pit_stops.ipynb

*Closest match in the current notebook: 44%.*

```python
from scipy.stats import chi2_contingency
from itertools import product
import numpy as np


df_pits_full = df_pits_full.sort_values(['session_key','driver_number','lap_number'])
df_pits_full['stop_number'] = df_pits_full.groupby(['session_key','driver_number']).cumcount() + 1
print(df_pits_full['stop_number'].value_counts().sort_index())

table_stop = pd.crosstab(df_pits_full['stop_number'], df_pits_full['is_disaster'])
print(table_stop)

chi2, p, dof, expected = chi2_contingency(table_stop)
print(f"\nchi2={chi2:.3f}, p={p:.6f}")
print(f"Expected cells < 5: {(expected < 5).sum()}")

print("\nDisaster rate by stop number:")
print(df_pits_full.groupby('stop_number')['is_disaster'].agg(['mean','count']))

# --- NEW: cap at 3+ and run exact test ---
df_pits_full['stop_number_capped'] = df_pits_full['stop_number'].clip(upper=3)
table_capped = pd.crosstab(df_pits_full['stop_number_capped'], df_pits_full['is_disaster'])
print("\nCapped contingency table (3×2):")
print(table_capped)

chi2_c, p_c, dof_c, expected_c = chi2_contingency(table_capped)
print(f"\nChi-square (capped): chi2={chi2_c:.3f}, p={p_c:.6f}")
print(f"Expected cells < 5: {(expected_c < 5).sum()}")
print(f"\nExpected counts:")
print(pd.DataFrame(expected_c, 
                   index=table_capped.index,
                   columns=table_capped.columns).round(2))

# contribution of stop_3+ disaster cell
obs_cell = table_capped.loc[3, 1]
exp_cell = expected_c[2, 1]
contribution = (obs_cell - exp_cell)**2 / exp_cell
print(f"\nStop 3+ disaster cell: observed={obs_cell}, expected={exp_cell:.2f}")
print(f"Contribution to chi2: {contribution:.2f} ({contribution/chi2_c*100:.1f}% of total)")

# Fisher-Freeman-Halton exact test
from scipy.stats import fisher_exact
# Note: fisher_exact only works for 2x2 -- for 3x2 we use monte carlo permutation
from scipy.stats import chi2_contingency
chi2_exact, p_exact = chi2_contingency(table_capped, lambda_="log-likelihood")[:2]

# Monte Carlo exact test via permutation
np.random.seed(42)
n_permutations = 10000
observed_chi2 = chi2_c
count_extreme = 0

flat_data = df_pits_full[['stop_number_capped','is_disaster']].copy()
for _ in range(n_permutations):
    shuffled = flat_data['is_disaster'].sample(frac=1).values
    perm_table = pd.crosstab(flat_data['stop_number_capped'], shuffled)
    if perm_table.shape == (3, 2):
        c, _, _, _ = chi2_contingency(perm_table)
        if c >= observed_chi2:
            count_extreme += 1

p_permutation = count_extreme / n_permutations
print(f"\nMonte Carlo permutation test (n=10,000):")
print(f"Observed chi2={observed_chi2:.3f}")
print(f"Permutation p-value: {p_permutation:.4f}")
```


---

## satart_lap.ipynb

*Closest match in the current notebook: 59%.*

```text

Method: multiple linear regression, lap_duration ~ tyre_age + C(compound) + 
track_temperature + lap_number (as specified in the question bank).

DATA PREP (several rounds of cleaning were required before results were trustworthy):
1. Started from load_laps(session_name='Race'), caution_flag==0 (existing SC/Red/ 
   Yellow filter from data_prep.py).
2. Dropped is_pit_out_lap==1 (2,075 laps) -- pit-lane driving time contaminates 
   lap_duration for reasons unrelated to tyre/temp/fuel effects.
3. Filtered lap_duration to [60,200] seconds -- removed red-flag/session-suspension 
   artifacts (max observed before filtering: 2264s) that caution_flag alone did NOT 
   catch (race control's lap_number tagging isn't exhaustive, consistent with the 
   earlier Albon/Safety Car finding).
4. tyre_age computed via range-join to silver_stints (tyre_age_at_start + laps into 
   the stint), excluding lap_end < lap_start phantom stints.
5. track_temperature joined via nearest-timestamp match (merge_asof) to silver_weather.
Final n = 62,828 laps.

PRIMARY MODEL (as specified, 4 predictors) -- R² = 0.149:
  track_temperature:  coef=-0.386, p<0.001  -> real, substantial effect. Warmer 
                       track -> faster laps, consistent with tyre warm-up physics.
  lap_number:          coef=-0.169, p<0.001  -> real, visible effect. Fuel burn-off 
                       proxy behaving as expected -- pace improves across a stint as 
                       fuel load decreases.
  compound (vs HARD):  SOFT coef=-1.903, MEDIUM coef=-1.668, INTERMEDIATE coef=+1.415, 
                       WET coef=+14.57 (all p<0.001) -- matches real-world F1 tyre 
                       hierarchy (slicks faster than wets, soft faster than hard in 
                       the dry).
  tyre_age:            coef=-0.040, p<0.001 -> SURPRISING SIGN (expected positive -- 
                       degradation should SLOW laps). Partial regression plot (run on 
                       the extended model, pattern applies here too) shows an almost 
                       perfectly FLAT cloud, no visible trend -- statistically 
                       significant only because of the very large sample size 
                       (n=62,828), not because of a practically meaningful effect. 
                       Classic case of statistical significance without practical 
                       significance.

EXTENDED MODEL (exploratory, +humidity +rainfall) -- R² = 0.155:
  Small R² increase over the primary model, as expected mathematically (adding 
  predictors can only raise or maintain R², never lower it). rainfall coef=+2.07, 
  p<0.001 -- sensible (rain slows laps). humidity coef=-0.078, p<0.001 -- weaker/
  less mechanistically obvious effect, included for completeness. Kept as a 
  secondary exploration, NOT the primary answer to the question as originally 
  specified.

MULTICOLLINEARITY CHECK (performed while testing whether to add air_temperature): 
track_temperature and air_temperature are correlated (r=0.724). Including both 
simultaneously raised R² marginally but distorted both coefficients -- 
air_temperature took an implausible POSITIVE sign despite p<0.001. Confirms: 
multicollinearity doesn't hurt overall model fit, but corrupts individual 
coefficient trust. DECISION: air_temperature excluded from both models above.

TAKEAWAY: These four factors together explain 14.9% of lap-time variance -- a real 
but modest share. The remaining ~85% comes from factors this model doesn't capture 
-- most likely driver skill, car/team performance differences, traffic, and race- 
specific circumstances (this maps directly onto later diagnostic questions about 
driver consistency and team pace differences). Individually: track_temperature and 
lap_number/fuel-load show genuine, visible effects; compound shows the expected, 
large, sensible hierarchy; tyre_age's effect, while statistically detectable, is 
negligible in practice within a single stint -- possibly because typical stint 
lengths in this dataset don't run long enough for classic tyre degradation to 
dominate over fuel-burn and warm-up effects.

METHODOLOGICAL NOTE FOR FUTURE QUESTIONS: partial regression plots are valuable 
alongside the coefficient table specifically to catch "significant but practically 
negligible" effects that a large sample size can manufacture -- worth using this 
check whenever a coefficient's sign or magnitude is surprising, not just when R² 
itself looks low.
```


---

## team_driver_outcome.ipynb

*Closest match in the current notebook: 53%.*

```text
METHOD:
Paired t-test per teammate pair per metric. One-sample t-test against zero 
on the delta series (driver_a - driver_b, where driver_number_a < 
driver_number_b for consistent sign). Minimum n=8 shared races. Bonferroni 
correction: α=0.05/(2 metrics × 21 pairs) = 0.00119. Qualifying results 
carried forward from Q2 (already Bonferroni corrected there).

Three metrics tested per pair:
  - Qualifying delta (from Q2): difference in qualifying lap time
  - Race pace delta: difference in session-normalized mean race lap time
  - Points delta: difference in points scored per race

21 valid pairs identified (n≥8 shared races). Results visualized as a 
dot matrix showing significance across three metrics per pair.

RESULTS BY TIER:

TIER 1 -- SIGNIFICANT ON ALL 3 METRICS (1 pair):
  Verstappen vs Tsunoda:
    Qualifying: significant (from Q2)
    Race pace: -1.10s/lap, p<0.001 (Verstappen faster)
    Points: +16.05/race, p<10^-8 (Verstappen scores 16 more points)
    The only pair with confirmed advantage across all three dimensions.
    Represents the largest statistically confirmed driver gap in the 
    dataset -- Tsunoda is a 2026 pairing with limited shared races but 
    the signal is overwhelming on every metric.

TIER 2 -- SIGNIFICANT ON 2 METRICS (2 pairs):
  Verstappen vs Perez:
    Qualifying: significant (from Q2)
    Race pace: -0.47s, p=0.025 (misses Bonferroni but directional)
    Points: +11.61/race, p<10^-9
    Well-documented F1 story confirmed statistically. Race pace just 
    misses correction threshold -- real effect, underpowered.

  Alonso vs Stroll:
    Qualifying: significant (from Q2)
    Race pace: -0.20s, p=0.164 (not significant)
    Points: +2.68/race, p=0.00006
    Alonso's advantage manifests in qualifying and points but NOT race 
    pace -- suggests his edge comes from strategic execution and 
    qualifying skill rather than raw race speed.

TIER 3 -- SIGNIFICANT ON 1 METRIC ONLY (4 pairs):
  All four significant only on qualifying:
  - Sargeant vs Albon: qualifying only
  - Leclerc vs Hamilton: qualifying only (brand new 2026 pairing, n=30)
  - Norris vs Piastri: qualifying only (race pace p=0.003, just misses)
  - Zhou vs Bottas: qualifying only

  These pairs show real qualifying advantages that are too noisy to 
  confirm in race pace or points -- consistent with qualifying being a 
  cleaner signal (one lap, controlled conditions) vs race outcomes 
  (strategy, SC, luck all add variance).

TIER 4 -- NO SIGNIFICANT ADVANTAGE ON ANY METRIC (14 pairs):
  Genuinely competitive pairings or insufficient shared races. Includes:
  Hamilton vs Russell, Leclerc vs Sainz, Magnussen vs Hulkenberg, 
  all RB Family pairings, all Alpine pairings.

KEY FINDINGS:
1. Qualifying is the easiest metric to show significance -- single lap, 
   controlled conditions, minimal noise. Most confirmed advantages show 
   up here first.
2. Race pace advantages are harder to confirm -- strategy, SC, and race 
   circumstances add enormous lap-to-lap variance.
3. Points advantages are significant only when the underlying skill gap 
   is large (Verstappen cases) or consistent over many races (Alonso).
4. Most teammate pairings (14/21) show NO statistically confirmed 
   advantage on any metric -- F1 teams pair drivers of similar ability, 
   and race-to-race noise dominates for most pairings.

BONFERRONI NOTE:
Norris vs Piastri race pace (p=0.003) just misses the strict Bonferroni 
threshold (α=0.00119). This is genuinely borderline -- a real but small 
advantage that would survive less conservative corrections (FDR/BH). 
Treat as "suggestive but not confirmed."
```

