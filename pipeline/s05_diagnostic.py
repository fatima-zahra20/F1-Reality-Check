"""
s05_diagnostic.py — builds the diagnostic serving layer for the dashboard.

Where s04 answers "what happened", this answers "why", and it does so as data
rather than prose. Every statistical claim the project makes gets recomputed
here from current silver and written to the dashboard bundle, so the dashboard
can show the finding, the evidence behind it, and the caveat attached to it.

Writes four tables into dashboard/data/dashboard.db (pipeline/serving.py owns
that path). Add --csv to also get a copy you can open and read by eye:

    diag_tests          one row per statistical test
    diag_coefficients   one row per predictor per model
    diag_groups         one row per group per metric (team/driver comparisons)
    diag_points         scatter clouds for the tests whose visual needs one

Design decisions
----------------
RECOMPUTED, NEVER PORTED. None of these numbers are copied from the diagnostic
notebooks. Those were run before the 2026-07-27 backfill (nine races recovered)
and before s02b replaced the exact-lap caution flag, which reclassified roughly
one lap in nine. The notebooks are the source of the model SPECIFICATIONS only.

DROP AND REWRITE, same as s04. These are derived views, not a log.

READ-ONLY ON SILVER. Opens f1.db with mode=ro. gold_f1.db is a separate later
layer and is deliberately untouched.

CLEAN LAPS mean silver_lap_flags.neutralised = 0, never the old caution_flag,
and never NULL — 480 laps have no flag row, and unknown status is not clean.

LAP DURATION BOUND is derived per session, not hardcoded. A lap longer than
2x that session's median clean lap is a car sitting in a red-flag queue, not a
lap. Silver deliberately preserves those (NOTES_LOG #18); filtering belongs
here, where the choice is explicit. Without this, Australia 2023 reports a mean
clean lap of 564s against a true ~84s, because the red-flag suspension laps
carry neutralised = 0.

SIGNIFICANCE IS BONFERRONI-CORRECTED wherever a family of comparisons is run.
The corrected alpha is written into the `method` string of every affected row,
because `significant` is meaningless without knowing the threshold it used.

UNDERPOWERED MEANS DESCRIPTIVE. Where a power calculation says the sample
cannot support the claim, `significant` is 0, `caveat` says so, and the
conclusion is worded as an observation rather than a result.

Usage
-----
    python pipeline\\s05_diagnostic.py
    python pipeline\\s05_diagnostic.py --tables diag_tests diag_groups
    python pipeline\\s05_diagnostic.py --tests T03 T04
    python pipeline\\s05_diagnostic.py --list

Requires the pinned Anaconda environment (python 3.13.9, pandas 2.3.3,
statsmodels 0.14.5). See NOTES_LOG #42 — a stray python 3.14 / pandas 3.0.3
install produces different numbers that look like data problems.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (DB_PATH, EXCLUDED_TEAMS, GOLD_DB_PATH,  # noqa: E402
                    LAP_OUTLIER_FACTOR)
import serving  # noqa: E402

import statsmodels.api as sm  # noqa: E402
import statsmodels.formula.api as smf  # noqa: E402
from statsmodels.stats.outliers_influence import variance_inflation_factor  # noqa: E402
from statsmodels.stats.power import TTestIndPower  # noqa: E402

# Mirrors data_prep.TEAM_NAME_MAP and s04. Cadillac deliberately unmapped.
TEAM_NAME_MAP = {
    "AlphaTauri": "RB Family",
    "RB": "RB Family",
    "Racing Bulls": "RB Family",
    "Alfa Romeo": "Sauber Family",
    "Kick Sauber": "Sauber Family",
    "Audi": "Sauber Family",
}

ALPHA = 0.05

# LAP_OUTLIER_FACTOR is imported from config, which is the single definition.

# Below this many shared races a teammate pairing cannot support a t-test.
# Used by the qualifying-delta and sector-consistency analyses alike, so the
# two report on the same set of pairings.
MIN_PAIR_SESSIONS = 8

# Same dynamic scope as s04 — no year list to maintain. See s04 for why the date
# test uses julianday rather than comparing the strings.
#
# Kept for the query sites still reading silver directly. The gold form below is
# the same races and is what the shared loaders use. The two were verified
# identical once and were not identical forever: on 23 Aug 2026 the string
# comparison held this form at 81 races while gold_session.is_analysable, which
# never had the defect, returned 82. Divergence between them is a symptom worth
# checking, not a settled question.
RACE_SCOPE = """
    SELECT s.session_key, s.meeting_key
    FROM silver_sessions s
    WHERE s.session_name = 'Race'
      AND s.is_cancelled = 0
      AND julianday(s.date_start) < julianday('now')
      AND EXISTS (SELECT 1 FROM silver_laps l WHERE l.session_key = s.session_key)
      AND EXISTS (SELECT 1 FROM silver_session_result r WHERE r.session_key = s.session_key)
"""

# The four-part predicate above, precomputed as a column in gold_session.
GOLD_RACE_SCOPE = """
    SELECT session_key, meeting_key FROM gold_session
    WHERE session_name = 'Race' AND is_analysable = 1
"""


def normalize_teams(df: pd.DataFrame, col: str = "team_name") -> pd.DataFrame:
    """Collapses renames, drops NULL team (one young-driver test session)."""
    df = df.copy()
    df = df[df[col].notna()]
    df[col] = df[col].replace(TEAM_NAME_MAP)
    return df


def drop_excluded_teams(df: pd.DataFrame, col: str = "team_name") -> pd.DataFrame:
    return df[~df[col].isin(EXCLUDED_TEAMS)].copy()


# --- collector -------------------------------------------------------------------

class Diagnostics:
    """
    Accumulates rows for the four output tables.

    Analyses append here rather than returning frames, because one analysis
    typically emits a test row, several coefficient rows and sometimes a group
    or scatter set, and keeping those together at the call site is what makes
    the code readable.
    """

    def __init__(self) -> None:
        self.tests: list[dict] = []
        self.coefficients: list[dict] = []
        self.groups: list[dict] = []
        self.points: list[dict] = []

    def add_test(self, test_id, category, question, method, statistic, p_value,
                 effect_size, effect_size_type, n, significant, conclusion,
                 caveat=None) -> None:
        self.tests.append({
            "test_id": test_id,
            "category": category,
            "question": question,
            "method": method,
            "statistic": _round(statistic),
            "p_value": _round(p_value, 6),
            "effect_size": _round(effect_size),
            "effect_size_type": effect_size_type,
            "n": int(n) if n is not None else None,
            "significant": int(bool(significant)),
            "conclusion": conclusion,
            "caveat": caveat,
        })

    def add_coefficients(self, test_id, model, fit, std_coefs=None, vifs=None,
                         skip_intercept=True) -> None:
        """Unpacks a fitted statsmodels result into one row per predictor."""
        conf = fit.conf_int()
        for name in fit.params.index:
            if skip_intercept and name == "Intercept":
                continue
            self.coefficients.append({
                "test_id": test_id,
                "model": model,
                "predictor": name,
                "coefficient": _round(fit.params[name], 4),
                "std_coefficient": _round((std_coefs or {}).get(name), 4),
                "std_error": _round(fit.bse[name], 4),
                "p_value": _round(fit.pvalues[name], 6),
                "ci_lower": _round(conf.loc[name, 0], 4),
                "ci_upper": _round(conf.loc[name, 1], 4),
                "vif": _round((vifs or {}).get(name), 3),
            })

    def add_group(self, test_id, metric, group_type, group_name, value, n,
                  ci_lower=None, ci_upper=None) -> None:
        self.groups.append({
            "test_id": test_id,
            "metric": metric,
            "group_type": group_type,
            "group_name": group_name,
            "value": _round(value, 4),
            "n": int(n) if n is not None else None,
            "ci_lower": _round(ci_lower, 4),
            "ci_upper": _round(ci_upper, 4),
        })

    def add_points(self, test_id, df, x, y, label=None, group_name=None) -> None:
        """Scatter cloud. Kept lean — only for tests whose visual needs one."""
        out = pd.DataFrame({
            "test_id": test_id,
            "x": df[x].values,
            "y": df[y].values,
            "label": df[label].values if label else None,
            "group_name": df[group_name].values if group_name else None,
        })
        self.points.append(out)


def _round(v, nd=4):
    """Rounds for CSV readability, passing through None/NaN unchanged."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return v
    if isinstance(v, (int, np.integer)):
        return int(v)
    return round(float(v), nd)


# --- statistical helpers ---------------------------------------------------------

def bonferroni(n_tests: int) -> float:
    """Corrected alpha for a family of n comparisons (NOTES_LOG #34)."""
    return ALPHA / max(n_tests, 1)


def standardized_coefficients(df: pd.DataFrame, outcome: str,
                              predictors: list[str]) -> dict:
    """
    Refits on z-scored predictors so coefficients are comparable across units
    (NOTES_LOG #37). The outcome is left in original units, so a standardized
    coefficient reads as "change in outcome per 1 SD of this predictor".
    """
    z = df[[outcome] + predictors].dropna().copy()
    for p in predictors:
        sd = z[p].std()
        z[p] = (z[p] - z[p].mean()) / sd if sd > 0 else 0.0
    fit = smf.ols(f"{outcome} ~ " + " + ".join(predictors), data=z).fit()
    return {p: fit.params[p] for p in predictors}


def compute_vifs(df: pd.DataFrame, predictors: list[str]) -> dict:
    """
    VIF per predictor, checked before any multiple-regression coefficient is
    interpreted (NOTES_LOG #37). Above ~5 means the coefficient is not
    trustworthy even when its p-value looks convincing.
    """
    x = df[predictors].dropna()
    if len(x) <= len(predictors) + 1:
        return {}
    x = sm.add_constant(x, has_constant="add")
    out = {}
    for i, name in enumerate(x.columns):
        if name == "const":
            continue
        try:
            out[name] = variance_inflation_factor(x.values, i)
        except Exception:
            out[name] = None
    return out


def cramers_v(chi2: float, table: pd.DataFrame) -> float:
    """Effect size for chi-square, so a huge n does not read as a huge effect."""
    n = table.values.sum()
    k = min(table.shape) - 1
    return float(np.sqrt(chi2 / (n * k))) if n and k else np.nan


