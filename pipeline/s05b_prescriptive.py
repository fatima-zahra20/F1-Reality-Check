"""
s05b_prescriptive.py - the models behind "Prescribe a lap", 2023 to 2026.

Runs between s05 and s06 so the existing publish command is unchanged.

This file was s05b_perfect.py. The name changed with the page it feeds: the
dashboard's fourth section is now Prescribe, the prescriptive layer, sitting
after Analyse, Diagnose and Predict. Only the naming moved. Every model, filter
and coefficient below is unchanged, and the four perfect_* tables further down
keep their own names for the reason given at ON_REQUEST.

Writes five tables straight into the dashboard bundle,
dashboard/data/dashboard.duckdb:

    lap_factor_anova       how much of within-race lap variation each factor explains
    lap_factor_model       every coefficient, so the app can decompose one lap
    lap_factor_reference   the typical value of each factor within each race
    lap_counterfactual_model   the within-lap fit the counterfactual moves along
    lap_counterfactual_bounds  the range each lever is allowed, per race

Four more are built only when named on --tables, and go to outputs/analysis/ as
CSV rather than into the bundle, because nothing reads them:

    perfect_lap            the ranked race laps, with every parameter attached
    perfect_lap_model      the model behind the ranking, one row per predictor
    perfect_lap_record     raw fastest clean lap per circuit, for comparison
    perfect_race           the four components of a great race, ranked separately

The design notes below still describe how the ranking works, because the code
that does it is intact and one command away. See ON_REQUEST near the runner for
why it stopped running by default.

DRS and the tow are NOT here. They exist for six races out of 81 and belong in
their own step, s05d_telemetry.py, which says so on the page.

Design decisions
----------------
RAW FASTEST LAP IS NOT THE BEST LAP. A global MIN(lap_duration) over 2023-2026
returns Spielberg 67.012s, and it always will, because Spielberg is the
shortest circuit on the calendar. Ranking laps by seconds ranks circuits by
length. Every approach here exists to get around that.

THE ANSWER IS A RESIDUAL. Lap time is modelled on where it was set, on what
compound, with how much fuel, on how old a tyre, in what weather. The perfect
lap is the largest negative residual: the lap least explained by its own
circumstances. That makes "best" a measured quantity rather than an opinion,
which is the same standard the rest of the dashboard is held to.

THE RESIDUAL IS STUDENTISED WITHIN ITS SESSION, and this is not a refinement,
it is the difference between a working ranking and a broken one. Residual
spread is wildly unequal across races: 2.64s in the average dry race against
4.68s in a wet one, peaking at 19.58s. Ranking raw residuals therefore ranks
races by how chaotic they were, not laps by how good they were. Measured on
the first build: intermediates are 4.2% of all clean laps but took 47.8% of
the top 500, rainfall laps are 2.5% of laps but took 51.2%, and the 2025
Belgian Grand Prix alone took 179 of 500 places. Dividing each residual by its
own session's residual standard deviation makes a lap compete against the
noise level of the race it was set in. That drops the worst single-race
concentration from 179 places to 16.

WET LAPS ARE STILL OVER-REPRESENTED after that correction, roughly elevenfold,
because `rainfall` is a 0/1 flag that cannot express how wet, so a drying
track leaves the model predicting one number across a phase where real lap
times fall by ten seconds. That residual is measuring track evolution, not
driving. The correction shrinks the effect, it does not remove it, so every
lap carries a `track_state` column and the honest reading of a wet-flagged
entry is "unusually fast for a lap the model could not properly price".

ONE LAP PER DRIVER PER RACE. A driver on a strong run sets twenty consecutive
laps that all beat expectation, and without this the leaderboard is one
afternoon repeated. Each driver-race contributes only its single best lap.

YEAR IS A FIXED EFFECT, deliberately. Without it the ranking collapses onto
whichever season had the fastest regulations and stays there. With it, a lap
has to beat its own era, not 2023's.

THE RAW RECORD IS ALWAYS SHOWN. perfect_lap_record carries the unadjusted
fastest clean lap per circuit so the model never looks like it is hiding the
real number. If the model's pick and the raw record disagree, that gap is the
interesting part, not an embarrassment.

NO BLENDED SCORE FOR THE PERFECT RACE. A race has no single measurable
quantity. Places gained, pace against the field, consistency and staying out
of trouble are four different things, and any weighting of them into one
number is an opinion wearing a decimal point. All four ship as separate
columns with separate ranks; the reader picks.

CLEAN LAPS use silver_lap_flags.neutralised = 0 and the per-session derived
bound, identical to s05.load_clean_laps. Same definition, same numbers.

READ-ONLY ON SILVER. Opened with read_only=True, so nothing here can write back
into a layer it is only supposed to read. gold_f1.duckdb is untouched.

Usage
-----
    python pipeline\\s05b_prescriptive.py
    python pipeline\\s05b_prescriptive.py --tables perfect_lap
    python pipeline\\s05b_prescriptive.py --top 200

Requires the pinned Anaconda environment. See NOTES_LOG #42.
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH, LAP_OUTLIER_FACTOR, read_sql  # noqa: E402
import serving  # noqa: E402

import statsmodels.formula.api as smf  # noqa: E402
from statsmodels.stats.anova import anova_lm  # noqa: E402
from statsmodels.stats.outliers_influence import variance_inflation_factor  # noqa: E402

TEAM_NAME_MAP = {
    "AlphaTauri": "RB Family",
    "RB": "RB Family",
    "Racing Bulls": "RB Family",
    "Alfa Romeo": "Sauber Family",
    "Kick Sauber": "Sauber Family",
    "Audi": "Sauber Family",
}

# LAP_OUTLIER_FACTOR is imported from config, which is the single definition.

# 0 keeps every candidate. There is one per driver-race, so the whole table is
# ~1,550 rows and the dashboard can filter it freely rather than being handed a
# pre-truncated list.
DEFAULT_TOP_N = 0

# A session needs this many modelled laps before its residual standard
# deviation is stable enough to divide by. Below it, one wild lap sets the
# scale and every other lap in that race is judged against it.
MIN_SESSION_LAPS = 50

# A compound seen fewer times than this cannot support its own dummy, and
# UNKNOWN / TEST_UNKNOWN are placeholders rather than rubber.
MIN_COMPOUND_LAPS = 200
VALID_COMPOUNDS = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"]

# Teams start a race on a scrubbed set now and then, so a couple of laps of
# stint-1 tyre age is normal. A whole field starting on tyres this old is not
# racing, it is bad stint metadata. Across the 81 races the median session sits
# at 0.10 laps and the mean at 0.67; the 2025 Miami Grand Prix reports 22.3,
# the only session anywhere near this line. Left in, it hands the model a
# field-wide fiction, the model predicts slow laps for everyone, and the whole
# race sweeps the leaderboard on the strength of the error. Measured per
# session rather than hardcoded to Miami so the rule survives new data.
MAX_PLAUSIBLE_START_AGE = 10

# Same dynamic scope as s04 and s05, so all three cover the same races. See s04
# for why the date test casts both sides to a timestamp rather than comparing
# the strings.
RACE_SCOPE = """
    SELECT s.session_key, s.meeting_key
    FROM silver_sessions s
    WHERE s.session_name = 'Race'
      AND s.is_cancelled = 0
      AND CAST(substr(s.date_start, 1, 19) AS TIMESTAMP)
            < (now() AT TIME ZONE 'UTC')
      AND EXISTS (SELECT 1 FROM silver_laps l WHERE l.session_key = s.session_key)
      AND EXISTS (SELECT 1 FROM silver_session_result r WHERE r.session_key = s.session_key)
