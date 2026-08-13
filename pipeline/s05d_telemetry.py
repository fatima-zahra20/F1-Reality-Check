"""
s05d_telemetry.py - DRS and the tow, on the races that actually carry telemetry.

Runs after s05c and before s06.

Produces two CSVs in outputs/dashboard/:

    telemetry_tow      top speed and DRS usage by gap to the car ahead
    telemetry_effect   coefficients and the diagnostics that qualify them

Why this is a separate step
--------------------------
SIX RACES OF EIGHTY-ONE. car_data exists for Bahrain, Saudi Arabian, Spanish and
Belgian 2023 and Monaco and Hungarian 2026, and for nothing else. That is not a
data limitation, it is an ingestion one: OpenF1 answers a whole-session request
with HTTP 422 and the rest has never been paged per driver. Until it is, DRS and
the tow cannot enter the within-race model in s05b, which is fitted across all
81 races. Adding them there would drop 95% of the laps to gain two columns.

So they are measured here, on their own, and the page says how many races that
is. A finding on 3,895 laps of six races is worth reporting. It is not worth
smuggling into a table labelled "every race".

ERS IS NOT HERE BECAUSE IT IS NOT ANYWHERE. car_data has exactly six channels:
throttle, rpm, brake, speed, n_gear, drs. There is no battery state, no
deployment, no harvest, and no other OpenF1 endpoint carrying them. ERS
management is a real driver of lap time and this source cannot see it.
Reconstructing it from rpm would be a guess presented as a measurement.

DRS CODES ARE PARTLY UNDOCUMENTED. 0 to 3 mean the flap is closed, 8 means
detected and eligible but not yet open, and 10, 12 and 14 mean open. 9, 11, 13
and 15 appear in the data and are not documented anywhere reliable; they are
0.8% of samples. "Open" is taken as 10 and above, and the ambiguity is written
into telemetry_effect rather than left for a reader to discover.

DRS USAGE IS NOT A FREE VARIABLE. It correlates 0.575 with running inside 1.5s,
because DRS is only available when you are within a second of the car ahead. Its
coefficient is therefore partly traffic restated, and the note says so.

TOP SPEED IS AN OUTCOME, NOT A CAUSE. It is measured on the same lap it would be
explaining. It is reported in telemetry_tow as evidence of the tow, which is
what it is good for, and deliberately kept out of any model of lap time.

READ-ONLY ON SILVER AND BRONZE. Nothing is written back to either.

Usage
-----
    python pipeline\\s05d_telemetry.py
    python pipeline\\s05d_telemetry.py --dry-run

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
import statsmodels.formula.api as smf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (BRONZE_DB_PATH, DB_PATH,  # noqa: E402
                    LAP_OUTLIER_FACTOR, OUTPUTS_DIR)

DASHBOARD_DIR = OUTPUTS_DIR / "dashboard"

# 10 and above means the flap is open. See the module docstring.
DRS_OPEN_FROM = 10

# A lap with fewer samples than this is a partial trace, and a DRS share
# computed from a handful of readings is noise wearing a decimal point.
MIN_SAMPLES_PER_LAP = 100

# Matches s05b, so the two tables describe traffic the same way.
GAP_CAP_SECONDS = 10.0
DIRTY_AIR_SECONDS = 1.5

# LAP_OUTLIER_FACTOR is imported from config, which is the single definition.

GAP_BUCKETS = [0, 1, 1.5, 2, 3, 5, GAP_CAP_SECONDS + 0.01]
GAP_LABELS = ["under 1s", "1 to 1.5s", "1.5 to 2s", "2 to 3s", "3 to 5s",
              "over 5s"]

VALID_COMPOUNDS = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE"]

MODEL_FORMULA = (
    "lap_vs_median ~ C(compound) + tyre_age + lap_number + C(team_name)"
    " + C(session_key) + gap_ahead + in_dirty_air + out_of_position"
    " + yellow_sector"
)

TABLES = ["telemetry_tow", "telemetry_effect"]


def _round(v, nd=4):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    return round(float(v), nd)


# --- loaders ---------------------------------------------------------------------

def telemetry_races(silver, bronze) -> list[int]:
    """Race sessions that have car_data. Read every run, never hardcoded."""
    races = pd.read_sql("""
        SELECT session_key FROM silver_sessions
        WHERE session_name = 'Race' AND is_cancelled = 0
    """, silver).session_key.astype(int)
    have = pd.read_sql("SELECT DISTINCT session_key FROM car_data", bronze)
    have = set(pd.to_numeric(have.session_key, errors="coerce")
                 .dropna().astype(int))
    return sorted(set(races) & have)


def load_laps(silver, keys: list[int]) -> pd.DataFrame:
    """
    Clean race laps on the telemetry races, with the same filters as s05b.

    Same definition of clean, same traffic flags, same outlier bound. If this
    diverged from s05b the two tables would not be comparable, which is the
    only reason for the duplication.
    """
    inlist = ",".join(str(k) for k in keys)
    laps = pd.read_sql(f"""
        SELECT l.session_key, l.driver_number, l.lap_number, l.date_start,
               l.lap_duration, d.team_name, d.full_name, d.name_acronym,
               s.circuit_short_name, s.year,
               st.compound, st.tyre_age_at_start, st.lap_start,
               st.stint_number, f.yellow_sector_flag
        FROM silver_laps l
        JOIN silver_lap_flags f
          ON  f.session_key   = l.session_key
          AND f.driver_number = l.driver_number
          AND f.lap_number    = l.lap_number
        JOIN silver_drivers d
          ON  d.session_key   = l.session_key
          AND d.driver_number = l.driver_number
        JOIN silver_sessions s ON s.session_key = l.session_key
        LEFT JOIN silver_stints st
          ON  st.session_key   = l.session_key
          AND st.driver_number = l.driver_number
          AND st.lap_start    <= l.lap_number
          AND st.lap_end      >= l.lap_number
        WHERE l.session_key IN ({inlist})
          AND l.lap_duration IS NOT NULL
          AND f.neutralised = 0
          AND COALESCE(l.is_pit_out_lap, 0) = 0
    """, silver)

    # The in-lap matches two stints; the lower number is the tyre it was driven
    # on. Same resolution as s05b.load_laps.
    laps = (laps.sort_values(["session_key", "driver_number", "lap_number",
                              "stint_number"])
                .drop_duplicates(["session_key", "driver_number", "lap_number"],
                                 keep="first"))

    med = laps.groupby("session_key").lap_duration.transform("median")
    laps = laps[laps.lap_duration <= LAP_OUTLIER_FACTOR * med].copy()
    laps["session_median_lap"] = laps.groupby(
        "session_key").lap_duration.transform("median")
    laps["lap_vs_median"] = laps.lap_duration - laps.session_median_lap

    laps["tyre_age"] = laps.tyre_age_at_start + (laps.lap_number
                                                 - laps.lap_start)
    laps["yellow_sector"] = laps.yellow_sector_flag.fillna(0).astype(int)
    laps["date_start"] = pd.to_datetime(laps.date_start, format="ISO8601",
                                        utc=True, errors="coerce")
    laps["lap_end_ts"] = laps.date_start + pd.to_timedelta(laps.lap_duration,
                                                           unit="s")
    return laps[laps.compound.isin(VALID_COMPOUNDS) & (laps.tyre_age >= 0)]


def attach_traffic(silver, laps: pd.DataFrame, keys: list[int]) -> pd.DataFrame:
    """Gap to the car ahead, flagged the same way s05b flags it."""
    inlist = ",".join(str(k) for k in keys)
    iv = pd.read_sql(f"""
        SELECT session_key, driver_number, "date", interval_seconds,
               interval_laps
        FROM silver_intervals
        WHERE session_key IN ({inlist})
          AND (interval_seconds IS NOT NULL OR interval_laps IS NOT NULL)
    """, silver)
    iv["date"] = pd.to_datetime(iv["date"], format="ISO8601", utc=True)

    timed = laps[laps.date_start.notna()].sort_values("date_start")
    out = pd.merge_asof(timed, iv.sort_values("date"),
                        left_on="date_start", right_on="date",
                        by=["session_key", "driver_number"],
                        direction="backward")
    out["gap_ahead"] = out.interval_seconds.clip(
        upper=GAP_CAP_SECONDS).fillna(GAP_CAP_SECONDS)
    out["in_dirty_air"] = (
        out.interval_seconds < DIRTY_AIR_SECONDS).fillna(False).astype(int)
    out["out_of_position"] = (
        out.interval_laps.notna() | out.interval_seconds.isna()).astype(int)
    return out.drop(columns=["date"], errors="ignore")


def fetch_car_data(bronze, keys: list[int]) -> pd.DataFrame:
    """
    Every car_data sample for these races, in one scan.

    car_data is 13.9M rows with no indexes and every column typed TEXT, so a
    WHERE costs a full scan either way. One query is cheaper than six.
    """
    inlist = ",".join(str(k) for k in keys)
    car = pd.read_sql(f"""
        SELECT session_key, driver_number, "date", drs, speed, rpm
        FROM car_data
        WHERE CAST(session_key AS INTEGER) IN ({inlist})
          AND drs IS NOT NULL
    """, bronze)
    for c in ("session_key", "driver_number", "drs", "speed", "rpm"):
        car[c] = pd.to_numeric(car[c], errors="coerce")
    car["date"] = pd.to_datetime(car["date"], format="ISO8601", utc=True,
                                 errors="coerce")
    car = car.dropna(subset=["date", "drs", "session_key", "driver_number"])
    car["drs_open"] = (car.drs >= DRS_OPEN_FROM).astype(int)
    return car


# --- per-lap telemetry -----------------------------------------------------------

def summarise_per_lap(car: pd.DataFrame, laps: pd.DataFrame) -> pd.DataFrame:
    """
    Attribute each sample to the lap whose window contains it, per driver.

    A timestamp join, not a lap number: car_data has no lap column, and the
    only honest way to say which lap a 4Hz sample belongs to is the lap's own
    start and end. Samples falling in a pit lane or between laps land outside
    every window and are dropped rather than assigned to a neighbour.
    """
    frames = []
    for (sk, dn), g in car.groupby(["session_key", "driver_number"], sort=False):
        lg = laps[(laps.session_key == sk) & (laps.driver_number == dn)]
        if lg.empty:
            continue
        g = g.sort_values("date")
        lg = lg.sort_values("date_start")
        idx = np.searchsorted(lg.date_start.values, g.date.values,
                              side="right") - 1
        keep = idx >= 0
        g, idx = g[keep], idx[keep]
        if g.empty:
            continue
        inside = g.date.values <= lg.lap_end_ts.values[idx]
        g, idx = g[inside], idx[inside]
        if g.empty:
            continue
        g = g.assign(lap_number=lg.lap_number.values[idx])
        frames.append(g.groupby("lap_number").agg(
            drs_share=("drs_open", "mean"),
            top_speed=("speed", "max"),
            max_rpm=("rpm", "max"),
            samples=("drs_open", "size"),
        ).assign(session_key=sk, driver_number=dn).reset_index())

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out[out.samples >= MIN_SAMPLES_PER_LAP]


def build_telemetry_tow(laps: pd.DataFrame) -> pd.DataFrame:
    """
    The tow, as a table a reader can check by eye.

    Top speed against gap to the car ahead. No model, because none is needed:
    if a tow exists, cars running close must reach a higher top speed than
    cars running alone, and either the numbers show that or they do not.
    """
    df = laps.assign(bucket=pd.cut(laps.gap_ahead, GAP_BUCKETS,
                                   labels=GAP_LABELS))
    out = (df.groupby("bucket", observed=True)
             .agg(laps=("top_speed", "size"),
                  mean_top_speed=("top_speed", "mean"),
                  max_top_speed=("top_speed", "max"),
                  mean_drs_share=("drs_share", "mean"),
                  mean_lap_vs_median=("lap_vs_median", "mean"))
             .reset_index())
    out["bucket"] = out.bucket.astype(str)

    clear = out.loc[out.bucket == GAP_LABELS[-1], "mean_top_speed"]
    base = float(clear.iloc[0]) if len(clear) else np.nan
    out["top_speed_vs_clear_air"] = (out.mean_top_speed - base).round(2)

    for c in ("mean_top_speed", "max_top_speed", "mean_lap_vs_median"):
        out[c] = out[c].round(2)
    out["mean_drs_share"] = out.mean_drs_share.round(4)
    return out


def build_telemetry_effect(laps: pd.DataFrame, car: pd.DataFrame,
                           keys: list[int]) -> pd.DataFrame:
    """
    What DRS is worth once traffic is accounted for, with its caveats attached.

    The caveats ship in the same table as the coefficient on purpose. A reader
    who sees -5.15 seconds and not the 0.575 correlation with dirty air has
    been given a number and denied the thing that makes it interpretable.
    """
    rows = []

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        base = smf.ols(MODEL_FORMULA, data=laps).fit()
        full = smf.ols(MODEL_FORMULA + " + drs_share", data=laps).fit()
    f_stat, p_val, _ = full.compare_f_test(base)

    conf = full.conf_int()
    for term in ("drs_share", "gap_ahead", "in_dirty_air", "out_of_position"):
        if term not in full.params.index:
            continue
        rows.append({
            "term": term, "kind": "coefficient",
            "coefficient": _round(full.params[term], 6),
            "std_error": _round(full.bse[term], 6),
            "p_value": _round(full.pvalues[term], 8),
            "ci_lower": _round(conf.loc[term, 0], 6),
            "ci_upper": _round(conf.loc[term, 1], 6),
            "note": None,
        })

    def diag(term, value, note):
        rows.append({"term": term, "kind": "diagnostic",
                     "coefficient": _round(value), "std_error": None,
                     "p_value": None, "ci_lower": None, "ci_upper": None,
                     "note": note})

    diag("races covered", len(keys),
         f"DRS and the tow are measured on {len(keys)} races of the "
         f"{len(keys)} that carry car_data, out of 81 races in the dashboard. "
         "Everything on this panel is those races only. The within-race model "
         "in the section above covers all 81 and cannot include DRS, because "
         "adding it would drop 95% of the laps.")

    diag("laps covered", len(laps),
         f"{len(laps):,} laps survive the clean filters and have at least "
         f"{MIN_SAMPLES_PER_LAP} telemetry samples inside the lap window.")

    diag("model gain from DRS", full.rsquared - base.rsquared,
         f"Adding DRS usage to a traffic-only model on these races moves "
         f"R-squared from {base.rsquared:.3f} to {full.rsquared:.3f}, "
         f"F = {f_stat:.1f}, p = {p_val:.2e}.")

    corr = float(laps[["drs_share", "in_dirty_air"]].corr().iloc[0, 1])
    diag("drs and dirty air correlation", corr,
         f"DRS usage correlates {corr:.3f} with running inside "
         f"{DIRTY_AIR_SECONDS}s, because DRS is only available within a second "
         "of the car ahead. So the DRS coefficient is partly traffic restated "
         "and cannot be read as the pure aerodynamic gain from the wing.")

    undoc = car[~car.drs.isin([0, 1, 2, 3, 8, 10, 12, 14])]
    diag("undocumented drs codes", 100.0 * len(undoc) / len(car),
         f"{100.0 * len(undoc) / len(car):.1f}% of samples carry a drs value "
         "with no reliable published meaning (9, 11, 13, 15). Open is taken as "
         f"{DRS_OPEN_FROM} and above; those codes therefore count as open.")

    diag("ers", None,
         "Energy recovery is not measured. car_data carries throttle, rpm, "
         "brake, speed, n_gear and drs and nothing else, and no other OpenF1 "
         "endpoint reports battery state, deployment or harvest. ERS "
         "management genuinely affects lap time and this source cannot see it.")

    diag("top speed", None,
         "Top speed is reported in the tow table as evidence and kept out of "
         "the model. It is measured on the same lap it would be explaining, so "
         "it is an outcome, not a cause.")

    return pd.DataFrame(rows)


# --- runner ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Measure DRS and the tow on the telemetry races.")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and report without writing the CSVs")
    args = ap.parse_args()

    for p in (DB_PATH, BRONZE_DB_PATH):
        if not p.exists():
            print(f"[FAIL] database not found at {p}")
            return 1

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    print("=" * 74)
    print("DRS AND THE TOW")
    print(f"silver: {DB_PATH}")
    print(f"bronze: {BRONZE_DB_PATH}")
    print(f"python: {sys.version.split()[0]}  pandas {pd.__version__}")
    print("=" * 74)

    silver = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    bronze = sqlite3.connect(f"file:{BRONZE_DB_PATH}?mode=ro", uri=True)

    keys = telemetry_races(silver, bronze)
    if not keys:
        print("[FAIL] no race session has car_data. Run s01_backfill.py "
              "--telemetry first.")
        return 1

    laps = attach_traffic(silver, load_laps(silver, keys), keys)
    names = ", ".join(sorted(laps.circuit_short_name.unique()))
    print(f"\nraces with telemetry: {len(keys)}  ({names})")
    print(f"clean laps on those races: {len(laps):,}")

    t0 = time.time()
    car = fetch_car_data(bronze, keys)
    print(f"car_data samples: {len(car):,}  ({time.time() - t0:.1f}s, one scan)")

    per_lap = summarise_per_lap(car, laps)
    if per_lap.empty:
        print("[FAIL] no lap could be matched to a telemetry window.")
        return 1
    laps = laps.merge(per_lap, on=["session_key", "driver_number",
                                   "lap_number"], how="inner")
    print(f"laps with usable telemetry: {len(laps):,}")

    tow = build_telemetry_tow(laps)
    effect = build_telemetry_effect(laps, car, keys)
    silver.close()
    bronze.close()

    clear = tow.loc[tow.bucket == GAP_LABELS[-1], "mean_top_speed"]
    close = tow.loc[tow.bucket == GAP_LABELS[0], "mean_top_speed"]
    if len(clear) and len(close):
        print(f"\ntow: {float(close.iloc[0]) - float(clear.iloc[0]):+.1f} km/h "
              "top speed inside 1s against clear air")
    drs = effect[(effect.term == "drs_share") & (effect.kind == "coefficient")]
    if len(drs):
        print(f"DRS: {float(drs.coefficient.iloc[0]):+.3f}s per unit share, "
              f"p = {float(drs.p_value.iloc[0]):.2e}")

    frames = {"telemetry_tow": tow, "telemetry_effect": effect}
    print()
    for name in TABLES:
        df = frames[name].copy()
        if df.empty:
            print(f"  [WARN] {name} is empty")
            continue
        df["generated_at"] = generated_at
        if not args.dry_run:
            df.to_csv(DASHBOARD_DIR / f"{name}.csv", index=False)
        print(f"  {name:18s} {len(df):>5,} rows x {len(df.columns):>2} cols"
              + ("   (dry run, not written)" if args.dry_run else ""))

    print("\n" + "=" * 74)
    print(f"These figures cover {len(keys)} races, not 81. The panel must say so.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