def cohens_d(a: pd.Series, b: pd.Series) -> float:
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return np.nan
    pooled = np.sqrt(((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / (na + nb - 2))
    return float((a.mean() - b.mean()) / pooled) if pooled > 0 else np.nan


def eta_squared(groups: list[np.ndarray]) -> float:
    """Proportion of variance explained by group membership, for ANOVA."""
    allv = np.concatenate(groups)
    grand = allv.mean()
    ss_between = sum(len(g) * (g.mean() - grand) ** 2 for g in groups)
    ss_total = ((allv - grand) ** 2).sum()
    return float(ss_between / ss_total) if ss_total > 0 else np.nan


def required_n_per_group(effect_size: float, power: float = 0.80) -> float:
    """Sample size per group needed to detect `effect_size` at 80% power."""
    try:
        return float(TTestIndPower().solve_power(
            effect_size=effect_size, alpha=ALPHA, power=power, alternative="two-sided"))
    except Exception:
        return np.nan


def mean_ci(s: pd.Series):
    """95% CI on a mean. Returns (lo, hi), or (None, None) when n < 2."""
    s = s.dropna()
    if len(s) < 2:
        return None, None
    se = s.std(ddof=1) / np.sqrt(len(s))
    crit = stats.t.ppf(0.975, len(s) - 1)
    return float(s.mean() - crit * se), float(s.mean() + crit * se)


# --- shared data loaders ---------------------------------------------------------

def load_clean_laps(gold) -> pd.DataFrame:
    """
    Race laps that are genuinely representative of racing pace.

    NOW READ FROM GOLD. The first three filters are `is_representative_lap`,
    which is a conformed column rather than a WHERE clause repeated at 14 sites,
    and `team_name` arrives already conformed instead of needing
    normalize_teams(). Verified to select the identical 81,689 laps the old SQL
    did, then the trim below takes it to the identical 81,677.

    The filters, in order:
      1-3. is_representative_lap: lap_duration present, neutralised = 0
         (NULL excluded too, since 480 laps have no flag row and unknown is not
         clean), and not a pit-out lap.
      4. duration <= LAP_OUTLIER_FACTOR x the session's median clean lap.

    FILTER 4 IS AN ANALYSIS CHOICE, NOT A DATA PROPERTY, which is why it stays
    here rather than moving into gold. It uses gold's precomputed pace_ratio so
    the arithmetic is shared, but the threshold belongs to this layer.

    Its original comment claimed it removed "red-flag queues". That cannot be
    true: it runs after neutralised = 0, so red flags are already gone. What it
    actually removes now is 12 laps of 81,689, and six of those are Monaco 2026
    lap 70, where sixteen cars ran at 2.02x with nothing flagged. That is a
    missed caution, and s03_verify check [21] tracks it. So this filter is partly
    compensating for a caution bug, exactly as the 60-200s window was
    (NOTES_LOG #49). It is left at 2.0 deliberately: fixing that caution and
    changing this threshold in one step would make it impossible to attribute
    whatever moves.

    Sensitivity, for when that is picked up: 1.5x drops 118 laps, 1.8x drops 28,
    2.0x drops 12, 2.5x drops 1.
    """
    laps = pd.read_sql(f"""
        WITH scope AS ({GOLD_RACE_SCOPE})
        SELECT l.session_key, l.driver_number, l.lap_number,
               l.date_start, l.lap_duration, l.team_name, l.pace_ratio,
               l.session_green_median_s AS session_median_lap
        FROM scope
        JOIN gold_lap l ON l.session_key = scope.session_key
        WHERE l.is_representative_lap = 1
    """, gold)

    laps = laps[laps["pace_ratio"] <= LAP_OUTLIER_FACTOR].copy()

    # Recompute after trimming so the normalisation baseline is itself clean.
    laps["session_median_lap"] = laps.groupby("session_key")["lap_duration"].transform("median")
    laps["lap_vs_median"] = laps["lap_duration"] - laps["session_median_lap"]
    # gold conforms team_name already; this only drops the null-team rows, which
    # measured as 0 in race scope. Kept so the contract does not depend on that
    # staying 0.
    return normalize_teams(laps.drop(columns="pace_ratio"))


def load_driver_race(gold, laps: pd.DataFrame) -> pd.DataFrame:
    """
    One row per driver per race: result, grid, pace, stops.

    NOW READ FROM GOLD. The grid hop is the part worth noting. A grid is
    published against the session that SET it, so attaching a race to its grid
    means going out to the meeting and back in to Qualifying, paired on
    session_name and never session_type, which would group Sprint Qualifying in
    and fan the join out (NOTES_LOG #26). gold_session_result did that once, so
    grid_position is a column here rather than two more LEFT JOINs.
    """
    df = pd.read_sql(f"""
        WITH scope AS ({GOLD_RACE_SCOPE})
        SELECT r.session_key, r.driver_number, r.team_name,
               r.driver_full_name AS full_name,
               r.year, r.circuit_short_name,
               s.meeting_name, s.circuit_type, s.session_date_start AS date_start,
               r.grid_position,
               r.grid_lap_duration AS quali_lap,
               r."position"        AS finish_position,
               r.dnf, r.dns, r.dsq, r.points,
               r.gap_to_leader_laps
        FROM scope
        JOIN gold_session_result r ON r.session_key = scope.session_key
        JOIN gold_session s        ON s.session_key = scope.session_key
    """, gold)
    df = normalize_teams(df)

    pace = (laps.groupby(["session_key", "driver_number"])
                .agg(mean_clean_lap=("lap_duration", "mean"),
                     clean_laps=("lap_duration", "size"))
                .reset_index())
    df = df.merge(pace, on=["session_key", "driver_number"], how="left")

    # A driver who retired on lap 5 has a mean lap, but it is not a race pace.
    # Measured: 110 driver-races have fewer than 25 clean laps, and their
    # pace_vs_median reaches +40s, which triples the spread of the whole
    # variable (sd 0.93 -> 2.24) and swamps every regression that uses it.
    #
    # The threshold is half the race distance rather than a fixed lap count,
    # so it travels to a 44-lap Spa and a 78-lap Monaco alike. Rows are kept —
    # DNF and result analyses still need them — but their pace reads NULL.
    race_laps = laps.groupby("session_key")["lap_number"].max().rename("race_laps")
    df = df.merge(race_laps, on="session_key", how="left")
    df["pace_is_representative"] = (df["clean_laps"] >= 0.5 * df["race_laps"])
    df.loc[~df["pace_is_representative"].fillna(False), "mean_clean_lap"] = np.nan

    # Session-median normalisation — raw lap times are confounded by circuit
    # mix, so nothing cross-circuit may use them raw (NOTES_LOG #35). Computed
    # after the mask so a handful of retirements cannot drag the baseline.
    df["session_median_lap"] = df.groupby("session_key")["mean_clean_lap"].transform("median")
    df["pace_vs_median"] = df["mean_clean_lap"] - df["session_median_lap"]

    # lane_duration is not carried in gold: it was byte-identical to
    # pit_duration across all 22,898 populated rows, maximum absolute difference
    # 0.0, so the column name changes and the numbers cannot.
    pits = pd.read_sql(f"""
        WITH scope AS ({GOLD_RACE_SCOPE})
        SELECT p.session_key, p.driver_number,
               COUNT(*) AS pit_count,
               AVG(p.pit_duration) AS mean_lane_duration,
               MIN(p.lap_number) AS first_pit_lap
        FROM scope JOIN gold_pit p ON p.session_key = scope.session_key
        GROUP BY p.session_key, p.driver_number
    """, gold)
    df = df.merge(pits, on=["session_key", "driver_number"], how="left")

    df["position_change"] = df["grid_position"] - df["finish_position"]
    df["classified"] = ((df["dnf"] == 0) & (df["dns"] == 0) & (df["dsq"] == 0)
                        & df["finish_position"].notna()).astype(int)
    df["lapped"] = (df["gap_to_leader_laps"].fillna(0) >= 1).astype(int)
    return df


def load_race_weather(gold) -> pd.DataFrame:
    """
    Per race: did it rain at all, and what share of samples were wet.

    rainfall is a 0/1 state per sample, not millimetres (NOTES_LOG #17), so it
    can be averaged but never summed as an amount.
    """
    return pd.read_sql(f"""
        WITH scope AS ({GOLD_RACE_SCOPE})
        SELECT w.session_key,
               MAX(w.rainfall)                          AS race_had_rain,
               1.0 * SUM(w.rainfall) / COUNT(*)         AS pct_samples_wet
        FROM scope JOIN gold_weather w ON w.session_key = scope.session_key
        GROUP BY w.session_key
    """, gold)


def teammate_pairs(df: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    """
    Self-join to one row per (race, teammate pair), a-minus-b deltas.

    driver_number_a < driver_number_b fixes the sign convention so deltas are
    comparable across races. Only genuine two-car pairings are kept.
    """
    keep = ["session_key", "driver_number", "team_name", "full_name", "year"] + value_cols
    base = df[keep].copy()
    merged = base.merge(base, on=["session_key", "team_name", "year"], suffixes=("_a", "_b"))
    merged = merged[merged["driver_number_a"] < merged["driver_number_b"]].copy()
    for c in value_cols:
        merged[f"{c}_delta"] = merged[f"{c}_a"] - merged[f"{c}_b"]
    merged["pair_id"] = (merged["team_name"] + "_"
                         + merged["driver_number_a"].astype(str) + "_"
                         + merged["driver_number_b"].astype(str))
    return merged


# =================================================================================
# Analyses
# =================================================================================

def a01_team_pace_points(d: Diagnostics, ctx) -> None:
    """Session-normalized team pace -> team race points, race and season level."""
    dr = drop_excluded_teams(ctx["driver_race"])

    team_race = (dr.groupby(["session_key", "team_name", "year"])
                   .agg(team_points=("points", "sum"),
                        dnf_count=("dnf", "sum"),
                        pace_vs_median=("pace_vs_median", "mean"))
                   .reset_index()
                   .dropna(subset=["pace_vs_median", "team_points"]))

    preds = ["pace_vs_median", "dnf_count"]
    fit = smf.ols("team_points ~ pace_vs_median + dnf_count", data=team_race).fit()
    std = standardized_coefficients(team_race, "team_points", preds)
    vifs = compute_vifs(team_race, preds)

    d.add_test(
        "T01", "championship", "How much of a team's race points is explained by pace?",
        "OLS: team_points ~ pace_vs_median + dnf_count",
        fit.fvalue, fit.f_pvalue, fit.rsquared, "r_squared", int(fit.nobs),
        fit.f_pvalue < ALPHA,
        f"Session-normalized race pace explains {fit.rsquared:.1%} of race-level team "
        f"points; each 1s/lap slower than the session median costs "
        f"{abs(fit.params['pace_vs_median']):.1f} points per race.",
        "Pace and reliability are both proxies for car quality, so their "
        "coefficients cannot be read as independent causes.",
    )
    d.add_coefficients("T01", "race_level", fit, std, vifs)
    d.add_points("T01", team_race.assign(_x=team_race["pace_vs_median"],
                                         _y=team_race["team_points"]),
                 "_x", "_y", label="team_name", group_name="team_name")

    # Season level: aggregation smooths race-to-race noise, which is why the
    # same relationship reads stronger here despite n dropping to ~40.
    season = (team_race.groupby(["team_name", "year"])
                       .agg(season_points=("team_points", "sum"),
                            mean_pace=("pace_vs_median", "mean"),
                            total_dnfs=("dnf_count", "sum"),
                            races=("session_key", "nunique"))
                       .reset_index())
    season["dnf_rate"] = season["total_dnfs"] / season["races"]

    fit_s = smf.ols("season_points ~ mean_pace + dnf_rate", data=season).fit()
    std_s = standardized_coefficients(season, "season_points", ["mean_pace", "dnf_rate"])
    vif_s = compute_vifs(season, ["mean_pace", "dnf_rate"])

    d.add_test(
        "T01b", "championship", "Does the pace-points relationship hold at season level?",
        "OLS: season_points ~ mean_pace + dnf_rate",
        fit_s.fvalue, fit_s.f_pvalue, fit_s.rsquared, "r_squared", int(fit_s.nobs),
        fit_s.f_pvalue < ALPHA,
        f"At season level pace explains {fit_s.rsquared:.1%} of constructor points, "
        f"and DNF rate adds nothing detectable once pace is controlled.",
        f"n={int(fit_s.nobs)} team-seasons is small; the DNF coefficient is "
        f"estimated too imprecisely to interpret.",
    )
    d.add_coefficients("T01b", "season_level", fit_s, std_s, vif_s)


def a02_teammate_decomposition(d: Diagnostics, ctx) -> None:
    """Which factor explains the points gap between teammates?"""
    dr = drop_excluded_teams(ctx["driver_race"])
    pairs = teammate_pairs(dr, ["points", "pace_vs_median", "quali_lap", "dnf", "pit_count"])

    pairs = pairs.rename(columns={
        "points_delta": "points_delta",
        "pace_vs_median_delta": "pace_delta",
        "quali_lap_delta": "quali_delta",
        "dnf_delta": "reliability_delta",
        "pit_count_delta": "pit_delta",
    })
    preds = ["pace_delta", "quali_delta", "reliability_delta", "pit_delta"]
    clean = pairs.dropna(subset=["points_delta"] + preds).copy()

    # A teammate quali gap of 5s+ is not a pace difference, it is a red-flagged
    # or aborted session leaking through the grid table. Bound derived from the
    # physical implausibility, not tuned.
    n_implausible = int((clean["quali_delta"].abs() > 5).sum())
    clean = clean[clean["quali_delta"].abs() <= 5].copy()

    fit = smf.ols("points_delta ~ " + " + ".join(preds), data=clean).fit()
    std = standardized_coefficients(clean, "points_delta", preds)
    vifs = compute_vifs(clean, preds)

    ranked = sorted(std.items(), key=lambda kv: -abs(kv[1]))
    order = " > ".join(f"{k.replace('_delta','')} ({abs(v):.2f})" for k, v in ranked)

    d.add_test(
        "T02", "teammates",
        "Which factor explains the most of the points gap between teammates?",
        "OLS on teammate deltas, standardized coefficients for relative importance",
        fit.fvalue, fit.f_pvalue, fit.rsquared, "r_squared", int(fit.nobs),
        fit.f_pvalue < ALPHA,
        f"Ranked by standardized coefficient: {order}.",
        f"{n_implausible} pairs dropped for |quali_delta| > 5s (session mismatch, "
        f"not pace). R²={fit.rsquared:.3f} — most of the teammate gap is race "
        f"randomness these four predictors do not capture.",
    )
    d.add_coefficients("T02", "teammate_points_gap", fit, std, vifs)


def a03_grid_to_finish(d: Diagnostics, ctx) -> None:
    """Grid position -> finish position, the strongest single race-level predictor."""
    dr = ctx["driver_race"]
    df = dr[(dr["classified"] == 1) & dr["grid_position"].notna()].copy()

    res = stats.linregress(df["grid_position"], df["finish_position"])
    d.add_test(
        "T03", "grid", "How strongly does grid position predict finishing position?",
        "simple linear regression: finish_position ~ grid_position",
        res.slope, res.pvalue, res.rvalue ** 2, "r_squared", len(df),
        res.pvalue < ALPHA,
        f"Grid position explains {res.rvalue**2:.1%} of finishing position; each "
        f"grid place further back costs {res.slope:.3f} finishing places on average.",
        "Classified finishers only — DNF/DNS/DSQ excluded, so this is the clean-race "
        "effect and understates how much a bad grid slot matters overall.",
    )
    d.coefficients.append({
        "test_id": "T03", "model": "grid_to_finish", "predictor": "grid_position",
        "coefficient": _round(res.slope, 4), "std_coefficient": _round(res.rvalue, 4),
        "std_error": _round(res.stderr, 4), "p_value": _round(res.pvalue, 6),
        "ci_lower": _round(res.slope - 1.96 * res.stderr, 4),
        "ci_upper": _round(res.slope + 1.96 * res.stderr, 4), "vif": None,
    })
    d.add_points("T03", df, "grid_position", "finish_position",
                 label="full_name", group_name="team_name")


def a04_grid_circuit_interaction(d: Diagnostics, ctx) -> None:
    """Does grid position matter more on street circuits? (interaction term)"""
    dr = ctx["driver_race"]
    df = dr[(dr["classified"] == 1) & dr["grid_position"].notna()
            & dr["circuit_type"].notna()].copy()

    # Temporary - Road is a handful of meetings; too thin to carry a slope.
    counts = df.groupby("circuit_type")["session_key"].nunique()
    thin = counts[counts < 5].index.tolist()
    df = df[~df["circuit_type"].isin(thin)].copy()

    fit = smf.ols('finish_position ~ grid_position * C(circuit_type)', data=df).fit()
    inter = [p for p in fit.params.index if ":" in p]
    p_inter = float(fit.pvalues[inter[0]]) if inter else np.nan

    d.add_test(
        "T04", "grid", "Does grid position's predictive power vary by circuit type?",
        "OLS with interaction: finish_position ~ grid_position * C(circuit_type)",
        fit.fvalue, p_inter, fit.rsquared, "r_squared", int(fit.nobs),
        p_inter < ALPHA,
        ("The grid-to-finish slope differs significantly by circuit type."
         if p_inter < ALPHA else
         "The grid-to-finish slope does not differ significantly by circuit type — "
         "street circuits do not punish a poor grid slot more heavily."),
        (f"Circuit types with fewer than 5 races excluded: {thin}. " if thin else "")
        + "A binary street/permanent split may hide circuit-specific effects; "
          "Monaco is pooled with faster street tracks.",
    )
    d.add_coefficients("T04", "grid_x_circuit", fit)

    for ctype, g in df.groupby("circuit_type"):
        sub = stats.linregress(g["grid_position"], g["finish_position"])
        d.add_group("T04", "grid_to_finish_slope", "circuit_type", ctype,
                    sub.slope, len(g))


def a05_stop_strategy_ancova(d: Diagnostics, ctx) -> None:
    """One-stop vs two-stop, controlling for grid position."""
    dr = ctx["driver_race"]
    df = dr[(dr["classified"] == 1) & dr["grid_position"].notna()
            & dr["pit_count"].notna()].copy()
    df["positions_gained"] = df["grid_position"] - df["finish_position"]

    # 4+ stop races are wet/red-flag chaos rather than a strategy choice, so
    # they are collapsed into one "3+" bucket instead of being modelled apart.
    df["pit_group"] = df["pit_count"].clip(upper=3).astype(int)

    groups = [g["positions_gained"].values for _, g in df.groupby("pit_group")]
    f_naive, p_naive = stats.f_oneway(*groups)

    d.add_test(
        "T05a", "strategy", "Do raw strategy outcomes differ by stop count?",
        "one-way ANOVA: positions_gained ~ pit_group",
        f_naive, p_naive, eta_squared(groups), "eta_squared", len(df),
        p_naive < ALPHA,
        ("Raw positions gained differ by stop count." if p_naive < ALPHA else
         "Raw positions gained do not differ by stop count — but this comparison is "
         "confounded, because multi-stop strategies are run disproportionately by "
         "cars starting further back, which have more places available to gain."),
        "Naive stage — see T05b, which controls for grid position.",
    )

    fit = smf.ols("positions_gained ~ C(pit_group) + grid_position", data=df).fit()
    ref = int(df["pit_group"].min())
    terms = [p for p in fit.params.index if p.startswith("C(pit_group)")]
    p_strategy = float(min(fit.pvalues[terms])) if terms else np.nan

    d.add_test(
        "T05b", "strategy",
        "Is there a real advantage to fewer stops once grid position is controlled?",
        "ANCOVA: positions_gained ~ C(pit_group) + grid_position",
        fit.fvalue, p_strategy, fit.rsquared, "r_squared", int(fit.nobs),
        p_strategy < ALPHA,
        "Controlling for grid position reveals a stop-count effect that the raw "
        f"comparison hid: relative to a {ref}-stop, extra stops cost "
        + ", ".join(f"{abs(fit.params[t]):.2f} places ({t.split('.')[-1].rstrip(']')}-stop)"
                    for t in terms) + ".",
        "positions_gained rewards starting further back, which is exactly why the "
        "grid covariate is mandatory here.",
    )
    d.add_coefficients("T05b", "stop_strategy_ancova", fit,
                       vifs=compute_vifs(df, ["pit_group", "grid_position"]))

    for grp, g in df.groupby("pit_group"):
        lo, hi = mean_ci(g["positions_gained"])
        label = f"{grp}-stop" if grp < 3 else "3+-stop"
        d.add_group("T05b", "positions_gained", "pit_group", label,
                    g["positions_gained"].mean(), len(g), lo, hi)


def a06_overtake_conversion(d: Diagnostics, ctx) -> None:
    """
    Event-level logistic: does a close-following moment convert to a pass?

    Race-level averages cannot answer this — aggregating to per-race totals
    averages the effect away entirely (NOTES_LOG #38). One row per (driver, lap)
    where the driver was within 2s of the car ahead at the start of the lap.
    """
    con = ctx["con"]
    laps = ctx["clean_laps"]

    lap_times = laps[["session_key", "driver_number", "lap_number", "date_start"]].dropna().copy()
    lap_times["date_start"] = pd.to_datetime(lap_times["date_start"], format="ISO8601", utc=True)

    pos = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT p.session_key, p.driver_number, p."date", p."position"
        FROM scope JOIN silver_position p ON p.session_key = scope.session_key
    """, con)
    pos["date"] = pd.to_datetime(pos["date"], format="ISO8601", utc=True)

    # Position held at the moment each lap began.
    opp = pd.merge_asof(
        lap_times.sort_values("date_start"),
        pos.sort_values("date").rename(columns={"date": "pos_date"}),
        left_on="date_start", right_on="pos_date",
        by=["session_key", "driver_number"], direction="backward",
    ).dropna(subset=["position"])

    # Who was directly ahead: the driver holding position-1 on the same lap.
    ahead_key = opp[["session_key", "lap_number", "position", "driver_number"]].rename(
        columns={"position": "ahead_position", "driver_number": "driver_ahead"})
    opp["ahead_position"] = opp["position"] - 1
    opp = opp.merge(ahead_key, on=["session_key", "lap_number", "ahead_position"], how="inner")

    # Gap to the car ahead at that moment.
    intervals = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT i.session_key, i.driver_number, i."date", i.interval_seconds
        FROM scope JOIN silver_intervals i ON i.session_key = scope.session_key
        WHERE i.interval_seconds IS NOT NULL
    """, con)
    intervals["date"] = pd.to_datetime(intervals["date"], format="ISO8601", utc=True)

    opp = pd.merge_asof(
        opp.sort_values("date_start"),
        intervals.sort_values("date").rename(columns={"date": "gap_date",
                                                      "interval_seconds": "gap_to_ahead"}),
        left_on="date_start", right_on="gap_date",
        by=["session_key", "driver_number"], direction="nearest",
    ).dropna(subset=["gap_to_ahead"])

    # An "opportunity" is being within 2s — outside that there is nothing to convert.
    opp = opp[opp["gap_to_ahead"] < 2.0].copy()

    # Tyre age for both cars, from the stint range join.
    stints = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT st.session_key, st.driver_number, st.lap_start, st.lap_end,
               st.tyre_age_at_start
        FROM scope JOIN silver_stints st ON st.session_key = scope.session_key
        WHERE st.lap_end >= st.lap_start
    """, con)

    def attach_age(frame, driver_col, out_col):
        m = frame.merge(stints, left_on=["session_key", driver_col],
                        right_on=["session_key", "driver_number"],
                        how="left", suffixes=("", "_st"))
        inside = (m["lap_number"] >= m["lap_start"]) & (m["lap_number"] <= m["lap_end"])
        m = m[inside].copy()
        m[out_col] = m["tyre_age_at_start"] + (m["lap_number"] - m["lap_start"])
        cols = list(frame.columns) + [out_col]
        return m[cols].drop_duplicates(subset=["session_key", "driver_number", "lap_number"])

    opp = attach_age(opp, "driver_number", "driver_tyre_age")
    opp = attach_age(opp, "driver_ahead", "ahead_tyre_age")
    opp["tyre_delta"] = opp["driver_tyre_age"] - opp["ahead_tyre_age"]
    opp = opp.dropna(subset=["tyre_delta"])

    # Outcome: did this driver pass that specific car within the next 2 laps?
    overtakes = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT o.session_key, o.overtaking_driver_number AS driver_number,
               o.overtaken_driver_number AS driver_ahead, o."date"
        FROM scope JOIN silver_overtakes o ON o.session_key = scope.session_key
    """, con)
    overtakes["date"] = pd.to_datetime(overtakes["date"], format="ISO8601", utc=True)

    # Window end = start of lap+2 for the same driver.
    win = lap_times.rename(columns={"lap_number": "_ln", "date_start": "window_end"})
    win["_ln"] = win["_ln"] - 2
    opp = opp.merge(win, left_on=["session_key", "driver_number", "lap_number"],
                    right_on=["session_key", "driver_number", "_ln"], how="left")
    opp["window_end"] = opp["window_end"].fillna(opp["date_start"] + pd.Timedelta(minutes=4))

    cand = opp.merge(overtakes, on=["session_key", "driver_number", "driver_ahead"],
                     how="left", suffixes=("", "_ot"))
    hit = ((cand["date"] >= cand["date_start"]) & (cand["date"] <= cand["window_end"]))
    cand["hit"] = hit.fillna(False).astype(int)
    outcome = (cand.groupby(["session_key", "driver_number", "lap_number"])["hit"]
                   .max().rename("overtook").reset_index())
    opp = opp.merge(outcome, on=["session_key", "driver_number", "lap_number"], how="left")
    opp["overtook"] = opp["overtook"].fillna(0).astype(int)

    model = smf.logit("overtook ~ gap_to_ahead + tyre_delta", data=opp).fit(disp=0)
    rate = opp["overtook"].mean()

    d.add_test(
        "T06", "overtaking", "What predicts whether a close-following moment converts?",
        "logistic regression: overtook ~ gap_to_ahead + tyre_delta (opportunities within 2s)",
        model.llr, model.llr_pvalue, model.prsquared, "pseudo_r_squared", int(model.nobs),
        model.llr_pvalue < ALPHA,
        f"Overtakes are gap-driven: conversion rate is {rate:.1%}, and each extra "
        f"second of gap reduces the log-odds of a pass by "
        f"{abs(model.params['gap_to_ahead']):.2f}. Fresher tyres help, with a much "
        f"smaller per-unit effect over a much wider range.",
        "silver_overtakes includes pit-cycle and penalty position changes, not only "
        "on-track passes (NOTES_LOG #20), so conversion is an upper bound.",
    )
    d.add_coefficients("T06", "overtake_logit", model,
                       vifs=compute_vifs(opp, ["gap_to_ahead", "tyre_delta"]))
    ctx["opportunities"] = opp


def a07_pit_lane_by_team(d: Diagnostics, ctx) -> None:
    """
    Pit lane duration by team, and whether a slow stop costs track position.

    Uses lane_duration throughout. stop_duration is unusable — 0.0/1.4/7.9/3.3%
    coverage by year (NOTES_LOG #10) — so any finding built on it rested on
    almost no data.
    """
    con = ctx["con"]
    pits = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT p.session_key, p.driver_number, p.lap_number, p.lane_duration,
               d.team_name, m.circuit_short_name
        FROM scope
        JOIN silver_pit p ON p.session_key = scope.session_key
        JOIN silver_sessions s ON s.session_key = scope.session_key
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        JOIN silver_drivers d
          ON d.session_key = p.session_key AND d.driver_number = p.driver_number
        WHERE p.lane_duration IS NOT NULL
    """, con)
    pits = drop_excluded_teams(normalize_teams(pits))

    # Tukey fence derived fresh on this sample, never hardcoded (NOTES_LOG #30).
    q1, q3 = pits["lane_duration"].quantile([0.25, 0.75])
    fence = q3 + 1.5 * (q3 - q1)
    clean = pits[pits["lane_duration"] <= fence].copy()
    n_disaster = len(pits) - len(clean)

    groups = [g["lane_duration"].values for _, g in clean.groupby("team_name")]
    f_stat, p_val = stats.f_oneway(*groups)

    d.add_test(
        "T07a", "pit_stops", "Does pit lane duration differ by team?",
        f"one-way ANOVA on lane_duration, Tukey fence {fence:.2f}s derived fresh",
        f_stat, p_val, eta_squared(groups), "eta_squared", len(clean),
        p_val < ALPHA,
        ("Pit lane duration differs significantly by team, a real crew/pit-lane effect."
         if p_val < ALPHA else
         "Pit lane duration does not differ significantly by team."),
        f"{n_disaster} stops above the {fence:.2f}s fence excluded as disasters. "
        f"lane_duration used because stop_duration coverage is 0-8% by year.",
    )
    for team, g in clean.groupby("team_name"):
        lo, hi = mean_ci(g["lane_duration"])
        d.add_group("T07a", "mean_lane_duration", "team", team,
                    g["lane_duration"].mean(), len(g), lo, hi)

    # Does a slow stop actually cost position?
    dr = ctx["driver_race"]
    merged = clean.merge(
        dr[["session_key", "driver_number", "position_change", "grid_position"]],
        on=["session_key", "driver_number"], how="inner").dropna(
        subset=["position_change", "grid_position"])

    # Grid position is in the model because without it the lane-duration term
    # picks up "backmarkers pit slower and also gain places", not the stop.
    fit = smf.ols("position_change ~ lane_duration + grid_position", data=merged).fit()
    only_grid = smf.ols("position_change ~ grid_position", data=merged).fit()
    delta_r2 = fit.rsquared - only_grid.rsquared

    d.add_test(
        "T07b", "pit_stops", "Does a slow pit stop cost track position?",
        "OLS: position_change ~ lane_duration + grid_position",
        fit.fvalue, fit.pvalues.get("lane_duration", np.nan), delta_r2,
        "delta_r_squared_vs_grid_only", int(fit.nobs),
        fit.pvalues.get("lane_duration", 1) < ALPHA,
        f"A slower stop does cost position and the effect is statistically clear "
        f"({fit.params['lane_duration']:+.3f} places per extra second in the lane), "
        f"but it is tiny: adding lane duration to a grid-only model raises R² by "
        f"just {delta_r2:.3f}, from {only_grid.rsquared:.3f} to {fit.rsquared:.3f}.",
        "Almost all of this model's R² is the grid term, not the stop. The dominant "
        "unmeasured factor is whether the car ahead also pitted on the same lap — "
        "the undercut/overcut dynamic, which needs cross-driver encoding.",
    )
    d.add_coefficients("T07b", "slow_stop_position", fit,
                       vifs=compute_vifs(merged, ["lane_duration", "grid_position"]))


def a08_lapping_by_team(d: Diagnostics, ctx) -> None:
    """Lapping rate by team — a pace story, not a reliability one."""
    dr = drop_excluded_teams(ctx["driver_race"])
    df = dr[dr["classified"] == 1].copy()

    table = pd.crosstab(df["team_name"], df["lapped"])
    chi2, p, dof, expected = stats.chi2_contingency(table)

    rates = df.groupby("team_name")["lapped"].agg(["mean", "size"]).sort_values("mean")
    d.add_test(
        "T08", "pace", "Does being lapped depend on which team you drive for?",
        "chi-square: team_name x lapped (classified finishers only)",
        chi2, p, cramers_v(chi2, table), "cramers_v", len(df), p < ALPHA,
        f"Lapping rate is strongly team-specific, from {rates['mean'].iloc[0]:.1%} "
        f"({rates.index[0]}) to {rates['mean'].iloc[-1]:.1%} ({rates.index[-1]}).",
        "Classified finishers only by construction: a car that retires never gets a "
        "lapped classification, so this measures pace deficit, not reliability.",
    )
    for team, row in rates.iterrows():
        d.add_group("T08", "lapping_rate", "team", team, row["mean"], int(row["size"]))


def a09_dnf_by_team(d: Diagnostics, ctx) -> None:
    """DNF rate by team, plus the Sainz natural experiment."""
    dr = drop_excluded_teams(ctx["driver_race"])
    starters = dr[dr["dns"] == 0].copy()

    table = pd.crosstab(starters["team_name"], starters["dnf"])
    chi2, p, dof, expected = stats.chi2_contingency(table)
    rates = starters.groupby("team_name")["dnf"].agg(["mean", "size"]).sort_values("mean")

    d.add_test(
        "T09", "reliability", "Are some teams' cars statistically more fragile?",
        "chi-square: team_name x dnf (all race starters)",
        chi2, p, cramers_v(chi2, table), "cramers_v", len(starters), p < ALPHA,
        f"DNF rate is team-specific, from {rates['mean'].iloc[0]:.1%} "
        f"({rates.index[0]}) to {rates['mean'].iloc[-1]:.1%} ({rates.index[-1]}).",
        None,
    )
    for team, row in rates.iterrows():
        d.add_group("T09", "dnf_rate", "team", team, row["mean"], int(row["size"]))

    # Driver-level rates are reported descriptively: detecting a team-sized
    # effect per driver needs far more starts than any driver has here.
    per_driver = (starters.groupby(["full_name", "team_name"])["dnf"]
                          .agg(["mean", "size"]).reset_index())
    per_driver = per_driver[per_driver["size"] >= 20]
    for _, r in per_driver.iterrows():
        d.add_group("T09", "dnf_rate", "driver_team", f"{r['full_name']} ({r['team_name']})",
                    r["mean"], int(r["size"]))

    # The natural experiment: same driver, two cars. This is the cleanest
    # available evidence that fragility follows the car.
    sainz = starters[starters["full_name"].str.contains("SAINZ", case=False, na=False)]
    by_team = sainz.groupby("team_name")["dnf"].agg(["mean", "size", "sum"])
    by_team = by_team[by_team["size"] >= 20].sort_values("mean")

    if len(by_team) >= 2:
        lo_team, hi_team = by_team.index[0], by_team.index[-1]
        a = sainz[sainz["team_name"] == lo_team]["dnf"]
        b = sainz[sainz["team_name"] == hi_team]["dnf"]
        chi_tab = pd.crosstab(sainz[sainz["team_name"].isin([lo_team, hi_team])]["team_name"],
                              sainz[sainz["team_name"].isin([lo_team, hi_team])]["dnf"])
        chi2_s, p_s, _, exp_s = stats.chi2_contingency(chi_tab)
        thin = bool((exp_s < 5).sum())

        d.add_test(
            "T09b", "reliability",
            "Does one driver's DNF rate change with the car? (Sainz natural experiment)",
            "chi-square on two-team contingency for a single driver",
            chi2_s, p_s, cramers_v(chi2_s, chi_tab), "cramers_v", len(a) + len(b),
            False,
            f"Carlos Sainz recorded {a.mean():.1%} DNFs at {lo_team} "
            f"({int(a.sum())}/{len(a)}) and {b.mean():.1%} at {hi_team} "
            f"({int(b.sum())}/{len(b)}) — the same driver, different cars, with each "
            f"rate close to the team's own.",
            "DESCRIPTIVE ONLY. A single-driver comparison at n≈"
            f"{min(len(a), len(b))} per team cannot reach significance"
            + (" and has expected cells below 5" if thin else "")
            + "; it is corroborating evidence for the team-level result, not a test.",
        )
        for team, row in by_team.iterrows():
            d.add_group("T09b", "dnf_rate", "sainz_team", team, row["mean"], int(row["size"]))


def a10_wet_advantage(d: Diagnostics, ctx) -> None:
    """
    Wet-weather advantage by team and driver. Descriptive only.

    wet_advantage = dry_mean - wet_mean on position_change. Negative means the
    entity gains MORE places in the wet. Two limits are recorded rather than
    worked around: the sample is far below the power threshold, and the metric
    is confounded by typical grid slot.
    """
    dr = drop_excluded_teams(ctx["driver_race"])
    df = dr[(dr["classified"] == 1) & dr["position_change"].notna()].copy()
    df = df.merge(ctx["weather"], on="session_key", how="left")
    df["race_had_rain"] = df["race_had_rain"].fillna(0).astype(int)

    n_wet_races = int(df[df["race_had_rain"] == 1]["session_key"].nunique())
    # Detecting a two-position advantage needs roughly this many wet races per
    # entity; the project has been short of it since the diagnostic phase.
    needed = required_n_per_group(effect_size=2.0 / df["position_change"].std())

    for group_type, key in (("team", "team_name"), ("driver", "full_name")):
        piv = (df.groupby([key, "race_had_rain"])["position_change"]
                 .agg(["mean", "size"]).unstack("race_had_rain"))
        piv = piv.dropna()
        if piv.empty:
            continue
        for name, row in piv.iterrows():
            dry_mean, wet_mean = row[("mean", 0)], row[("mean", 1)]
            wet_n = int(row[("size", 1)])
            if wet_n < 8:
                continue
            d.add_group("T10", "wet_advantage", group_type, str(name),
                        dry_mean - wet_mean, wet_n)

    d.add_test(
        "T10", "weather",
        "Do specific teams or drivers outperform their own dry-weather baseline in the wet?",
        "difference of means on position_change, wet vs dry (no significance test)",
        None, None, None, None, len(df), False,
        f"Wet-weather advantage varies across teams and drivers, but with only "
        f"{n_wet_races} wet races in the data the differences cannot be "
        f"distinguished from noise.",
        f"DESCRIPTIVE ONLY. Detecting a two-position effect at 80% power needs "
        f"about {needed:.0f} wet races per entity against {n_wet_races} available. "
        f"The metric is also confounded by typical grid position: cars starting at "
        f"the back have more places available to gain in any conditions.",
    )


def a11_tyre_degradation(d: Diagnostics, ctx) -> None:
    """
    Degradation slope per stint, compared across compounds.

    A pooled model with interaction terms produces a physically impossible
    inverted-U, because it conflates within-stint and between-stint variance.
    One regression per stint, then ANOVA on the slopes, is the decomposition
    the question actually asks for (NOTES_LOG #38).
    """
    con = ctx["con"]
    laps = ctx["clean_laps"]

    stints = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT st.session_key, st.driver_number, st.stint_number,
               st.lap_start, st.lap_end, st.compound, st.tyre_age_at_start
        FROM scope JOIN silver_stints st ON st.session_key = scope.session_key
        WHERE st.lap_end >= st.lap_start
          AND st.compound IS NOT NULL
          AND st.compound NOT IN ('UNKNOWN', 'TEST_UNKNOWN')
    """, con)

    m = laps.merge(stints, on=["session_key", "driver_number"], how="inner")
    m = m[(m["lap_number"] >= m["lap_start"]) & (m["lap_number"] <= m["lap_end"])].copy()
    m = m.drop_duplicates(subset=["session_key", "driver_number", "lap_number"], keep="first")
    m["tyre_age"] = m["tyre_age_at_start"] + (m["lap_number"] - m["lap_start"])
    m = drop_excluded_teams(m)

    # FUEL CORRECTION — the reason this is not a plain per-stint slope.
    #
    # Within one stint, tyre_age = tyre_age_at_start + (lap_number - lap_start).
    # Those two differ by a constant, so they are perfectly collinear and NO
    # per-stint model can separate them. A raw per-stint slope therefore
    # measures degradation MINUS fuel burn, not degradation, and fuel burn wins:
    # measured here at about -0.06 s/lap, enough to turn a genuinely degrading
    # compound negative.
    #
    # Across stints the collinearity breaks — tyre_age resets at each stop while
    # lap_number keeps climbing — so a session-level fit CAN identify both. The
    # fuel term is estimated there and subtracted before the per-stint slopes
    # are taken. The uncorrected slopes are still reported alongside, because
    # they are what the original notebook measured.
    fuel_rows = []
    for sk, g in m.groupby("session_key"):
        if g["lap_number"].nunique() < 10 or g["tyre_age"].nunique() < 10:
            continue
        try:
            fit_f = smf.ols("lap_duration ~ lap_number + tyre_age", data=g).fit()
            fuel_rows.append({"session_key": sk, "fuel_per_lap": fit_f.params["lap_number"]})
        except Exception:
            continue
    fuel = pd.DataFrame(fuel_rows)
    m = m.merge(fuel, on="session_key", how="left")
    m["fuel_per_lap"] = m["fuel_per_lap"].fillna(0.0)
    m["lap_fuel_corrected"] = m["lap_duration"] - m["fuel_per_lap"] * m["lap_number"]

    # A slope from fewer than 8 laps is noise, not a degradation rate.
    rows = []
    for (sk, dn, sn), g in m.groupby(["session_key", "driver_number", "stint_number"]):
        if len(g) < 8:
            continue
        res = stats.linregress(g["tyre_age"], g["lap_fuel_corrected"])
        raw = stats.linregress(g["tyre_age"], g["lap_duration"])
        rows.append({"session_key": sk, "driver_number": dn, "stint_number": sn,
                     "compound": g["compound"].iloc[0], "team_name": g["team_name"].iloc[0],
                     "slope": res.slope, "slope_uncorrected": raw.slope, "n_laps": len(g)})
    slopes = pd.DataFrame(rows)

    q1, q3 = slopes["slope"].quantile([0.25, 0.75])
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    trimmed = slopes[(slopes["slope"] >= lo) & (slopes["slope"] <= hi)].copy()
    n_out = len(slopes) - len(trimmed)

    # Compounds with a handful of stints cannot carry a mean.
    counts = trimmed["compound"].value_counts()
    usable = counts[counts >= 20].index.tolist()
    sub = trimmed[trimmed["compound"].isin(usable)].copy()

    groups = [g["slope"].values for _, g in sub.groupby("compound")]
    f_stat, p_val = stats.f_oneway(*groups)
    means = sub.groupby("compound")["slope"].agg(["mean", "size"]).sort_values("mean")

    d.add_test(
        "T11a", "tyres", "Does tyre degradation rate differ by compound?",
        f"fuel-corrected per-stint OLS slope (min 8 laps), one-way ANOVA across "
        f"compounds, Tukey fence [{lo:.3f}, {hi:.3f}]",
        f_stat, p_val, eta_squared(groups), "eta_squared", len(sub), p_val < ALPHA,
        "Degradation rate differs by compound — "
        + ", ".join(f"{c} {r['mean']:+.3f}s/lap" for c, r in means.iterrows())
        + " — but NOT in the soft-to-hard order the compound design implies: "
          "HARD and SOFT are statistically indistinguishable here and MEDIUM "
          "degrades least. Read this as a measured property of how each compound "
          "was actually used, not as compound chemistry.",
        f"{n_out} outlier stints removed by the fence. "
        + (f"Compounds with <20 stints excluded: {sorted(set(counts.index) - set(usable))}. "
           if set(counts.index) - set(usable) else "")
        + "Slopes are fuel-corrected: tyre age and fuel load are perfectly "
          "collinear within a stint, so an uncorrected slope measures "
          "degradation minus fuel burn. Uncorrected values are reported "
          "alongside for comparison with the original notebook. Compound is "
          "not randomly assigned — teams choose it by expected stint length, "
          "track state and weather — so these means carry a selection effect "
          "that no per-stint model can remove.",
    )
    for compound, r in means.iterrows():
        g = sub[sub["compound"] == compound]["slope"]
        lo_ci, hi_ci = mean_ci(g)
        d.add_group("T11a", "degradation_slope", "compound", compound,
                    r["mean"], int(r["size"]), lo_ci, hi_ci)
        d.add_group("T11a", "degradation_slope_uncorrected", "compound", compound,
                    sub[sub["compound"] == compound]["slope_uncorrected"].mean(),
                    int(r["size"]))

    # Same slopes, grouped by team instead — the contrast is the point.
    tgroups = [g["slope"].values for _, g in sub.groupby("team_name")]
    f_t, p_t = stats.f_oneway(*tgroups)
    tmeans = sub.groupby("team_name")["slope"].agg(["mean", "size"]).sort_values("mean")

    d.add_test(
        "T11b", "tyres", "Does tyre degradation rate differ by team?",
        "per-stint OLS slope, one-way ANOVA across teams",
        f_t, p_t, eta_squared(tgroups), "eta_squared", len(sub), p_t < ALPHA,
        ("Degradation rate differs by team." if p_t < ALPHA else
         "Degradation rate does not differ by team — the spread across constructors "
         "is small relative to the variation within each one."),
        "Compound is not held constant within team here; the compound effect (T11a) "
        "is an order of magnitude larger.",
    )
    for team, r in tmeans.iterrows():
        d.add_group("T11b", "degradation_slope", "team", team, r["mean"], int(r["size"]))


def a12_rain_variance(d: Diagnostics, ctx) -> None:
    """Does rain increase the spread of finishing positions relative to the grid?"""
    dr = ctx["driver_race"]
    df = dr[(dr["classified"] == 1) & dr["position_change"].notna()].copy()
    df = df.merge(ctx["weather"], on="session_key", how="left")
    df["race_had_rain"] = df["race_had_rain"].fillna(0).astype(int)

    per_race = (df.groupby(["session_key", "race_had_rain"])["position_change"]
                  .var().reset_index(name="variance").dropna())
    dry = per_race[per_race["race_had_rain"] == 0]["variance"]
    wet = per_race[per_race["race_had_rain"] == 1]["variance"]

    lev_stat, lev_p = stats.levene(dry, wet)
    t_stat, t_p = stats.ttest_ind(wet, dry, equal_var=False)

    d.add_test(
        "T12", "weather",
        "Does rain increase the variance of finishing positions across the field?",
        "Levene's test on per-race variance of position_change (wet vs dry)",
        lev_stat, lev_p, cohens_d(wet, dry), "cohens_d", len(per_race),
        lev_p < ALPHA,
        f"Wet races show {wet.mean() - dry.mean():+.2f} more variance in "
        f"position change on average, but the difference is not statistically "
        f"detectable (Levene p={lev_p:.3f}, Welch t-test p={t_p:.3f}).",
        f"Only {len(wet)} wet races available — underpowered for a variance "
        f"comparison, and a few chaotic wet races could carry the mean.",
    )
    for label, s in (("dry", dry), ("wet", wet)):
        lo, hi = mean_ci(s)
        d.add_group("T12", "position_change_variance", "condition", label,
                    s.mean(), len(s), lo, hi)


# --- analyses present in the notebooks but not in the original task list ----------

def a13_teammate_quali_deltas(d: Diagnostics, ctx) -> None:
    """
    Teammate qualifying pace deltas, one t-test per pairing.

    Not in the task list, but this is the analysis that established qualifying
    pace as a driver-skill signal, and T02 ranks it the strongest single
    predictor of the teammate points gap — so the dashboard needs the evidence.
    """
    dr = drop_excluded_teams(ctx["driver_race"])
    pairs = teammate_pairs(dr, ["quali_lap"])
    pairs = pairs.dropna(subset=["quali_lap_delta"])

    # Tukey fence on the delta: a 60s "delta" is an aborted lap, not pace.
    q1, q3 = pairs["quali_lap_delta"].quantile([0.25, 0.75])
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    clean = pairs[(pairs["quali_lap_delta"] >= lo) & (pairs["quali_lap_delta"] <= hi)]

    counts = clean.groupby("pair_id").size()
    valid = counts[counts >= MIN_PAIR_SESSIONS].index
    sub = clean[clean["pair_id"].isin(valid)]

    alpha_c = bonferroni(len(valid))
    n_sig = 0
    for pair_id, g in sub.groupby("pair_id"):
        t_stat, p_val = stats.ttest_1samp(g["quali_lap_delta"], 0)
        sig = p_val < alpha_c
        n_sig += int(sig)
        faster = g["full_name_a"].iloc[0] if g["quali_lap_delta"].mean() < 0 else g["full_name_b"].iloc[0]
        d.add_group("T13", "quali_delta_seconds", "teammate_pair",
                    f"{pair_id} | faster: {faster}" if sig else pair_id,
                    g["quali_lap_delta"].mean(), len(g), *mean_ci(g["quali_lap_delta"]))

    d.add_test(
        "T13", "teammates",
        "Are teammate qualifying gaps real driver skill, or race-to-race noise?",
        f"one-sample t-test per pairing vs 0, Bonferroni alpha={alpha_c:.5f} "
        f"({len(valid)} pairings)",
        None, None, n_sig / len(valid) if len(valid) else np.nan,
        "proportion_significant", len(sub), n_sig > 0,
        f"{n_sig} of {len(valid)} teammate pairings show a qualifying pace gap that "
        f"survives Bonferroni correction. Car pace is held constant within a pairing, "
        f"so these isolate a genuine driver effect.",
        f"Pairings with fewer than {MIN_PAIR_SESSIONS} shared qualifying sessions "
        f"excluded as underpowered. Tukey fence [{lo:.3f}, {hi:.3f}] applied to "
        f"remove aborted laps.",
    )


def a14_net_gain_by_team(d: Diagnostics, ctx) -> None:
    """
    Grid-to-finish net gain by team, controlling for grid position.

    Not in the task list. Included because the raw version of this comparison is
    actively misleading — backmarker teams look like the best racers purely
    because they start further back — and the corrected ANCOVA is the version
    the dashboard should show.
    """
    dr = drop_excluded_teams(ctx["driver_race"])
    df = dr[(dr["classified"] == 1) & dr["grid_position"].notna()].copy()
    df["net_gain"] = df["grid_position"] - df["finish_position"]

    groups = [g["net_gain"].values for _, g in df.groupby("team_name")]
    f_raw, p_raw = stats.f_oneway(*groups)

    fit = smf.ols("net_gain ~ C(team_name) + grid_position", data=df).fit()
    terms = [p for p in fit.params.index if p.startswith("C(team_name)")]
    p_team = float(min(fit.pvalues[terms])) if terms else np.nan

    d.add_test(
        "T14", "racecraft",
        "Do some teams race better than they qualify, once grid position is controlled?",
        "ANCOVA: net_gain ~ C(team_name) + grid_position (raw ANOVA reported as the "
        "naive stage)",
        fit.fvalue, p_team, fit.rsquared, "r_squared", int(fit.nobs), p_team < ALPHA,
        "Controlling for grid position separates genuine grid-to-finish conversion "
        "from the floor/ceiling effect that flatters backmarker teams in the raw "
        f"comparison (raw ANOVA F={f_raw:.2f}, p={p_raw:.4f}).",
        "Team coefficients are differences against the alphabetically-first team as "
        "baseline, not against the field average.",
    )
    d.add_coefficients("T14", "net_gain_ancova", fit)

    # Predicted net gain at a fixed reference grid slot, which is the only fair
    # way to rank teams here. Tukey on residuals would be wrong: the ANCOVA has
    # already removed the team means, so every pair would read as zero.
    ref = int(df["grid_position"].median())
    pred = pd.DataFrame({"team_name": sorted(df["team_name"].unique()),
                         "grid_position": ref})
    pred["predicted"] = fit.predict(pred)
    for _, r in pred.iterrows():
        n = int((df["team_name"] == r["team_name"]).sum())
        d.add_group("T14", f"predicted_net_gain_at_P{ref}", "team",
                    r["team_name"], r["predicted"], n)


def a15_position_swings(d: Diagnostics, ctx) -> None:
    """
    Are big lap-to-lap position swings explained by pit stops and neutralisations?

    Not in the task list. Rebuilt on silver_lap_flags rather than the exact-lap
    race-control join the notebook used, and with SC and VSC separated — the
    original conflated them, which is the error s02b was written to fix.
    """
    con = ctx["con"]
    laps = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT l.session_key, l.driver_number, l.lap_number, l.date_start,
               f.sc_flag, f.vsc_flag
        FROM scope
        JOIN silver_laps l ON l.session_key = scope.session_key
        JOIN silver_lap_flags f
          ON  f.session_key = l.session_key
          AND f.driver_number = l.driver_number
          AND f.lap_number = l.lap_number
        WHERE l.date_start IS NOT NULL
    """, con)
    laps["date_start"] = pd.to_datetime(laps["date_start"], format="ISO8601", utc=True)

    pos = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT p.session_key, p.driver_number, p."date", p."position"
        FROM scope JOIN silver_position p ON p.session_key = scope.session_key
    """, con)
    pos["date"] = pd.to_datetime(pos["date"], format="ISO8601", utc=True)

    snap = pd.merge_asof(
        laps.sort_values("date_start"),
        pos.sort_values("date").rename(columns={"date": "pos_date"}),
        left_on="date_start", right_on="pos_date",
        by=["session_key", "driver_number"], direction="backward",
    ).dropna(subset=["position"])

    snap = snap.sort_values(["session_key", "driver_number", "lap_number"])
    snap["next_position"] = snap.groupby(["session_key", "driver_number"])["position"].shift(-1)
    snap["position_swing"] = snap["next_position"] - snap["position"]
    snap = snap.dropna(subset=["position_swing"])

    pit_laps = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT DISTINCT p.session_key, p.driver_number, p.lap_number, 1 AS pit_flag
        FROM scope JOIN silver_pit p ON p.session_key = scope.session_key
    """, con)
    snap = snap.merge(pit_laps, on=["session_key", "driver_number", "lap_number"], how="left")
    snap["pit_flag"] = snap["pit_flag"].fillna(0).astype(int)
    snap["sc_flag"] = snap["sc_flag"].fillna(0).astype(int)
    snap["vsc_flag"] = snap["vsc_flag"].fillna(0).astype(int)

    fit = smf.ols("position_swing ~ pit_flag + sc_flag + vsc_flag", data=snap).fit()
    resid = snap["position_swing"] - fit.predict(snap)
    big = int((resid.abs() >= 3).sum())

    d.add_test(
        "T15", "racecraft",
        "Are the biggest position swings explained by pit cycles and neutralisations?",
        "OLS: position_swing ~ pit_flag + sc_flag + vsc_flag (SC and VSC separated)",
        fit.fvalue, fit.f_pvalue, fit.rsquared, "r_squared", int(fit.nobs),
        fit.f_pvalue < ALPHA,
        f"Pitting costs about {fit.params['pit_flag']:.2f} places on the lap it "
        f"happens, and a Safety Car discounts that. The three flags explain "
        f"{fit.rsquared:.1%} of total swing variance because most laps are quiet, "
        f"not because the model is wrong about the laps that are not.",
        f"{big} laps ({big/len(snap):.1%}) still show a swing of 3+ places "
        f"unexplained by these flags — on-track battles, damage and tyre cliffs.",
    )
    d.add_coefficients("T15", "position_swing", fit,
                       vifs=compute_vifs(snap, ["pit_flag", "sc_flag", "vsc_flag"]))


def a16_lap1_swing(d: Diagnostics, ctx) -> None:
    """
    Do certain grid slots systematically gain or lose places on lap 1?

    Not in the task list; cheap, and it is the descriptive layer's opening
    chapter, so the dashboard's lap-1 panel needs a number behind it.
    """
    con = ctx["con"]
    dr = ctx["driver_race"]

    lap2 = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT l.session_key, l.driver_number, l.date_start AS lap2_date
        FROM scope JOIN silver_laps l ON l.session_key = scope.session_key
        WHERE l.lap_number = 2 AND l.date_start IS NOT NULL
    """, con)
    lap2["lap2_date"] = pd.to_datetime(lap2["lap2_date"], format="ISO8601", utc=True)

    pos = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT p.session_key, p.driver_number, p."date", p."position"
        FROM scope JOIN silver_position p ON p.session_key = scope.session_key
    """, con)
    pos["date"] = pd.to_datetime(pos["date"], format="ISO8601", utc=True)

    after = pd.merge_asof(
        lap2.sort_values("lap2_date"),
        pos.sort_values("date").rename(columns={"date": "pos_date",
                                                "position": "position_after_lap1"}),
        left_on="lap2_date", right_on="pos_date",
        by=["session_key", "driver_number"], direction="backward",
    ).dropna(subset=["position_after_lap1"])

    df = after.merge(dr[["session_key", "driver_number", "grid_position", "full_name"]],
                     on=["session_key", "driver_number"], how="inner").dropna(
        subset=["grid_position"])
    df["lap1_swing"] = df["grid_position"] - df["position_after_lap1"]

    res = stats.linregress(df["grid_position"], df["lap1_swing"])
    d.add_test(
        "T16", "start", "Do certain grid positions systematically gain or lose on lap 1?",
        "simple linear regression: lap1_swing ~ grid_position",
        res.slope, res.pvalue, res.rvalue ** 2, "r_squared", len(df), res.pvalue < ALPHA,
        f"Starting further back means gaining more places on lap 1, but the effect is "
        f"weak — grid position explains only {res.rvalue**2:.1%} of lap-1 swing. Lap 1 "
        f"is dominated by incident and racecraft, not starting slot.",
        "Drivers without a lap 2 are excluded, which silently drops genuine lap-1 "
        "retirements and so understates negative swings.",
    )
    d.add_points("T16", df, "grid_position", "lap1_swing", label="full_name")


def a17_lap1_chaos_by_circuit(d: Diagnostics, ctx) -> None:
    """
    Is lap-1 chaos more frequent at certain circuit types?

    Chaos is measured as "race control logged anything on lap 1", per race.
    Overtake counts are not usable for this: silver_overtakes carries a
    timestamp but no lap number, so isolating lap-1 passes would need the
    lap-2 boundary derived per driver, and the resulting count would still
    include pit-cycle swaps (NOTES_LOG #20).
    """
    con = ctx["con"]

    races = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT scope.session_key, m.circuit_type
        FROM scope
        JOIN silver_sessions s ON s.session_key = scope.session_key
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
    """, con)

    # "Any message on lap 1" is useless as a proxy: every race logs procedural
    # traffic such as GREEN LIGHT - PIT EXIT OPEN, so the rate is 100% for every
    # circuit type and the test has no variance to work with. Restrict to
    # categories that mean something actually happened.
    chaos = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT DISTINCT rc.session_key
        FROM scope JOIN silver_race_control rc ON rc.session_key = scope.session_key
        WHERE rc.lap_number = 1
          AND (rc.category IN ('SafetyCar', 'CarEvent')
               OR (rc.category = 'Flag'
                   AND rc.flag IN ('YELLOW', 'DOUBLE YELLOW', 'RED')))
    """, con)

    races["chaos"] = races["session_key"].isin(chaos["session_key"]).astype(int)

    # Circuit types with too few races cannot support a chi-square cell.
    counts = races["circuit_type"].value_counts()
    thin = counts[counts < 5].index.tolist()
    kept = races[~races["circuit_type"].isin(thin)]

    table = pd.crosstab(kept["circuit_type"], kept["chaos"])
    chi2, p, _, _ = stats.chi2_contingency(table)
    v = cramers_v(chi2, table)

    for ctype, grp in kept.groupby("circuit_type"):
        d.add_group("T17", "lap1_chaos_rate", "circuit_type", ctype,
                    grp["chaos"].mean(), len(grp))

    rates = kept.groupby("circuit_type")["chaos"].mean()
    spread = ", ".join(f"{k} {v_:.1%}" for k, v_ in rates.items())
    d.add_test(
        "T17", "start",
        "Is lap-1 chaos more frequent at certain circuits?",
        "chi-square: circuit_type x any race control message on lap 1",
        chi2, p, v, "cramers_v", len(kept), p < ALPHA,
        f"Lap-1 incident rates are near-identical across circuit types ({spread}), "
        f"so the opening lap is no more eventful on street circuits than on "
        f"permanent ones.",
        (f"Circuit types with fewer than 5 races excluded: {thin}. " if thin else "")
        + "An incident is a yellow, double yellow or red flag, a safety car, or a "
          "car event logged on lap 1. Purely procedural messages are excluded: "
          "counting those puts every race at 100% and leaves nothing to compare. "
          "Overtakes are not included, because the overtake feed carries no lap "
          "number.",
    )


def a18_within_stint_pace(d: Diagnostics, ctx) -> None:
    """
    What explains lap-time variation within a stint?

    The bank specifies lap_duration ~ tyre_age + compound + track_temp +
    lap_number. The outcome here is lap time against the session median
    instead of raw seconds: a raw lap time is dominated by which circuit it
    was set on, which would swamp every other term (NOTES_LOG #35).

    The tyre_age coefficient in this pooled model is NOT a degradation rate,
    and the wording deliberately never calls it one. tyre_age and lap_number
    are collinear by construction inside a stint, so the term is identified
    only from between-stint variation, which carries stint selection with it.
    T11a is the within-stint estimate and the two will not agree. That is
    expected: they estimate different quantities.
    """
    con = ctx["con"]
    laps = ctx["clean_laps"]

    stints = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT st.session_key, st.driver_number, st.stint_number, st.compound,
               st.tyre_age_at_start, st.lap_start, st.lap_end
        FROM scope JOIN silver_stints st ON st.session_key = scope.session_key
        WHERE st.lap_end >= st.lap_start AND st.compound IS NOT NULL
    """, con)

    m = laps.merge(stints, on=["session_key", "driver_number"], how="inner")
    m = m[(m["lap_number"] >= m["lap_start"]) & (m["lap_number"] <= m["lap_end"])].copy()
    m["tyre_age"] = m["tyre_age_at_start"] + (m["lap_number"] - m["lap_start"])

    temp = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT w.session_key, AVG(w.track_temperature) AS track_temperature
        FROM scope JOIN silver_weather w ON w.session_key = scope.session_key
        GROUP BY w.session_key
    """, con)
    m = m.merge(temp, on="session_key", how="left")

    keep = m["compound"].value_counts()
    m = m[m["compound"].isin(keep[keep >= 200].index)]
    m = m.dropna(subset=["lap_vs_median", "tyre_age", "track_temperature",
                         "lap_number"])

    fit = smf.ols(
        "lap_vs_median ~ tyre_age + C(compound) + track_temperature + lap_number",
        data=m).fit()
    vifs = compute_vifs(m, ["tyre_age", "track_temperature", "lap_number"])
    d.add_coefficients("T18", "within_stint_pace", fit, vifs=vifs)

    age = fit.params.get("tyre_age", np.nan)
    age_p = fit.pvalues.get("tyre_age", np.nan)
    age_lo, age_hi = fit.conf_int().loc["tyre_age"]
    fuel = fit.params.get("lap_number", np.nan)
    temp = fit.params.get("track_temperature", np.nan)
    max_vif = max(vifs.values()) if vifs else np.nan

    # Deliberately NOT phrased as "tyre age costs Xs per lap". This term is a
    # between-stint contrast, not a degradation rate; see the caveat.
    age_clause = (
        f"the tyre age term reads {age:+.3f}s per lap of age"
        if age_p < ALPHA else
        f"the tyre age term is indistinguishable from zero "
        f"(p={age_p:.2f}, 95% CI [{age_lo:+.4f}, {age_hi:+.4f}]s)"
    )
    d.add_test(
        "T18", "pace",
        "What factors explain lap-time variation within a stint?",
        "OLS: lap_vs_median ~ tyre_age + C(compound) + track_temperature + lap_number",
        fit.fvalue, fit.f_pvalue, fit.rsquared, "r_squared", int(fit.nobs),
        fit.f_pvalue < ALPHA,
        f"Together these explain {fit.rsquared:.1%} of how far a lap sits from its "
        f"session median. Fuel load dominates: every lap completed takes "
        f"{abs(fuel):.3f}s off the lap time. Each degree of track temperature adds "
        f"{temp:+.3f}s, and compound matters more than either, with intermediates "
        f"a different kind of lap entirely. Once fuel is in the model "
        f"{age_clause}, which is a statement about stint choice rather than about "
        f"rubber. For the degradation rate itself, read T11a.",
        f"The tyre age term here is a BETWEEN-stint contrast and must not be read "
        f"as a degradation rate. Within any single stint, tyre age and lap number "
        f"differ by a constant, so no model holding both can separate them. What "
        f"identifies this coefficient is the comparison of laps at the same point "
        f"in the race on tyres of different ages, which is a comparison between "
        f"cars that chose different stint lengths: longer stints go with harder "
        f"compounds and managed pace, so stint selection is inside the estimate. "
        f"VIF peaks at {max_vif:.2f}, low precisely because pooling across stints "
        f"is what breaks the collinearity, so this is a well-determined estimate "
        f"of the wrong quantity. T11a measures degradation the other way round, "
        f"within stints and after subtracting a separately estimated fuel term, "
        f"and that is the number to use. Compound is chosen by teams, never "
        f"assigned, so its coefficients carry a selection effect too. The outcome "
        f"is normalised to the session median, so this describes relative pace, "
        f"not lap times.",
    )


def a19_anomalous_lap_causes(d: Diagnostics, ctx) -> None:
    """
    Do anomalous laps cluster around a specific cause more for some teams?

    An anomalous lap is one above that driver's own Tukey fence within the
    race, which is what makes "unusually slow for them" rather than "slow".
    """
    con = ctx["con"]

    laps = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT l.session_key, l.driver_number, l.lap_number, l.lap_duration,
               f.sc_flag, f.vsc_flag, f.red_flag, f.yellow_sector_flag,
               d.team_name
        FROM scope
        JOIN silver_laps l ON l.session_key = scope.session_key
        JOIN silver_lap_flags f
          ON f.session_key = l.session_key AND f.driver_number = l.driver_number
         AND f.lap_number = l.lap_number
        JOIN silver_drivers d
          ON d.session_key = l.session_key AND d.driver_number = l.driver_number
        WHERE l.lap_duration IS NOT NULL
          AND COALESCE(l.is_pit_out_lap, 0) = 0
    """, con)
    laps = drop_excluded_teams(normalize_teams(laps))

    # Fence per driver per race, derived fresh (NOTES_LOG #30).
    g = laps.groupby(["session_key", "driver_number"])["lap_duration"]
    q1 = g.transform(lambda s: s.quantile(0.25))
    q3 = g.transform(lambda s: s.quantile(0.75))
    laps["anomalous"] = laps["lap_duration"] > q3 + 1.5 * (q3 - q1)

    odd = laps[laps["anomalous"]].copy()

    def cause(r):
        if r["red_flag"] == 1:
            return "RedFlag"
        if r["sc_flag"] == 1:
            return "SafetyCar"
        if r["vsc_flag"] == 1:
            return "VSC"
        if r["yellow_sector_flag"] == 1:
            return "Yellow"
        return "Unflagged"

    odd["cause"] = odd.apply(cause, axis=1)

    # Red flags are too rare to hold a chi-square cell, as the notebook found.
    dropped = int((odd["cause"] == "RedFlag").sum())
    odd = odd[odd["cause"] != "RedFlag"]

    table = pd.crosstab(odd["team_name"], odd["cause"])
    chi2, p, _, _ = stats.chi2_contingency(table)
    v = cramers_v(chi2, table)

    shares = odd["cause"].value_counts(normalize=True)
    for cse, share in shares.items():
        d.add_group("T19", "cause_share", "cause", str(cse), share,
                    int((odd["cause"] == cse).sum()))
    for team, grp in odd.groupby("team_name"):
        d.add_group("T19", "unflagged_share", "team", team,
                    (grp["cause"] == "Unflagged").mean(), len(grp))

    d.add_test(
        "T19", "pace",
        "Do anomalous laps cluster around a specific cause more for some teams?",
        "chi-square: team_name x anomaly cause (SafetyCar / VSC / Yellow / Unflagged)",
        chi2, p, v, "cramers_v", len(odd), p < ALPHA,
        f"{shares.get('Unflagged', 0):.0%} of unusually slow laps carry no flag at "
        f"all, so most are traffic, mistakes or damage rather than neutralisations. "
        + ("The mix of causes does differ by team."
           if p < ALPHA else
           "The mix of causes does not differ detectably by team."),
        f"{dropped} red-flag laps dropped: too few to support a chi-square cell. "
        "An anomalous lap is defined against that driver's own spread in that "
        "race, so the threshold moves with the circuit. Flags come from "
        "silver_lap_flags, which covers ranges, so an unflagged lap is genuinely "
        "unflagged rather than merely untagged.",
    )