"""

MODEL_FORMULA = (
    "lap_duration ~ C(circuit_short_name) + C(year) + C(compound)"
    " + tyre_age + lap_number + track_temperature + air_temperature"
    " + humidity + wind_speed + rainfall"
)

NUMERIC_PREDICTORS = ["tyre_age", "lap_number", "track_temperature",
                      "air_temperature", "humidity", "wind_speed", "rainfall"]

# The same predictors against a different target: how far a lap sits from the
# median lap of its own race, rather than its absolute time.
#
# This is the model behind "what made this lap what it was", and the change of
# target is the whole point. On absolute lap time, 83.6% of the variance is
# the circuit: a model that mostly knows Monaco is slower than Monza explains
# a great deal and tells you nothing about a lap. Taking the difference from
# the session median absorbs the circuit, the era and the car generation, and
# leaves only what actually varied inside one afternoon.
#
# It also makes the honest answer visible. This model reaches R2 = 0.236, so
# roughly 76% of why one lap differs from another in the same race is not
# explained by anything recorded. That leftover is the driver, the line, and
# the parts of the car and the traffic no channel reports.
#
# It got there in three steps, each worth recording because each one was a gap
# in the model rather than a gap in the sport:
#
#   93% -> 88%   dividing by the true total instead of the ANOVA table's, which
#                does not partition the variance
#   88% -> 83%   adding the car, wind direction crossed with circuit, and gap
#                to the car ahead
#   83% -> 76%   flagging out-of-position running and sector yellows instead of
#                filling them in as clear air and clear track
#
# What is still missing is known and mostly unobtainable from this source. ERS
# deployment does not exist in OpenF1 at all: car_data carries throttle, rpm,
# brake, speed, n_gear and drs, and nothing else. DRS and the tow do exist, but
# only for the six races that carry telemetry, so they cannot enter a model
# fitted across 81. They are reported separately by s05d_telemetry.py.
FACTOR_FORMULA = (
    "lap_vs_median ~ C(compound) + tyre_age + lap_number"
    " + track_temperature + air_temperature + humidity + wind_speed + rainfall"
    " + C(team_name) + gap_ahead + in_dirty_air"
    " + out_of_position + being_lapped + yellow_sector"
    " + C(circuit_short_name):wind_u + C(circuit_short_name):wind_v"
)

# Human labels. Several formula terms collapse to one factor for reporting,
# because a reader wants "wind direction", not two orthogonal components
# crossed with twenty-four circuits.
FACTOR_LABELS = {
    "C(compound)": "Tyre compound",
    "lap_number": "Fuel load, via lap number",
    "tyre_age": "Tyre age",
    "rainfall": "Rain",
    "track_temperature": "Track temperature",
    "air_temperature": "Air temperature",
    "humidity": "Humidity",
    "wind_speed": "Wind speed",
    "C(team_name)": "The car",
    "gap_ahead": "Traffic ahead",
    "in_dirty_air": "Running in dirty air",
    # Named for what it measures, not for what it would be nice to measure.
    # See attach_traffic for why this is not simply "traffic".
    "out_of_position": "Out of position, gap counted in laps",
    "being_lapped": "Being lapped",
    "yellow_sector": "Sector yellow flag",
    "C(circuit_short_name):wind_u": "Wind direction, per circuit",
    "C(circuit_short_name):wind_v": "Wind direction, per circuit",
    "Residual": "Everything not measured",
}

# Within a race, being close behind another car costs time. Beyond this the
# car ahead is irrelevant, so the gap is capped rather than left to run to
# the length of a straight and drag the fit around.
GAP_CAP_SECONDS = 10.0

# The sport's own DRS and dirty-air threshold is one second; 1.5 gives the
# aerodynamic effect a little room without reaching for a tuned number.
DIRTY_AIR_SECONDS = 1.5


def _round(v, nd=3):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    return round(float(v), nd)


def normalize_teams(df: pd.DataFrame, col: str = "team_name") -> pd.DataFrame:
    df = df[df[col].notna()].copy()
    df[col] = df[col].replace(TEAM_NAME_MAP)
    return df


# --- loaders ---------------------------------------------------------------------

def load_laps(con) -> pd.DataFrame:
    """
    Every clean race lap with the parameters that could explain its time.

    Clean means the same three things it means in s05: not neutralised, not a
    pit-out lap, and not longer than 2x the session median. The stint join is
    a BETWEEN on lap number rather than an equality, because stints are stored
    as ranges; it is a left join because some race laps fall outside any
    recorded stint and dropping them silently would be worse than carrying a
    null compound into the filter below.

    That BETWEEN fans out. silver_stints has 14,012 overlapping pairs, because
    a stint's lap_end and the next stint's lap_start are the SAME lap: the lap
    the driver pitted on. So the in-lap matches two stints and arrives twice,
    244 times over after the clean filters. The lap was driven on the older
    tyre and the stop happened at the end of it, so the lower stint_number is
    the correct attribution, not merely the convenient one.
    """
    laps = read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT l.session_key, l.driver_number, l.lap_number,
               l.date_start, l.lap_duration,
               l.duration_sector_1, l.duration_sector_2, l.duration_sector_3,
               l.i1_speed, l.i2_speed, l.st_speed,
               d.team_name, d.full_name, d.name_acronym,
               s.year, s.circuit_short_name,
               m.meeting_name,
               st.compound, st.stint_number, st.tyre_age_at_start, st.lap_start,
               f.yellow_sector_flag
        FROM scope
        JOIN silver_laps l ON l.session_key = scope.session_key
        JOIN silver_lap_flags f
          ON  f.session_key   = l.session_key
          AND f.driver_number = l.driver_number
          AND f.lap_number    = l.lap_number
        JOIN silver_drivers d
          ON  d.session_key   = l.session_key
          AND d.driver_number = l.driver_number
        JOIN silver_sessions s ON s.session_key = scope.session_key
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        LEFT JOIN silver_stints st
          ON  st.session_key   = l.session_key
          AND st.driver_number = l.driver_number
          AND st.lap_start    <= l.lap_number
          AND st.lap_end      >= l.lap_number
        WHERE l.lap_duration IS NOT NULL
          AND f.neutralised = 0
          AND COALESCE(l.is_pit_out_lap, 0) = 0
    """, con)

    laps = (laps.sort_values(["session_key", "driver_number", "lap_number",
                              "stint_number"])
                .drop_duplicates(["session_key", "driver_number", "lap_number"],
                                 keep="first"))

    med = laps.groupby("session_key")["lap_duration"].median().rename("session_median_lap")
    laps = laps.merge(med, on="session_key", how="left")
    laps = laps[laps["lap_duration"] <= LAP_OUTLIER_FACTOR * laps["session_median_lap"]].copy()

    # Recompute after trimming so the baseline is itself clean, same as s05.
    laps["session_median_lap"] = laps.groupby("session_key")["lap_duration"].transform("median")
    laps["lap_vs_median"] = laps["lap_duration"] - laps["session_median_lap"]
    laps["pct_of_median"] = 100.0 * laps["lap_duration"] / laps["session_median_lap"]

    # Tyre age on THIS lap, not at the stint's start.
    laps["tyre_age"] = laps["tyre_age_at_start"] + (laps["lap_number"] - laps["lap_start"])

    # A sector yellow is NOT a neutralisation, which is why s02b keeps the two
    # apart and why these laps survive the filter above. But it is not nothing
    # either: one marshal sector under yellow costs 1.86s, so it belongs in the
    # model as a factor rather than being left in the residual as "driving".
    laps["yellow_sector"] = laps["yellow_sector_flag"].fillna(0).astype(int)

    return normalize_teams(laps)


