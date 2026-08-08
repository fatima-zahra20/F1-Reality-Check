"""
s05b_perfect.py - finds the best lap and the best race, 2023 to 2026.

Runs between s05 and s06 so the existing publish command is unchanged.

Produces four CSVs in outputs/dashboard/:

    perfect_lap         the ranked race laps, with every parameter attached
    perfect_lap_model   the model behind the ranking, one row per predictor
    perfect_lap_record  raw fastest clean lap per circuit, for comparison
    perfect_race        the four components of a great race, ranked separately

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

READ-ONLY ON SILVER AND BRONZE. gold_f1.db is untouched.

Usage
-----
    python pipeline\\s05b_perfect.py
    python pipeline\\s05b_perfect.py --tables perfect_lap
    python pipeline\\s05b_perfect.py --top 200

Requires the pinned Anaconda environment. See NOTES_LOG #42.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH, OUTPUTS_DIR  # noqa: E402

import statsmodels.formula.api as smf  # noqa: E402
from statsmodels.stats.outliers_influence import variance_inflation_factor  # noqa: E402

DASHBOARD_DIR = OUTPUTS_DIR / "dashboard"

TEAM_NAME_MAP = {
    "AlphaTauri": "RB Family",
    "RB": "RB Family",
    "Racing Bulls": "RB Family",
    "Alfa Romeo": "Sauber Family",
    "Kick Sauber": "Sauber Family",
    "Audi": "Sauber Family",
}

LAP_OUTLIER_FACTOR = 2.0

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

# Same dynamic scope as s04 and s05, so all three cover the same races.
RACE_SCOPE = """
    SELECT s.session_key, s.meeting_key
    FROM silver_sessions s
    WHERE s.session_name = 'Race'
      AND s.is_cancelled = 0
      AND s.date_start < datetime('now')
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
    laps = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT l.session_key, l.driver_number, l.lap_number,
               l.date_start, l.lap_duration,
               l.duration_sector_1, l.duration_sector_2, l.duration_sector_3,
               l.i1_speed, l.i2_speed, l.st_speed,
               d.team_name, d.full_name, d.name_acronym,
               s.year, s.circuit_short_name,
               m.meeting_name,
               st.compound, st.stint_number, st.tyre_age_at_start, st.lap_start
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
    wx = pd.read_sql(f"""
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
    res = pd.read_sql(f"""
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

    flagged = pd.read_sql(f"""
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

TABLES = ["perfect_lap", "perfect_lap_model", "perfect_lap_record", "perfect_race"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Find the perfect lap and race.")
    ap.add_argument("--tables", nargs="*", default=None,
                    help=f"subset of output tables to write {TABLES}")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                    help=f"how many ranked laps to keep (default {DEFAULT_TOP_N})")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and report without writing the CSVs")
    args = ap.parse_args()

    targets = args.tables or TABLES
    unknown = [t for t in targets if t not in TABLES]
    if unknown:
        print(f"[FAIL] unknown table(s): {unknown}. Valid: {TABLES}")
        return 1

    if not DB_PATH.exists():
        print(f"[FAIL] silver database not found at {DB_PATH}")
        return 1

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    print("=" * 74)
    print("PERFECT LAP AND PERFECT RACE")
    print(f"silver: {DB_PATH}")
    print(f"csv:    {DASHBOARD_DIR}")
    print(f"python: {sys.version.split()[0]}  pandas {pd.__version__}")
    print("=" * 74)

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    t0 = time.time()
    laps = load_laps(con)
    print(f"\nclean race laps:      {len(laps):>8,}  ({time.time() - t0:.1f}s)")

    t0 = time.time()
    laps = attach_weather(con, laps)
    print(f"with a weather sample: {laps['track_temperature'].notna().sum():>8,}"
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

    ranked = build_perfect_lap(modelled, args.top)
    frames = {
        "perfect_lap": ranked,
        "perfect_lap_model": build_perfect_lap_model(fit, modelled, ranked,
                                                     vifs, rare, bad),
        "perfect_lap_record": build_perfect_lap_record(laps),
        "perfect_race": build_perfect_race(con, laps),
    }
    con.close()

    head = ranked.head(200)
    print(f"  candidates (one per driver-race): {len(ranked):,}")
    print(f"  top 200 spans {head.session_key.nunique()} races and "
          f"{head.driver_number.nunique()} drivers, "
          f"{(head.track_state == 'wet').mean() * 100:.0f}% wet")

    for label, row in (("overall", ranked.iloc[0]),
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
    for name in targets:
        df = frames[name].copy()
        df["generated_at"] = generated_at
        if not args.dry_run:
            df.to_csv(DASHBOARD_DIR / f"{name}.csv", index=False)
        print(f"  {name:20s} {len(df):>6,} rows x {len(df.columns):>2} cols"
              + ("   (dry run, not written)" if args.dry_run else ""))

    print("\n" + "=" * 74)
    print("Run s06_publish.py --execute to push these to the data-latest release.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