def a20_sector_consistency(d: Diagnostics, ctx) -> None:
    """
    Is a driver's sector advantage over their teammate consistent enough to be
    a real skill signal?

    Sectors are not comparable to each other (they differ in length), so this
    only ever compares the same sector between two drivers in the same car.
    """
    con = ctx["con"]

    sec = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT l.session_key, l.driver_number, l.lap_number,
               l.duration_sector_1, l.duration_sector_2, l.duration_sector_3,
               d.team_name, d.full_name
        FROM scope
        JOIN silver_laps l ON l.session_key = scope.session_key
        JOIN silver_lap_flags f
          ON f.session_key = l.session_key AND f.driver_number = l.driver_number
         AND f.lap_number = l.lap_number
        JOIN silver_drivers d
          ON d.session_key = l.session_key AND d.driver_number = l.driver_number
        WHERE f.neutralised = 0 AND COALESCE(l.is_pit_out_lap, 0) = 0
          AND l.duration_sector_1 IS NOT NULL
    """, con)
    sec = drop_excluded_teams(normalize_teams(sec))

    per_race = (sec.groupby(["session_key", "team_name", "driver_number", "full_name"])
                   .agg(s1=("duration_sector_1", "median"),
                        s2=("duration_sector_2", "median"),
                        s3=("duration_sector_3", "median"))
                   .reset_index())
    per_race["year"] = 0  # teammate_pairs keys on it; sectors need no season split

    pairs = teammate_pairs(per_race, ["s1", "s2", "s3"])

    results, tested = [], 0
    for (pair_id, sector) in [(p, s) for p in pairs["pair_id"].unique()
                              for s in ["s1", "s2", "s3"]]:
        sub = pairs[pairs["pair_id"] == pair_id][f"{sector}_delta"].dropna()
        if len(sub) < MIN_PAIR_SESSIONS:
            continue
        tested += 1
        t, p = stats.ttest_1samp(sub, 0)
        results.append({"pair_id": pair_id, "sector": sector, "t": t, "p": p,
                        "mean": sub.mean(), "n": len(sub)})

    res = pd.DataFrame(results)
    alpha_c = bonferroni(tested) if tested else ALPHA
    res["significant"] = res["p"] < alpha_c
    n_sig = int(res["significant"].sum())

    for r in res[res["significant"]].itertuples():
        lo, hi = mean_ci(pairs[pairs["pair_id"] == r.pair_id]
                         [f"{r.sector}_delta"].dropna())
        d.add_group("T20", "sector_delta_seconds", "pair_sector",
                    f"{r.pair_id} {r.sector.upper()}", r.mean, r.n, lo, hi)

    d.add_test(
        "T20", "pace",
        "Is a driver's sector strength consistent across races, or too variable "
        "to be meaningful?",
        f"one-sample t-test per teammate pair per sector vs 0, "
        f"Bonferroni alpha={alpha_c:.5f} ({tested} comparisons)",
        np.nan, np.nan, n_sig / tested if tested else np.nan,
        "proportion_significant", int(res["n"].sum()) if len(res) else 0,
        n_sig > 0,
        f"{n_sig} of {tested} pair-and-sector combinations show a sector advantage "
        f"that survives Bonferroni correction. Because both drivers share a car, "
        f"a surviving signal isolates a genuine and repeatable driver difference "
        f"in that part of the circuit.",
        f"Pairs with fewer than {MIN_PAIR_SESSIONS} shared races excluded. Sectors "
        "are never compared to each other, only the same sector between teammates, "
        "because sectors differ in length. Medians per race are used so a single "
        "traffic-affected lap cannot move a pairing.",
    )


def a21_strategy_divergence(d: Diagnostics, ctx) -> None:
    """
    When teammates' strategies diverge, what predicts which one finished ahead?

    Only pairs whose stop counts actually differed are in scope: where both
    cars ran the same strategy there is no divergence to explain.
    """
    dr = drop_excluded_teams(ctx["driver_race"])

    base = dr[["session_key", "driver_number", "team_name", "full_name", "year",
               "pit_count", "first_pit_lap", "pace_vs_median", "grid_position",
               "finish_position"]].copy()
    pairs = teammate_pairs(base, ["pit_count", "first_pit_lap", "pace_vs_median",
                                  "grid_position", "finish_position"])

    div = pairs[(pairs["pit_count_delta"].abs() > 0)].dropna(subset=[
        "finish_position_delta", "pit_count_delta", "first_pit_lap_delta",
        "pace_vs_median_delta", "grid_position_delta"]).copy()

    # a finished ahead of b when a's finishing position is the lower number.
    div["a_ahead"] = (div["finish_position_delta"] < 0).astype(int)

    model = smf.logit(
        "a_ahead ~ pit_count_delta + first_pit_lap_delta + pace_vs_median_delta "
        "+ grid_position_delta", data=div).fit(disp=False)
    vifs = compute_vifs(div, ["pit_count_delta", "first_pit_lap_delta",
                              "pace_vs_median_delta", "grid_position_delta"])
    d.add_coefficients("T21", "strategy_divergence_logit", model, vifs=vifs)

    pace_c = model.params.get("pace_vs_median_delta", np.nan)
    d.add_test(
        "T21", "strategy",
        "When teammates' strategies diverge, what predicts which one pays off?",
        "logistic regression: finished-ahead ~ stop count, first stop lap, pace "
        "and grid deltas, on teammate pairs whose stop counts differed",
        model.llr, model.llr_pvalue, model.prsquared, "pseudo_r_squared",
        int(model.nobs), model.llr_pvalue < ALPHA,
        f"Across {int(model.nobs)} teammate races where the two cars ran different "
        f"stop counts, race pace is what decides the outcome ({pace_c:.2f} log-odds "
        f"per second): the faster car finishes ahead largely regardless of which "
        f"strategy it was given.",
        "Strategy is not assigned at random. A team that splits strategies usually "
        "does so because one car is already behind, so the strategy and the "
        "situation that produced it cannot be fully separated here. Finishing "
        "ahead is also not the same as the strategy paying off.",
    )


def a22_disaster_stop_concentration(d: Diagnostics, ctx) -> None:
    """
    Are disaster stops random, or concentrated in specific teams?

    lane_duration, not stop_duration: coverage is 3.5% for stop_duration
    against 77% for lane_duration.
    """
    con = ctx["con"]

    pits = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT p.session_key, p.driver_number, p.lap_number, p.lane_duration,
               d.team_name
        FROM scope
        JOIN silver_pit p ON p.session_key = scope.session_key
        JOIN silver_drivers d
          ON d.session_key = p.session_key AND d.driver_number = p.driver_number
        WHERE p.lane_duration IS NOT NULL
    """, con)
    pits = drop_excluded_teams(normalize_teams(pits))

    q1, q3 = pits["lane_duration"].quantile([0.25, 0.75])
    fence = q3 + 1.5 * (q3 - q1)
    pits["disaster"] = (pits["lane_duration"] > fence).astype(int)

    # Which stop of the race it was, capped: 4th stops are too rare to stand alone.
    pits = pits.sort_values(["session_key", "driver_number", "lap_number"])
    pits["stop_number"] = (pits.groupby(["session_key", "driver_number"])
                               .cumcount() + 1).clip(upper=3)

    table = pd.crosstab(pits["team_name"], pits["disaster"])
    chi2, p, _, _ = stats.chi2_contingency(table)
    v = cramers_v(chi2, table)

    for team, grp in pits.groupby("team_name"):
        d.add_group("T22", "disaster_rate", "team", team,
                    grp["disaster"].mean(), len(grp))
    for num, grp in pits.groupby("stop_number"):
        label = f"Stop {int(num)}" + ("+" if num == 3 else "")
        d.add_group("T22", "disaster_rate", "stop_number", label,
                    grp["disaster"].mean(), len(grp))

    rates = pits.groupby("team_name")["disaster"].mean().sort_values()
    d.add_test(
        "T22", "pit_stops",
        "Are disaster stops random, or concentrated in specific teams?",
        f"chi-square: team_name x disaster stop, Tukey fence {fence:.2f}s derived fresh",
        chi2, p, v, "cramers_v", len(pits), p < ALPHA,
        f"Disaster stops are not spread evenly: rates run from "
        f"{rates.iloc[0]:.1%} ({rates.index[0]}) to {rates.iloc[-1]:.1%} "
        f"({rates.index[-1]}). "
        + ("The concentration is statistically clear."
           if p < ALPHA else
           "The differences are not statistically distinguishable from chance."),
        f"A disaster is defined by this dataset's own upper Tukey fence "
        f"({fence:.2f}s), not a fixed number of seconds. lane_duration is used "
        "because stop_duration covers only 3.5% of stops. Multi-minute values are "
        "cars held in the lane under a red flag (NOTES_LOG #18), and they are "
        "counted as disasters here, which slightly inflates rates for teams that "
        "happened to be in the pits when a race was stopped.",
    )