def attach_weather(con, laps: pd.DataFrame) -> pd.DataFrame:
    """
    Nearest weather sample to each lap's start, within its own session.

    Weather is logged roughly once a minute and laps take roughly ninety
    seconds, so nearest-in-time is the honest join; there is no exact match to
    find. merge_asof needs both sides sorted by the join key and grouped by
    session, otherwise it will happily match a lap in Bahrain to a reading in
    Suzuka.
    """
    wx = read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT w.session_key, w.date, w.humidity, w.pressure, w.rainfall,
               w.track_temperature, w.air_temperature,
               w.wind_speed, w.wind_direction
        FROM scope JOIN silver_weather w ON w.session_key = scope.session_key
    """, con)
    wx["date"] = pd.to_datetime(wx["date"], format="ISO8601", utc=True)
    wx = wx.sort_values("date")

    laps = laps.copy()
    laps["_ts"] = pd.to_datetime(laps["date_start"], format="ISO8601", utc=True)

    timed = laps[laps["_ts"].notna()].sort_values("_ts")
    untimed = laps[laps["_ts"].isna()]

    merged = pd.merge_asof(timed, wx, left_on="_ts", right_on="date",
                           by="session_key", direction="nearest")
    out = pd.concat([merged, untimed], ignore_index=True)
    return out.drop(columns=["_ts", "date"], errors="ignore")


# --- perfect lap -----------------------------------------------------------------

def fit_lap_model(laps: pd.DataFrame):
    """
    Models lap time on its circumstances, so the leftover is the driving.

    Returns (fit, modelled_frame). The frame is the subset the model could
    actually use: a lap with no compound, no weather sample or no tyre age
    cannot be given an expectation, and giving it one by imputation would let
    a lap win the ranking on the strength of a filled-in value.
    """
    df = laps.copy()

    keep = df["compound"].isin(VALID_COMPOUNDS)
    counts = df.loc[keep, "compound"].value_counts()
    rare = counts[counts < MIN_COMPOUND_LAPS].index.tolist()
    if rare:
        keep &= ~df["compound"].isin(rare)

    needed = ["lap_duration", "circuit_short_name", "year", "compound",
              "tyre_age", "lap_number", "track_temperature", "air_temperature",
              "humidity", "wind_speed", "rainfall"]
    df = df[keep & df[needed].notna().all(axis=1)].copy()

    # A negative tyre age is a stint-boundary artefact, not a fresh tyre.
    df = df[df["tyre_age"] >= 0]

    # Drop sessions whose stint metadata cannot be believed. See the constant.
    start_age = (df[df["stint_number"] == 1]
                 .groupby("session_key")["tyre_age_at_start"].median())
    bad = sorted(start_age[start_age > MAX_PLAUSIBLE_START_AGE].index.tolist())
    if bad:
        df = df[~df["session_key"].isin(bad)]

    fit = smf.ols(MODEL_FORMULA, data=df).fit()
    df["predicted_lap"] = fit.fittedvalues
    df["residual"] = df["lap_duration"] - df["predicted_lap"]

    # Studentise within session. See the module docstring: without this the
    # ranking is a list of chaotic races rather than a list of good laps.
    df["resid_sd_session"] = df.groupby("session_key")["residual"].transform("std")
    df["session_modelled_laps"] = df.groupby("session_key")["residual"].transform("size")
    df = df[df["session_modelled_laps"] >= MIN_SESSION_LAPS].copy()
    df["z_residual"] = df["residual"] / df["resid_sd_session"]

    # Wet is a property of the lap, not only of the session: a driver on
    # intermediates is racing a different track surface to one on slicks in
    # the same minute.
    df["track_state"] = np.where(
        (df["rainfall"] == 1) | df["compound"].isin(["INTERMEDIATE", "WET"]),
        "wet", "dry")

    return fit, df, rare, bad


def compute_vifs(df: pd.DataFrame) -> dict:
    """VIF on the numeric predictors only; dummy VIFs are not interpretable."""
    x = df[NUMERIC_PREDICTORS].astype(float)
    x = x.loc[:, x.std() > 0]
    x = x.assign(_const=1.0)
    cols = [c for c in x.columns if c != "_const"]
    return {c: variance_inflation_factor(x.values, x.columns.get_loc(c)) for c in cols}


def build_perfect_lap(modelled: pd.DataFrame, top_n: int = 0) -> pd.DataFrame:
    """
    The ranked laps, one candidate per driver-race, best z_residual first.

    rank_dry is a second rank computed over dry laps only, so the dashboard
    can offer a dry-only view without recomputing anything and without the
    ranks jumping to 1, 4, 9 when wet entries are filtered out.
    """
    best = modelled.loc[
        modelled.groupby(["session_key", "driver_number"])["z_residual"].idxmin()]
    df = best.sort_values("z_residual").copy()
    if top_n:
        df = df.head(top_n)
    df.insert(0, "rank", range(1, len(df) + 1))

    dry = df["track_state"] == "dry"
    df["rank_dry"] = pd.Series(pd.NA, index=df.index, dtype="Int64")
    df.loc[dry, "rank_dry"] = range(1, int(dry.sum()) + 1)

    df["gap_to_expected"] = df["residual"]
    df["driver"] = df["full_name"].fillna(df["name_acronym"])

    cols = ["rank", "rank_dry", "session_key", "driver_number", "lap_number",
            "year", "meeting_name", "circuit_short_name",
            "driver", "name_acronym", "team_name", "track_state",
            "lap_duration", "predicted_lap", "residual", "z_residual",
            "resid_sd_session", "gap_to_expected",
            "session_median_lap", "lap_vs_median", "pct_of_median",
            "duration_sector_1", "duration_sector_2", "duration_sector_3",
            "i1_speed", "i2_speed", "st_speed",
            "compound", "tyre_age", "stint_number",
            "air_temperature", "track_temperature", "humidity", "pressure",
            "wind_speed", "wind_direction", "rainfall",
            "date_start"]
    out = df[cols].copy()
    for c in ("lap_duration", "predicted_lap", "residual", "z_residual",
              "resid_sd_session", "gap_to_expected",
              "session_median_lap", "lap_vs_median", "pct_of_median",
              "duration_sector_1", "duration_sector_2", "duration_sector_3"):
        out[c] = out[c].round(3)
    return out


def build_perfect_lap_model(fit, modelled: pd.DataFrame, ranked: pd.DataFrame,
                            vifs: dict, rare: list, bad: list) -> pd.DataFrame:
    """
    One row per predictor, plus a header row carrying the fit statistics.

    Dummy levels are collapsed to a single row per factor: 25 circuit
    coefficients are the machinery, not the finding, and listing them would
    bury the seven predictors a reader actually wants to check.
    """
    rows = [{
        "term": "MODEL",
        "kind": "fit",
        "coefficient": None, "std_error": None, "p_value": None,
        "ci_lower": None, "ci_upper": None, "vif": None,
        "note": (f"OLS, n={int(fit.nobs):,} clean race laps, "
                 f"R2={fit.rsquared:.3f}, adj R2={fit.rsquared_adj:.3f}, "
                 f"{len(fit.params)} parameters"),
    }]

    for name in NUMERIC_PREDICTORS:
        if name not in fit.params.index:
            continue
        conf = fit.conf_int().loc[name]
        rows.append({
            "term": name,
            "kind": "numeric",
            "coefficient": _round(fit.params[name], 4),
            "std_error": _round(fit.bse[name], 4),
            "p_value": _round(fit.pvalues[name], 6),
            "ci_lower": _round(conf[0], 4),
            "ci_upper": _round(conf[1], 4),
            "vif": _round(vifs.get(name), 3),
            "note": None,
        })

    for factor, label in (("C(circuit_short_name)", "circuit"),
                          ("C(year)", "year"),
                          ("C(compound)", "compound")):
        levels = [p for p in fit.params.index if p.startswith(factor)]
        if not levels:
            continue
        spread = fit.params[levels].max() - fit.params[levels].min()
        rows.append({
            "term": label,
            "kind": "fixed effect",
            "coefficient": None, "std_error": None, "p_value": None,
            "ci_lower": None, "ci_upper": None, "vif": None,
            "note": (f"{len(levels) + 1} levels absorbed, "
                     f"{spread:.3f}s between the fastest and slowest"),
        })

    if rare:
        rows.append({
            "term": "excluded compounds", "kind": "note",
            "coefficient": None, "std_error": None, "p_value": None,
            "ci_lower": None, "ci_upper": None, "vif": None,
            "note": f"seen on fewer than {MIN_COMPOUND_LAPS} clean laps: "
                    f"{', '.join(sorted(rare))}",
        })

    if bad:
        rows.append({
            "term": "excluded sessions", "kind": "note",
            "coefficient": None, "std_error": None, "p_value": None,
            "ci_lower": None, "ci_upper": None, "vif": None,
            "note": (f"session_key {', '.join(map(str, bad))} dropped: the "
                     f"whole field is recorded as starting on tyres older "
                     f"than {MAX_PLAUSIBLE_START_AGE} laps, against a median "
                     "of 0.1 laps across every other race. Bad stint "
                     "metadata, not a tyre strategy."),
        })

    # The two limits a reader should know before trusting the ranking. Both
    # are measured here rather than asserted, so they move when the data does.
    sd = modelled.groupby("session_key")["residual"].std()
    wet = modelled.groupby("session_key")["rainfall"].max() == 1
    rows.append({
        "term": "residual spread", "kind": "diagnostic",
        "coefficient": None, "std_error": None, "p_value": None,
        "ci_lower": None, "ci_upper": None, "vif": None,
        "note": (f"residual SD is {sd[~wet].mean():.2f}s in the average dry race "
                 f"and {sd[wet].mean():.2f}s in a wet one, up to {sd.max():.2f}s. "
                 "Residuals are divided by their own session's SD before "
                 "ranking, otherwise the leaderboard ranks races by chaos."),
    })

    base = (modelled["track_state"] == "wet").mean()
    head = ranked.head(200)
    shown = (head["track_state"] == "wet").mean()
    rows.append({
        "term": "wet-lap over-representation", "kind": "diagnostic",
        "coefficient": _round(shown / base if base else None, 2),
        "std_error": None, "p_value": None,
        "ci_lower": None, "ci_upper": None, "vif": None,
        "note": (f"wet laps are {100 * base:.1f}% of modelled laps but "
                 f"{100 * shown:.1f}% of the top 200. A 0/1 rainfall flag "
                 "cannot express how wet, so a drying track reads as driving. "
                 "Use rank_dry for a view without them."),
    })

    return pd.DataFrame(rows)


def build_perfect_lap_record(laps: pd.DataFrame) -> pd.DataFrame:
    """
    The unadjusted fastest clean lap at each circuit.

    This is the number a fan would look up, and it sits next to the model's
    answer so the two can be compared rather than one quietly replacing the
    other.
    """
    df = laps[laps["lap_duration"].notna()].copy()
    idx = df.groupby("circuit_short_name")["lap_duration"].idxmin()
    rec = df.loc[idx].copy()
    rec["driver"] = rec["full_name"].fillna(rec["name_acronym"])

    seasons = (df.groupby("circuit_short_name")["year"]
                 .nunique().rename("seasons_raced").reset_index())
    laps_n = (df.groupby("circuit_short_name").size()
                .rename("clean_laps").reset_index())

    rec = rec.merge(seasons, on="circuit_short_name").merge(
        laps_n, on="circuit_short_name")

    cols = ["circuit_short_name", "seasons_raced", "clean_laps",
            "lap_duration", "year", "meeting_name", "driver", "name_acronym",
            "team_name", "driver_number", "lap_number", "compound", "tyre_age",
            "session_key"]
    out = rec[cols].sort_values("lap_duration").reset_index(drop=True)
    out["lap_duration"] = out["lap_duration"].round(3)
    return out


# --- what made a lap what it was --------------------------------------------------

def attach_traffic(con, laps: pd.DataFrame) -> pd.DataFrame:
    """
    Gap to the car ahead at each lap's start, and what kind of traffic it is.

    Intervals are sampled about every four seconds, so this is the nearest
    reading at or before the lap began, the same convention s04 uses for
    fact_lap.

    THE NULL INTERVAL IS NOT CLEAR TRACK. This function used to filter
    interval_seconds IS NOT NULL and then fill every missing gap with the cap,
    which said "a lap behind" and "alone in front" were the same thing. They
    are the opposite. When the car ahead is a whole lap away the timing feed
    stops reporting seconds and reports interval_laps instead, so the filter
    was removing exactly the rows that identify out-of-position running, and
    the fill was then labelling them clean air. Those laps are +10.1s, the
    largest single effect in the model, and the old code buried all of it in
    the residual. The filter is gone and the lap-counted rows are flagged.

    WHAT out_of_position IS NOT. It is not a clean measure of traffic, and it
    is not named as one. It marks the 1.56% of laps where the feed reported the
    gap in laps rather than seconds, which happens when a car is caught by the
    leaders, but also when it is damaged, off the track, or limping to the pits.
    The three laps before the flag average +2.1s, +2.3s and +3.0s against a
    field baseline near +0.5s, so a car is already slow before the flag lands:
    part of this term is a consequence of a bad lap, not a cause of one. The
    median spell is one lap, so it is an event marker, not a state.

    It stays in the model anyway. Leaving it out does not make the 5.2% of
    variance disappear, it moves it into the residual, where the page calls the
    residual "the driver and the line". Mislabelled is worse than caveated.

    IT ALSO CHANGES WHAT gap_ahead MEANS. With the lapped rows filled to the
    cap, the model saw "large gap, slow lap" and read a dirty-air effect out of
    it: gap_ahead came out at -0.135 s per second with p < 1e-300. Once those
    rows are flagged separately, the gap effect on properly timed laps collapses
    to -0.009 s per second at p = 0.13, and dirty air to +0.08s. The earlier
    figure was the artefact, not the finding. A driver held up behind another
    car matches that car's pace; it does not make their lap slower than it.
    """
    intervals = read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT i.session_key, i.driver_number, i."date", i.interval_seconds,
               i.interval_laps, i.gap_to_leader_laps
        FROM scope JOIN silver_intervals i ON i.session_key = scope.session_key
        WHERE i.interval_seconds IS NOT NULL
           OR i.interval_laps    IS NOT NULL
    """, con)
    intervals["date"] = pd.to_datetime(intervals["date"], format="ISO8601",
                                       utc=True)

    laps = laps.copy()
    laps["_ts"] = pd.to_datetime(laps["date_start"], format="ISO8601",
                                 utc=True, errors="coerce")
    timed = laps[laps["_ts"].notna()].sort_values("_ts")
    untimed = laps[laps["_ts"].isna()]

    merged = pd.merge_asof(
        timed, intervals.sort_values("date"),
        left_on="_ts", right_on="date",
        by=["session_key", "driver_number"], direction="backward")

    out = pd.concat([merged, untimed], ignore_index=True)
    out["gap_ahead"] = out["interval_seconds"].clip(
        upper=GAP_CAP_SECONDS).fillna(GAP_CAP_SECONDS)
    out["in_dirty_air"] = (
        out["interval_seconds"] < DIRTY_AIR_SECONDS).fillna(False).astype(int)

    # Out of position: the car ahead is a lap or more away, so gap_ahead is a
    # filled-in cap rather than a measurement. The flag lets the model account
    # for the lap instead of trusting that number.
    out["out_of_position"] = (
        out["interval_laps"].notna() | out["interval_seconds"].isna()
    ).astype(int)

    # Being lapped is the other side of it and a separate cost: yielding to the
    # leaders is not the same as circulating at the back in clear air.
    out["being_lapped"] = out["gap_to_leader_laps"].notna().astype(int)

    return out.drop(columns=["_ts", "date"], errors="ignore")


def add_wind_components(laps: pd.DataFrame) -> pd.DataFrame:
    """
    Wind as two orthogonal components rather than a compass bearing.

    A bearing cannot be used as a number: 359 and 1 degree are neighbours, and
    a regression would treat them as opposite extremes. Splitting into
    components fixes that, but the deeper reason is orientation. Position
    coordinates are in each circuit's own frame and the rotation to compass
    north is unknown, so there is no way to say which way the pit straight
    points and therefore no way to compute a headwind directly.

    Crossing both components with circuit lets the model learn that rotation
    per track. It is the difference between a term worth nothing and the
    single most useful addition available: wind direction on its own adds
    0.0004 to R-squared, and crossed with circuit it adds 0.024.
    """
    laps = laps.copy()
    radians = np.radians(laps["wind_direction"].astype(float))
    laps["wind_u"] = laps["wind_speed"] * np.cos(radians)
    laps["wind_v"] = laps["wind_speed"] * np.sin(radians)
    return laps


def fit_factor_model(modelled: pd.DataFrame):
    """The within-race model, its ANOVA, and the VIFs behind it."""
    needed = ["lap_vs_median", "team_name", "gap_ahead", "in_dirty_air",
              "out_of_position", "being_lapped", "yellow_sector",
              "wind_u", "wind_v", "circuit_short_name"]
    df = modelled.dropna(subset=needed).copy()
    fit = smf.ols(FACTOR_FORMULA, data=df).fit()
    table = anova_lm(fit, typ=2)
    return fit, table, compute_vifs(df)