def a23_fighting_and_overtakes(d: Diagnostics, ctx) -> None:
    """
    Does spending more time within a second of the car ahead lead to more
    overtakes?

    Both quantities are per driver per race. The interval feed samples roughly
    every few seconds, so the count is exposure time, not a lap count.
    """
    con = ctx["con"]
    dr = ctx["driver_race"]

    fight = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT i.session_key, i.driver_number,
               SUM(CASE WHEN i.interval_seconds < 1.0 THEN 1 ELSE 0 END) AS drs_samples,
               COUNT(*) AS samples
        FROM scope JOIN silver_intervals i ON i.session_key = scope.session_key
        WHERE i.interval_seconds IS NOT NULL
        GROUP BY i.session_key, i.driver_number
    """, con)

    made = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT o.session_key, o.overtaking_driver_number AS driver_number,
               COUNT(*) AS overtakes_made
        FROM scope JOIN silver_overtakes o ON o.session_key = scope.session_key
        GROUP BY o.session_key, o.overtaking_driver_number
    """, con)

    m = (fight.merge(made, on=["session_key", "driver_number"], how="left")
              .fillna({"overtakes_made": 0}))
    m = m.merge(dr[["session_key", "driver_number", "team_name"]],
                on=["session_key", "driver_number"], how="inner")
    m = drop_excluded_teams(m)

    # Share, not raw count: a longer race gives more samples to both variables,
    # which would manufacture a correlation out of race length alone.
    m["drs_share"] = m["drs_samples"] / m["samples"]

    r, p = stats.pearsonr(m["drs_share"], m["overtakes_made"])
    rho, p_rho = stats.spearmanr(m["drs_share"], m["overtakes_made"])

    d.add_group("T23", "correlation", "method", "Pearson", r, len(m))
    d.add_group("T23", "correlation", "method", "Spearman", rho, len(m))

    # The two coefficients disagree in sign, which is itself the finding: there
    # is no consistent linear relationship to report in either direction.
    disagree = (r < 0) != (rho < 0)
    d.add_test(
        "T23", "overtaking",
        "Does spending more time within a second of the car ahead correlate with "
        "more overtakes?",
        "Pearson and Spearman correlation between share of interval samples under "
        "1.0s and overtakes made, per driver per race",
        r, p, abs(r), "abs_pearson_r", len(m), p < ALPHA,
        f"Essentially not at all. Pearson gives r={r:+.2f} and Spearman "
        f"rho={rho:+.2f}"
        + (", disagreeing even on the direction, " if disagree else ", ")
        + f"and the share of time spent within a second explains {r**2:.1%} of "
        f"how many passes a driver makes. Proximity is a precondition for "
        f"overtaking, not a predictor of it: the cars that spend longest within a "
        f"second are frequently the ones stuck behind something they cannot pass.",
        "Correlation only, and the causation runs both ways. A large sample makes "
        "even a negligible coefficient statistically significant here, which is "
        "why the effect size matters more than the p-value. The overtake feed "
        "includes pit-cycle and penalty position changes (NOTES_LOG #20).",
    )