def build_lap_factor_anova(fit, table: pd.DataFrame) -> pd.DataFrame:
    """
    One row per factor: how much of within-race lap variation it explains.

    Type II sums of squares, so each factor is measured after every other
    factor is accounted for. That matters here because track and air
    temperature move together, and a sequential decomposition would hand
    whichever came first in the formula all of their shared credit.

    SHARE OF VARIANCE IS THE ANSWER, NOT THE P-VALUE. At 77,000 laps
    everything is significant: tyre age reaches p = 0.005 while explaining
    0.009% of the variance. The p column is kept because omitting it would
    look evasive, but `pct_variance` is the column that means something.

    SHARES ARE TAKEN AGAINST THE TRUE TOTAL, not the ANOVA table's total, and
    the difference is not cosmetic. Type II sums of squares do not partition
    the variance: what two correlated predictors share is credited to neither,
    so the table's own total falls short of the real one. Dividing by it
    reported the unexplained share as 93.4% when the honest figure, 1 - R2, is
    88.1%. The gap is carried as its own row rather than quietly absorbed,
    because 5.7% of lap-time variance genuinely cannot be assigned to any one
    factor: track and air temperature move together, and so do fuel load and
    tyre age within a stint.
    """
    total = float(fit.centered_tss)
    mse = float(fit.mse_resid)

    # Terms sharing a label are one factor to a reader: the two wind
    # components crossed with circuit are 48 parameters answering a single
    # question. Their sums of squares and degrees of freedom add, and the
    # joint F follows from the combined term against the model's own error,
    # which is the same test as comparing the model with and without all of
    # them at once.
    grouped: dict[str, dict] = {}
    for term, r in table.iterrows():
        if term == "Residual":
            continue
        label = FACTOR_LABELS.get(term, term)
        slot = grouped.setdefault(label, {"terms": [], "sum_sq": 0.0, "df": 0})
        slot["terms"].append(term)
        slot["sum_sq"] += float(r.sum_sq)
        slot["df"] += int(r.df)

    rows = []
    for label, slot in grouped.items():
        f_stat = (slot["sum_sq"] / slot["df"]) / mse if slot["df"] else None
        p = (float(stats.f.sf(f_stat, slot["df"], fit.df_resid))
             if f_stat is not None else None)
        rows.append({
            "term": " + ".join(slot["terms"]),
            "factor": label,
            "sum_sq": _round(slot["sum_sq"], 2),
            "df": slot["df"],
            "f_statistic": _round(f_stat, 3),
            "p_value": _round(p, 8),
            "pct_variance": _round(100 * slot["sum_sq"] / total, 4),
            "is_residual": 0,
        })

    unique = sum(r["pct_variance"] for r in rows)
    unexplained = 100.0 * float(fit.ssr) / total
    rows.append({
        "term": "Shared", "factor": "Shared between correlated factors",
        "sum_sq": _round(total - fit.ssr
                         - table.sum_sq.drop("Residual").sum(), 2),
        "df": 0, "f_statistic": None, "p_value": None,
        "pct_variance": _round(100.0 - unique - unexplained, 4),
        "is_residual": 0,
    })
    rows.append({
        "term": "Residual", "factor": FACTOR_LABELS["Residual"],
        "sum_sq": _round(fit.ssr, 2), "df": int(fit.df_resid),
        "f_statistic": None, "p_value": None,
        "pct_variance": _round(unexplained, 4), "is_residual": 1,
    })

    out = pd.DataFrame(rows).sort_values("pct_variance", ascending=False)
    out.insert(0, "rank", range(1, len(out) + 1))
    out["model_r_squared"] = _round(fit.rsquared, 4)
    out["model_n"] = int(fit.nobs)
    return out.reset_index(drop=True)


def build_lap_factor_model(fit, vifs: dict) -> pd.DataFrame:
    """
    Every coefficient, including each compound level.

    The app needs all of them: a lap's contribution from a factor is its
    coefficient times that lap's value, so a table that collapses the dummy
    levels into a summary row cannot decompose anything. This is the one
    place where the full parameter list has to ship.
    """
    conf = fit.conf_int()
    rows = []
    for name in fit.params.index:
        base = name.split("[")[0] if name.startswith("C(") else name
        rows.append({
            "term": name,
            "factor": FACTOR_LABELS.get(base, base),
            "kind": "level" if "[" in name else
                    ("intercept" if name == "Intercept" else "numeric"),
            "coefficient": _round(fit.params[name], 6),
            "std_error": _round(fit.bse[name], 6),
            "p_value": _round(fit.pvalues[name], 8),
            "ci_lower": _round(conf.loc[name, 0], 6),
            "ci_upper": _round(conf.loc[name, 1], 6),
            "vif": _round(vifs.get(name), 3),
        })
    return pd.DataFrame(rows)


def build_lap_factor_reference(modelled: pd.DataFrame) -> pd.DataFrame:
    """
    The typical value of each factor within each race.

    A contribution only means something against a baseline, and the baseline
    that matches the question is "a normal lap in this same race". Fifty laps
    of fuel is unremarkable at lap 50 and extraordinary at lap 5, so the
    reference is per session rather than global.
    """
    df = modelled.dropna(subset=["lap_vs_median"])
    cols = NUMERIC_PREDICTORS + ["gap_ahead", "in_dirty_air", "out_of_position",
                                 "being_lapped", "yellow_sector",
                                 "wind_u", "wind_v"]
    numeric = (df.groupby("session_key")[cols]
                 .median().reset_index()
                 .melt(id_vars="session_key", var_name="term",
                       value_name="reference_value"))
    numeric["reference_level"] = None

    levels = []
    for column, term in (("compound", "compound"),
                         ("circuit_short_name", "circuit")):
        frame = (df.groupby("session_key")[column]
                   .agg(lambda s: s.mode().iat[0] if len(s.mode()) else None)
                   .reset_index().rename(columns={column: "reference_level"}))
        frame["term"] = term
        frame["reference_value"] = None
        levels.append(frame[["session_key", "term", "reference_value",
                             "reference_level"]])

    out = pd.concat([numeric] + levels, ignore_index=True)
    out["reference_value"] = out["reference_value"].round(4)
    return out.sort_values(["session_key", "term"]).reset_index(drop=True)


# --- the counterfactual: what could have been different ---------------------------
#
# Section 3 asks a different question from the ANOVA above it, and a different
# question needs a different identification strategy.
#
# THE ANOVA ASKS how much of the variance each factor accounts for, pooled over
# every lap of four seasons. That is the right design for "what matters".
#
# A COUNTERFACTUAL ASKS what would have happened had one thing been different,
# which is a causal claim, and the pooled model cannot support it for anything
# a team chooses. Compounds are not assigned at random: a fresh hard appears at
# 36% race distance, a fresh soft at 4%. So "fresh soft against fresh hard" in
# the pooled model is mostly "full tank against half tank", and adding fuel
# terms does not repair an unbalanced comparison, it just buries it.
#
# THE FIX IS TO COMPARE CARS ON THE SAME LAP OF THE SAME RACE. Every car on a
# given lap shares the fuel load, the track state, the weather, the circuit and
# the safety car phase, so subtracting the field's mean on that lap removes all
# of them at once, without estimating a parameter for any of them. What is left
# is what actually differed between the cars, which is what a "what if we had
# done X" question is about.
#
# WHAT THIS COSTS is the weather. A factor that is identical for every car on a
# lap is differenced away to nothing, so this model cannot see rain or wind at
# all. That is not a defect, it is the division of labour: conditions were never
# a choice, and they are reported from the pooled model as circumstance rather
# than as advice.
#
# WHAT IT FOUND, and it was not what I expected. The pooled model says a fresh
# soft is 0.229s slower than a fresh hard, which looked like the confound above.
# It is not. Within-lap it is 0.248s, and a model-free check on the 1,024
# race-laps where a soft is running and is no older than the hard gives 0.273s.
# Three routes, one answer. In race trim the soft really is the slower tyre,
# because a race soft is managed, not attacked. The pooled estimate was mildly
# biased; the sign was right all along.

WITHIN_FORMULA = (
    "dev ~ C(compound)*tyre_age + C(team_name) + gap_ahead + in_dirty_air"
    " + out_of_position + being_lapped + yellow_sector"
)

# A lap of a race where the feed recorded almost nobody says nothing about how
# cars differed from each other, and with one car the deviation is zero by
# construction.
MIN_CARS_ON_LAP = 5

# Which block of section 3 a lever belongs in. The split is not cosmetic: the
# first group is identified within-lap and can carry a causal reading, the
# second is identified only in the pooled model and cannot.
CHOICE_TERMS = ["compound", "tyre_age", "gap_ahead", "in_dirty_air",
                "out_of_position", "being_lapped"]
CONDITION_TERMS = ["rainfall", "track_temperature", "air_temperature",
                   "humidity", "wind_speed", "wind_direction", "lap_number",
                   "yellow_sector"]


def add_field_deviation(laps: pd.DataFrame) -> pd.DataFrame:
    """Each lap against the mean of every car running that same lap."""
    out = laps.copy()
    grp = out.groupby(["session_key", "lap_number"])["lap_vs_median"]
    out["field_mean"] = grp.transform("mean")
    out["cars_on_lap"] = grp.transform("size")
    out["dev"] = out["lap_vs_median"] - out["field_mean"]
    return out[out["cars_on_lap"] >= MIN_CARS_ON_LAP]


def fit_within_lap_model(modelled: pd.DataFrame):
    """The same-lap comparison behind every lever a team could have pulled."""
    df = add_field_deviation(modelled).dropna(
        subset=["dev", "compound", "tyre_age", "team_name", "gap_ahead"])
    return smf.ols(WITHIN_FORMULA, data=df).fit(), df


def build_counterfactual_model(within, pooled) -> pd.DataFrame:
    """
    Every lever section 3 can pull, with where its coefficient came from.

    Two sources in one table, and the `identification` column is the whole
    point. A reader moving the tyre slider is looking at a number estimated
    against cars on the same lap; a reader moving the rain slider is looking at
    a pooled association. Both are useful, they are not the same kind of claim,
    and the page must not let them look alike.
    """
    rows = []

    def emit(fit, name, group, identification):
        if name not in fit.params.index:
            return
        conf = fit.conf_int()
        # An interaction has a label of its own when one exists, and falls back
        # to its left-hand term when it does not. Without the first case the 48
        # wind terms report as "C(circuit_short_name)" instead of "Wind
        # direction, per circuit"; without the second, compound-by-tyre-age
        # reports as a formula fragment.
        if ":" in name:
            left, right = name.split(":", 1)
            full = f"{left.split('[')[0]}:{right.split('[')[0]}"
            base = full if full in FACTOR_LABELS else left.split("[")[0]
        else:
            base = name.split("[")[0] if name.startswith("C(") else name
        rows.append({
            "term": name,
            "factor": FACTOR_LABELS.get(base, base),
            "lever_group": group,
            "identification": identification,
            "kind": ("interaction" if ":" in name else
                     "level" if "[" in name else
                     "intercept" if name == "Intercept" else "numeric"),
            "coefficient": _round(fit.params[name], 6),
            "std_error": _round(fit.bse[name], 6),
            "p_value": _round(fit.pvalues[name], 8),
            "ci_lower": _round(conf.loc[name, 0], 6),
            "ci_upper": _round(conf.loc[name, 1], 6),
        })

    for name in within.params.index:
        if name == "Intercept":
            continue
        stem = name.split("[")[0].split(":")[0].replace("C(", "").rstrip(")")
        if stem in CHOICE_TERMS or "compound" in name or "tyre_age" in name:
            emit(within, name, "choice", "within-lap")

    for name in pooled.params.index:
        stem = name.split("[")[0].split(":")[0].replace("C(", "").rstrip(")")
        if stem in CONDITION_TERMS or "wind_" in name:
            emit(pooled, name, "condition", "pooled")

    out = pd.DataFrame(rows)
    out["within_r_squared"] = _round(within.rsquared, 4)
    out["within_n"] = int(within.nobs)
    return out