def a24_radio_and_outcome(d: Diagnostics, ctx) -> None:
    """
    Does radio volume relate to what happened in the race?

    silver_team_radio holds audio URLs and no transcription, so only volume and
    timing can be analysed, never content.
    """
    con = ctx["con"]
    dr = drop_excluded_teams(ctx["driver_race"])

    radio = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT tr.session_key, tr.driver_number, COUNT(*) AS messages
        FROM scope JOIN silver_team_radio tr ON tr.session_key = scope.session_key
        GROUP BY tr.session_key, tr.driver_number
    """, con)

    covered = set(radio["session_key"].unique())
    m = dr[dr["session_key"].isin(covered)].merge(
        radio, on=["session_key", "driver_number"], how="left")
    m["messages"] = m["messages"].fillna(0)

    # Races differ enormously in how much radio was captured, so compare each
    # driver against the field in their own race rather than across races.
    m["messages_vs_race"] = m["messages"] - m.groupby("session_key")["messages"].transform("mean")

    pairs = {
        "position_change": "places gained",
        "points": "points scored",
        "pit_count": "pit stops",
    }
    best_r, best_label, best_p, n_used = 0.0, None, np.nan, 0
    for col, label in pairs.items():
        sub = m.dropna(subset=[col, "messages_vs_race"])
        if len(sub) < 30:
            continue
        r, p = stats.pearsonr(sub["messages_vs_race"], sub[col])
        d.add_group("T24", "correlation_with_radio", "outcome", label, r, len(sub))
        if abs(r) > abs(best_r):
            best_r, best_label, best_p, n_used = r, label, p, len(sub)

    dnf_sub = m.dropna(subset=["dnf"])
    r_dnf, p_dnf = stats.pearsonr(dnf_sub["messages_vs_race"], dnf_sub["dnf"])
    d.add_group("T24", "correlation_with_radio", "outcome", "retired", r_dnf,
                len(dnf_sub))

    d.add_test(
        "T24", "racecraft",
        "Does radio message frequency correlate with race outcome?",
        "Pearson correlations between a driver's radio volume relative to their "
        "own race and each outcome measure",
        best_r, best_p, best_r ** 2, "r_squared", n_used, best_p < ALPHA,
        f"Radio volume does track race outcome, and more strongly than volume "
        f"without content might suggest: a driver getting more radio than the "
        f"rest of their own race correlates with {best_label} at r={best_r:+.2f} "
        f"({best_r**2:.0%} of the variance), and with retiring at r={r_dnf:+.2f}. "
        f"The likely mechanism is attention rather than causation, since teams "
        f"talk most to a car that is in contention or in trouble.",
        "Messages are audio URLs with no transcription, so this is volume only, "
        "and volume cannot separate a strategy call from a complaint. The "
        "relationship is almost certainly reverse causation: being in the fight "
        "generates radio traffic, not the other way round. Coverage falls sharply "
        "over time, from 2,744 messages in 2023 to 217 in 2026, and races with no "
        "radio captured at all are excluded rather than counted as silent.",
    )


# =================================================================================

ANALYSES = {
    "T01": a01_team_pace_points,
    "T02": a02_teammate_decomposition,
    "T03": a03_grid_to_finish,
    "T04": a04_grid_circuit_interaction,
    "T05": a05_stop_strategy_ancova,
    "T06": a06_overtake_conversion,
    "T07": a07_pit_lane_by_team,
    "T08": a08_lapping_by_team,
    "T09": a09_dnf_by_team,
    "T10": a10_wet_advantage,
    "T11": a11_tyre_degradation,
    "T12": a12_rain_variance,
    "T13": a13_teammate_quali_deltas,
    "T14": a14_net_gain_by_team,
    "T15": a15_position_swings,
    "T16": a16_lap1_swing,
    # Added so every question the notebooks answer has a figure recomputed
    # against current silver, rather than a number carried over from a notebook
    # run on pre-backfill data with the old caution flag.
    "T17": a17_lap1_chaos_by_circuit,
    "T18": a18_within_stint_pace,
    "T19": a19_anomalous_lap_causes,
    "T20": a20_sector_consistency,
    "T21": a21_strategy_divergence,
    "T22": a22_disaster_stop_concentration,
    "T23": a23_fighting_and_overtakes,
    "T24": a24_radio_and_outcome,
}

TABLES = ["diag_tests", "diag_coefficients", "diag_groups", "diag_points"]


# --- table builders --------------------------------------------------------------

def build_diag_tests(d: Diagnostics) -> pd.DataFrame:
    cols = ["test_id", "category", "question", "method", "statistic", "p_value",
            "effect_size", "effect_size_type", "n", "significant", "conclusion",
            "caveat"]
    return pd.DataFrame(d.tests, columns=cols).sort_values("test_id")


def build_diag_coefficients(d: Diagnostics) -> pd.DataFrame:
    cols = ["test_id", "model", "predictor", "coefficient", "std_coefficient",
            "std_error", "p_value", "ci_lower", "ci_upper", "vif"]
    return pd.DataFrame(d.coefficients, columns=cols).sort_values(["test_id", "predictor"])


def build_diag_groups(d: Diagnostics) -> pd.DataFrame:
    cols = ["test_id", "metric", "group_type", "group_name", "value", "n",
            "ci_lower", "ci_upper"]
    return pd.DataFrame(d.groups, columns=cols).sort_values(["test_id", "metric", "group_name"])


def build_diag_points(d: Diagnostics) -> pd.DataFrame:
    cols = ["test_id", "x", "y", "label", "group_name"]
    if not d.points:
        return pd.DataFrame(columns=cols)
    out = pd.concat(d.points, ignore_index=True)
    return out[cols].round({"x": 4, "y": 4})


BUILDERS = {
    "diag_tests": build_diag_tests,
    "diag_coefficients": build_diag_coefficients,
    "diag_groups": build_diag_groups,
    "diag_points": build_diag_points,
}


# --- runner ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Build the diagnostic serving layer.")
    ap.add_argument("--tables", nargs="*", default=None,
                    help=f"subset of output tables to write {TABLES}")
    ap.add_argument("--tests", nargs="*", default=None,
                    help="subset of analyses to run, e.g. T03 T04. Prints results "
                         "without writing, since a partial run cannot produce a "
                         "complete table; add --allow-partial to write anyway.")
    ap.add_argument("--allow-partial", action="store_true",
                    help="let a --tests subset overwrite the tables (destructive)")
    ap.add_argument("--csv", action="store_true",
                    help="also write each table as CSV, for reading by eye")
    ap.add_argument("--list", action="store_true", help="list analyses and exit")
    args = ap.parse_args()

    if args.list:
        for tid, fn in ANALYSES.items():
            first = (fn.__doc__ or "").strip().splitlines()[0]
            print(f"  {tid}  {first}")
        return 0

    if not DB_PATH.exists():
        print(f"[FAIL] silver database not found at {DB_PATH}")
        return 1
    if not GOLD_DB_PATH.exists():
        print(f"[FAIL] gold database not found at {GOLD_DB_PATH}\n"
              "       build it with: python pipeline\\s07_build_gold.py --execute")
        return 1

    targets = args.tables or TABLES
    unknown = [t for t in targets if t not in BUILDERS]
    if unknown:
        print(f"[FAIL] unknown table(s): {unknown}. Valid: {TABLES}")
        return 1

    run_ids = args.tests or list(ANALYSES)
    unknown_t = [t for t in run_ids if t not in ANALYSES]
    if unknown_t:
        print(f"[FAIL] unknown analysis id(s): {unknown_t}. Use --list.")
        return 1

    generated_at = datetime.now(timezone.utc).isoformat()

    print("=" * 74)
    print("DIAGNOSTIC SERVING LAYER")
    print(f"silver: {DB_PATH}")
    print(f"target: {serving.BUNDLE_DB}")
    print(f"python: {sys.version.split()[0]}  pandas {pd.__version__}")
    print("=" * 74)

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    gold = sqlite3.connect(f"file:{GOLD_DB_PATH}?mode=ro", uri=True)

    # Both connections stay open during the migration onto gold. The three
    # shared loaders below read gold; the per-test query sites still read
    # silver through ctx["con"] and are moved over one at a time so each move
    # can be checked against the 29 verdicts on its own.
    n_races = pd.read_sql(f"SELECT COUNT(*) AS n FROM ({GOLD_RACE_SCOPE})",
                          gold)["n"].iloc[0]
    print(f"\nscope: {n_races} completed races")

    started = time.time()
    clean_laps = load_clean_laps(gold)
    driver_race = load_driver_race(gold, clean_laps)
    weather = load_race_weather(gold)
    print(f"shared frames: {len(clean_laps):,} clean laps, "
          f"{len(driver_race):,} driver-races  ({time.time() - started:.1f}s)\n")

    ctx = {"con": con, "gold": gold, "clean_laps": clean_laps,
           "driver_race": driver_race, "weather": weather}
    d = Diagnostics()

    failures = []
    for tid in run_ids:
        t0 = time.time()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ANALYSES[tid](d, ctx)
        except Exception as exc:
            print(f"  [FAIL] {tid}: {type(exc).__name__}: {exc}")
            failures.append(tid)
            continue
        print(f"  {tid:5s} ok  {time.time() - t0:6.1f}s")

    con.close()
    gold.close()

    # These tables are drop-and-rewrite, so writing after a subset run would
    # replace a complete table with a fragment. Inspecting one analysis is a
    # normal thing to want; silently truncating the serving layer is not.
    partial = len(run_ids) < len(ANALYSES)
    write = (not partial) or args.allow_partial

    print()
    # Opened only when actually writing, so a --tests run cannot create or
    # touch the bundle at all.
    out = serving.connect() if write else None
    for name in targets:
        df = BUILDERS[name](d)
        df["generated_at"] = generated_at
        if write:
            serving.write_table(df, name, out, csv=args.csv)
        print(f"  {name:20s} {len(df):>6,} rows x {len(df.columns):>2} cols"
              + ("" if write else "   (not written)"))
    if out is not None:
        out.commit()
        out.close()

    if partial and not args.allow_partial:
        print(f"\n  NOT WRITTEN: --tests ran {len(run_ids)} of {len(ANALYSES)} analyses.")
        print("  These tables are drop-and-rewrite; writing now would replace the")
        print("  full serving layer with this fragment. Re-run without --tests, or")
        print("  pass --allow-partial if replacing them is what you want.")

    print("\n" + "=" * 74)
    if failures:
        print(f"FAILED analyses: {failures}")
        print("=" * 74)
        return 1
    n_sig = sum(1 for t in d.tests if t["significant"])
    n_caveat = sum(1 for t in d.tests if t["caveat"])
    print(f"{len(d.tests)} tests | {n_sig} significant | {n_caveat} carrying a caveat")
    print(f"Serving layer written to {serving.BUNDLE_DB.name}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