def build_counterfactual_bounds(modelled: pd.DataFrame) -> pd.DataFrame:
    """
    How far each lever may be moved, per race, and how far it has ever gone.

    A counterfactual is only worth reading inside the range the data covers.
    Two ranges ship, because they answer two different questions: what this
    race actually saw, and what has ever been recorded anywhere. The second is
    what allows a combination no driver has run, while still refusing to
    extrapolate the model into a track temperature that has never existed.
    """
    terms = [t for t in (CHOICE_TERMS + CONDITION_TERMS)
             if t in modelled.columns and t != "compound"]
    rows = []

    overall = modelled[terms].apply(pd.to_numeric, errors="coerce")
    for term in terms:
        s = overall[term].dropna()
        if s.empty:
            continue
        rows.append({"session_key": None, "term": term,
                     "low": _round(s.min()), "high": _round(s.max()),
                     "typical": _round(s.median()), "scope": "ever recorded"})

    for session_key, g in modelled.groupby("session_key"):
        num = g[terms].apply(pd.to_numeric, errors="coerce")
        for term in terms:
            s = num[term].dropna()
            if s.empty:
                continue
            rows.append({"session_key": int(session_key), "term": term,
                         "low": _round(s.min()), "high": _round(s.max()),
                         "typical": _round(s.median()), "scope": "this race"})

    return pd.DataFrame(rows)


# --- perfect race ----------------------------------------------------------------

def build_perfect_race(con, laps: pd.DataFrame) -> pd.DataFrame:
    """
    Four components of a great race, each ranked on its own.

    Deliberately NOT combined. The four measure different things and weighting
    them would smuggle an opinion into a page that promises tests:

      places_gained   grid position minus finish position
      pace_vs_field   mean clean lap minus that session's median, negative
                      is faster. Normalised per session so Monaco and Monza
                      are on the same scale.
      consistency     the driver's lap-time spread against the spread the
                      rest of the field managed in that same race, so 1.00
                      is exactly average and below 1.00 is steadier
      incident_free   1 when the driver finished, was not disqualified, and
                      drew no race control message naming them

    A driver needs at least ten clean laps to appear, because a standard
    deviation over three laps is noise with a decimal point.

    Consistency is measured against the field rather than in raw seconds for
    the same reason the lap ranking is studentised. A safety-car-shortened
    race leaves everyone with a handful of similar laps and a tiny spread:
    on the raw measure the 2025 Miami Grand Prix took four of the five
    steadiest drives in four seasons, which is a fact about that afternoon's
    lap count, not about the driving.

    The four are reported side by side and never summed, but they are not
    fully independent either: pace and consistency correlate because a slow
    car spends the race in traffic, which costs both. Two components pointing
    the same way is worth knowing before reading them as separate evidence.
    """
    res = read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT r.session_key, r.driver_number, d.team_name, d.full_name,
               d.name_acronym, s.year, m.meeting_name, s.circuit_short_name,
               g."position" AS grid_position,
               r."position" AS finish_position,
               r.dnf, r.dns, r.dsq, r.points
        FROM scope
        JOIN silver_session_result r ON r.session_key = scope.session_key
        JOIN silver_sessions s ON s.session_key = scope.session_key
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        JOIN silver_drivers d
          ON d.session_key = r.session_key AND d.driver_number = r.driver_number
        LEFT JOIN silver_sessions q
          ON q.meeting_key = s.meeting_key AND q.session_name = 'Qualifying'
        LEFT JOIN silver_starting_grid g
          ON g.session_key = q.session_key AND g.driver_number = r.driver_number
    """, con)
    res = normalize_teams(res)

    pace = (laps.groupby(["session_key", "driver_number"])
                .agg(mean_clean_lap=("lap_duration", "mean"),
                     lap_sd=("lap_duration", "std"),
                     clean_laps=("lap_duration", "size"),
                     session_median_lap=("session_median_lap", "first"))
                .reset_index())
    pace["pace_vs_field"] = pace["mean_clean_lap"] - pace["session_median_lap"]

    # Percent rather than seconds, so a slow circuit does not dominate.
    pace["pace_vs_field_pct"] = 100.0 * pace["pace_vs_field"] / pace["session_median_lap"]
    pace["lap_sd_pct"] = 100.0 * pace["lap_sd"] / pace["session_median_lap"]

    df = res.merge(pace, on=["session_key", "driver_number"], how="inner")
    df = df[df["clean_laps"] >= 10].copy()

    # Ratio to the field's own spread that day. See the docstring.
    df["session_typical_sd"] = df.groupby("session_key")["lap_sd_pct"].transform("median")
    df["consistency"] = df["lap_sd_pct"] / df["session_typical_sd"]

    flagged = read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT rc.session_key, rc.driver_number, COUNT(*) AS rc_messages
        FROM scope JOIN silver_race_control rc ON rc.session_key = scope.session_key
        WHERE rc.driver_number IS NOT NULL
        GROUP BY rc.session_key, rc.driver_number
    """, con)
    df = df.merge(flagged, on=["session_key", "driver_number"], how="left")
    df["rc_messages"] = df["rc_messages"].fillna(0).astype(int)

    df["places_gained"] = df["grid_position"] - df["finish_position"]
    df["incident_free"] = ((df["dnf"] == 0) & (df["dsq"] == 0)
                           & (df["rc_messages"] == 0)).astype(int)
    df["driver"] = df["full_name"].fillna(df["name_acronym"])

    df["rank_places_gained"] = df["places_gained"].rank(ascending=False,
                                                        method="min")
    df["rank_pace_vs_field"] = df["pace_vs_field_pct"].rank(ascending=True,
                                                            method="min")
    df["rank_consistency"] = df["consistency"].rank(ascending=True, method="min")

    for c in ("rank_places_gained", "rank_pace_vs_field", "rank_consistency"):
        df[c] = df[c].astype("Int64")

    cols = ["session_key", "driver_number", "year", "meeting_name",
            "circuit_short_name", "driver", "name_acronym", "team_name",
            "grid_position", "finish_position", "points",
            "places_gained", "rank_places_gained",
            "pace_vs_field", "pace_vs_field_pct", "rank_pace_vs_field",
            "consistency", "rank_consistency", "lap_sd_pct", "session_typical_sd",
            "lap_sd", "mean_clean_lap", "session_median_lap", "clean_laps",
            "incident_free", "rc_messages", "dnf", "dsq"]
    out = df[cols].copy()
    for c in ("pace_vs_field", "pace_vs_field_pct", "consistency", "lap_sd",
              "lap_sd_pct", "session_typical_sd",
              "mean_clean_lap", "session_median_lap"):
        out[c] = out[c].round(4)
    return out.sort_values(["year", "meeting_name", "driver"]).reset_index(drop=True)


# --- runner ----------------------------------------------------------------------

# Bundled: the app queries these, so they go into the dashboard bundle.
BUNDLED = ["lap_factor_anova", "lap_factor_model", "lap_factor_reference",
           "lap_counterfactual_model", "lap_counterfactual_bounds"]

# Not bundled, and no longer written by default: nothing reads these. Checked
# against every way a name can reach a query (FROM, JOIN, subquery, f-string,
# quoted, bare), across the app, the pipeline and the notebooks.
#
# They were kept for a while on the grounds that a choose-a-lap feature would
# want them. That feature exists now. views/prescribe.py was built on fact_lap,
# the map geometry and the lap_factor_* tables instead, so the reader these were
# waiting for arrived and did not use them. 3,157 rows and 793 KB were being
# written every run for nobody.
#
# The builders stay, because the modelling in them is sound and the cost of
# keeping unreferenced code is a great deal lower than rewriting it. They are
# reachable on request:
#
#     python pipeline\s05b_prescriptive.py --tables perfect_lap
#
# THEIR NAMES DID NOT CHANGE when this file became s05b_prescriptive.py, and
# that is deliberate rather than an oversight. These four rank the fastest lap
# and the best race ever recorded. A superlative is a descriptive question, not
# a prescriptive one, and nothing prescribes anything here. Renaming them to
# match the file would put a wrong label on a right thing.
#
# which writes CSV to serving.ANALYSIS_DIR. A plain run writes only BUNDLED.
ON_REQUEST = ["perfect_lap", "perfect_lap_model", "perfect_lap_record",
              "perfect_race"]

# Every name --tables will accept. DEFAULT_TABLES is what a plain run writes.
TABLES = ON_REQUEST + BUNDLED
DEFAULT_TABLES = BUNDLED


def main() -> int:
    ap = argparse.ArgumentParser(description="Find the perfect lap and race.")
    ap.add_argument("--tables", nargs="*", default=None,
                    help=f"subset of output tables to write. A plain run writes "
                         f"{DEFAULT_TABLES}. Also available on request, written "
                         f"as CSV to the analysis folder: {ON_REQUEST}")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                    help=f"how many ranked laps to keep (default {DEFAULT_TOP_N})")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and report without writing anything")
    ap.add_argument("--csv", action="store_true",
                    help="also write the bundled tables as CSV, to read by eye")
    args = ap.parse_args()

    targets = args.tables or DEFAULT_TABLES
    unknown = [t for t in targets if t not in TABLES]
    if unknown:
        print(f"[FAIL] unknown table(s): {unknown}. Valid: {TABLES}")
        return 1

    if not DB_PATH.exists():
        print(f"[FAIL] silver database not found at {DB_PATH}")
        return 1

    generated_at = datetime.now(timezone.utc).isoformat()

    print("=" * 74)
    print("PERFECT LAP AND PERFECT RACE")
    print(f"silver: {DB_PATH}")
    print(f"bundle:   {serving.BUNDLE_DB}")
    # Only named when something is actually going there, so a plain run does not
    # advertise a folder it will not create.
    if any(t in ON_REQUEST for t in targets):
        print(f"analysis: {serving.ANALYSIS_DIR}")
    print(f"python: {sys.version.split()[0]}  pandas {pd.__version__}")
    print("=" * 74)

    con = duckdb.connect(str(DB_PATH), read_only=True)

    t0 = time.time()
    laps = load_laps(con)
    print(f"\nclean race laps:      {len(laps):>8,}  ({time.time() - t0:.1f}s)")

    t0 = time.time()
    laps = attach_weather(con, laps)
    print(f"with a weather sample: {laps['track_temperature'].notna().sum():>8,}"
          f"  ({time.time() - t0:.1f}s)")

    t0 = time.time()
    laps = add_wind_components(attach_traffic(con, laps))
    print(f"with a gap to the car ahead: "
          f"{int((laps['gap_ahead'] < GAP_CAP_SECONDS).sum()):>8,}"
          f"  ({time.time() - t0:.1f}s)")

    t0 = time.time()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit, modelled, rare, bad = fit_lap_model(laps)
        vifs = compute_vifs(modelled)
    print(f"modelled:             {len(modelled):>8,}  "
          f"R2={fit.rsquared:.3f}  ({time.time() - t0:.1f}s)")
    if rare:
        print(f"  compounds excluded as too rare: {sorted(rare)}")
    if bad:
        print(f"  sessions dropped for implausible stint metadata: {bad}")
    worst_vif = max(vifs.values()) if vifs else float("nan")
    print(f"  peak VIF among numeric predictors: {worst_vif:.2f}")

    t0 = time.time()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ffit, ftable, fvifs = fit_factor_model(modelled)
    print(f"within-race model:    R2={ffit.rsquared:.3f}, "
          f"{100 * (1 - ffit.rsquared):.1f}% unexplained, "
          f"{len(ffit.params)} params  ({time.time() - t0:.1f}s)")

    t0 = time.time()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wfit, wdf = fit_within_lap_model(modelled)
    print(f"within-lap model:     R2={wfit.rsquared:.3f}, "
          f"n={int(wfit.nobs):,}, {len(wfit.params)} params  "
          f"({time.time() - t0:.1f}s)")
    fresh = {c: wfit.params.get(f"C(compound)[T.{c}]")
             for c in ("MEDIUM", "SOFT", "INTERMEDIATE")}
    print("  a fresh tyre against a fresh HARD: "
          + ", ".join(f"{k} {v:+.3f}s" for k, v in fresh.items()
                      if v is not None))

    frames = {
        "lap_factor_anova": build_lap_factor_anova(ffit, ftable),
        "lap_factor_model": build_lap_factor_model(ffit, fvifs),
        "lap_factor_reference": build_lap_factor_reference(modelled),
        "lap_counterfactual_model": build_counterfactual_model(wfit, ffit),
        "lap_counterfactual_bounds": build_counterfactual_bounds(modelled),
    }

    # Built only when asked for. build_perfect_race re-queries the database and
    # the ranking sorts every clean lap in four seasons, so a plain run should
    # not pay for output it is not going to write.
    ranked = None
    if any(t in ON_REQUEST for t in targets):
        ranked = build_perfect_lap(modelled, args.top)
        frames.update({
            "perfect_lap": ranked,
            "perfect_lap_model": build_perfect_lap_model(fit, modelled, ranked,
                                                         vifs, rare, bad),
            "perfect_lap_record": build_perfect_lap_record(laps),
            "perfect_race": build_perfect_race(con, laps),
        })
    con.close()

    if ranked is not None:
        head = ranked.head(200)
        print(f"  candidates (one per driver-race): {len(ranked):,}")
        print(f"  top 200 spans {head.session_key.nunique()} races and "
              f"{head.driver_number.nunique()} drivers, "
              f"{(head.track_state == 'wet').mean() * 100:.0f}% wet")

        for label, row in (
                ("overall", ranked.iloc[0]),
                ("dry only", ranked[ranked.track_state == "dry"].iloc[0])):
            print(f"\n  perfect lap, {label}: {row.driver}, "
                  f"{row.meeting_name} {row.year}, lap {row.lap_number}")
            print(f"    {row.lap_duration:.3f}s against an expected "
                  f"{row.predicted_lap:.3f}s, {row.residual:+.3f}s "
                  f"(z {row.z_residual:+.2f})")
            print(f"    {row.compound} on a {int(row.tyre_age)}-lap tyre, track "
                  f"{row.track_temperature}C, wind {row.wind_speed} m/s, "
                  f"{row.track_state}")

    print()
    out = None if args.dry_run else serving.connect()
    for name in targets:
        df = frames[name].copy()
        df["generated_at"] = generated_at
        where = "bundle" if name in BUNDLED else "analysis"
        if out is not None:
            if name in BUNDLED:
                serving.write_table(df, name, out, csv=args.csv)
            else:
                serving.write_analysis_csv(df, name)
        print(f"  {name:26s} {len(df):>6,} rows x {len(df.columns):>2} cols"
              + (f"   -> {where}" if not args.dry_run else
                 "   (dry run, not written)"))
    if out is not None:
        out.commit()
        out.close()

    print("\n" + "=" * 74)
    print("Run s06_publish.py --execute to push these to the data-latest release.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
