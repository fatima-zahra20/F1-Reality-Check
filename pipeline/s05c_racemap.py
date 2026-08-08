"""
s05c_racemap.py - track outlines and measured car positions for the race map.

Runs between s05b and s06.

Produces three CSVs in outputs/dashboard/:

    map_circuit_outline   the traced shape of each circuit, as an ordered path
    map_measured_xy       real recorded car positions, for the one race that has them
    map_coverage          one row per race, saying exactly what that race supports

Design decisions
----------------
TELEMETRY COVERAGE IS AN INGESTION FACT, NOT A DATA FACT. This module used to
assert that no race carried car telemetry, because bronze held zero car_data
rows for all 103 race-type sessions. That was wrong. OpenF1 answers a
whole-session telemetry request with HTTP 422, "you're likely asking for too
much data at once", and the original fetcher recorded that as zero rows.
Requested one driver at a time the data is all there. So what each race
supports is read from bronze every run and written into map_coverage, and the
dashboard reports "not retrieved" rather than "not recorded" for the gaps.

THE OUTLINE DOES NOT COME FROM THE RACE. Most Grands Prix have no position
data of their own. But an outline is a property of the CIRCUIT, not of the
session, so any session held at that circuit can donate the shape: practice
and qualifying sessions never appear in the UI, they just draw the track.
That is what takes map coverage from a handful of races to nearly all of
them.

CAR PLACEMENT IS DERIVED, NOT MEASURED, and the page must say so. For 74 of the
75 mappable races there is no recorded x/y, so a car's place on the outline is
computed from lap timing: at the moment the leader starts a lap, each driver is
however far through their own current lap, and that fraction is where the dot
goes. This is accurate for running order and spacing, which is what the race
line means. It is not accurate to the racing line, and it cannot know which
side of the track a car is on.

That placement is NOT precomputed here. It needs only date_start and
lap_duration, both already in fact_lap for all 81 races, and one race is 20
drivers by 60 laps. Computing it in the app keeps it consistent with the lap
table by construction and adds nothing to the download.

COORDINATES ARE TENTHS OF A METRE. DATA_DICTIONARY.md calls them millimetres
with ranges of about 17m; both are wrong. Hungaroring's bounding box is 10,637
by 12,052 units against a real lap length of 4,381 m, which puts the unit at
0.1 m. This module emits metres. The dictionary needs the correction, and needs
to stop describing silver_location, which the 2026-07-28 split removed.

18.8% OF POSITION ROWS ARE (0,0). Those are feed dropouts, not cars at the
origin. Unfiltered, every car teleports to the middle of the map once a second.

ONE SCAN OF BRONZE. location is 25.8M rows with no indexes and every column
typed TEXT, so any WHERE is a full scan costing about 9s. Every row this module
needs is therefore fetched in a single query rather than 22 of them.

READ-ONLY ON SILVER AND BRONZE. Nothing is written back to either.

Usage
-----
    python pipeline\\s05c_racemap.py
    python pipeline\\s05c_racemap.py --dry-run

Requires the pinned Anaconda environment. See NOTES_LOG #42.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import BRONZE_DB_PATH, DB_PATH, OUTPUTS_DIR  # noqa: E402

DASHBOARD_DIR = OUTPUTS_DIR / "dashboard"

# Position units are 0.1 m. See the module docstring.
UNITS_PER_METRE = 10.0

# Points kept per traced outline. A lap is sampled at roughly 3.7Hz, so a
# 90-second lap arrives as about 330 points and a slow circuit as more.
# Resampling every circuit to the same count makes the paths directly
# comparable and keeps the table a predictable size.
OUTLINE_POINTS = 400

# Below this many usable samples a lap cannot describe a circuit, so the
# tracer moves on to the next candidate lap rather than emitting a shape with
# holes in it.
MIN_OUTLINE_SAMPLES = 120

# Candidate laps to try per circuit before giving up on it.
MAX_TRACE_ATTEMPTS = 6

# Races carrying recorded positions are discovered, not listed. This used to
# be a single hardcoded session, because only the 2026 Monaco Grand Prix had
# any x/y. That turned out to be an ingestion bug rather than a fact about the
# data: OpenF1 answers a whole-session telemetry request with HTTP 422, and the
# old fetcher recorded that as zero rows. Paged per driver the data is there,
# so the set of races with real positions now grows whenever more is fetched
# and this module has to find them rather than be told.
#
# Total rows kept across all of them. The published bundle is downloaded by
# every visitor, so this is a hard budget rather than a target: the sampling
# stride is derived from it, and adding races makes the traces coarser instead
# of making the download bigger.
MEASURED_ROW_BUDGET = 400_000
MIN_MEASURED_STRIDE = 2


def _to_num(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Bronze stores everything as TEXT, including the numbers."""
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# --- choosing what to trace ------------------------------------------------------

def load_location_sessions(bronze, silver) -> pd.DataFrame:
    """Every session that has position data, tagged with its circuit."""
    keys = pd.read_sql("SELECT DISTINCT session_key FROM location", bronze)
    keys["session_key"] = pd.to_numeric(keys["session_key"], errors="coerce")
    keys = keys.dropna().astype({"session_key": int})

    ses = pd.read_sql("""
        SELECT session_key, year, session_type, session_name,
               circuit_key, circuit_short_name
        FROM silver_sessions
    """, silver)
    return ses.merge(keys, on="session_key")


def pick_trace_candidates(silver, loc_sessions: pd.DataFrame,
                          circuits: pd.DataFrame) -> pd.DataFrame:
    """
    For each circuit, the laps most likely to trace a clean outline.

    Preference order is qualifying, then practice, then race sessions. A
    qualifying lap is driven flat out, alone, on a dry line, which is exactly
    the lap whose path describes the circuit. Within a session the quickest
    laps come first, because a slow lap is usually slow for reasons that put
    the car somewhere the track is not, such as the pit lane.
    """
    wanted = loc_sessions[loc_sessions.circuit_key.isin(circuits.circuit_key)]
    if wanted.empty:
        return pd.DataFrame()

    order = {"Qualifying": 0, "Practice": 1, "Race": 2}
    wanted = wanted.assign(pref=wanted.session_type.map(order).fillna(3))

    keys = ",".join(str(k) for k in wanted.session_key.unique())
    laps = pd.read_sql(f"""
        SELECT session_key, driver_number, lap_number, date_start, lap_duration
        FROM silver_laps
        WHERE session_key IN ({keys})
          AND lap_duration IS NOT NULL
          AND date_start IS NOT NULL
          AND COALESCE(is_pit_out_lap, 0) = 0
    """, silver)

    laps = laps.merge(wanted[["session_key", "circuit_key", "circuit_short_name",
                              "session_type", "session_name", "year", "pref"]],
                      on="session_key")

    # A lap far off that circuit's best is an in-lap, a cool-down or a lap
    # under a red flag, none of which stay on the racing surface.
    best = laps.groupby("circuit_key")["lap_duration"].transform("min")
    laps = laps[laps.lap_duration <= 1.15 * best]

    laps = laps.sort_values(["circuit_key", "pref", "lap_duration"])
    return laps.groupby("circuit_key").head(MAX_TRACE_ATTEMPTS).reset_index(drop=True)


# --- the single bronze scan ------------------------------------------------------

def fetch_positions(bronze, pairs: set[tuple[int, int]],
                    whole_sessions: set[int]) -> pd.DataFrame:
    """
    One pass over location for everything this module needs.

    `pairs` are (session_key, driver_number) combinations wanted for outline
    tracing; `whole_sessions` are sessions wanted in full, for the measured
    race. Both go into a single WHERE so the 25.8M-row table is scanned once
    rather than once per circuit.
    """
    clauses = []
    if pairs:
        pair_sql = " OR ".join(
            f"(session_key = '{s}' AND driver_number = '{d}')" for s, d in pairs)
        clauses.append(f"({pair_sql})")
    if whole_sessions:
        keys = ", ".join(f"'{k}'" for k in whole_sessions)
        clauses.append(f"session_key IN ({keys})")
    if not clauses:
        return pd.DataFrame()

    df = pd.read_sql(f"""
        SELECT session_key, driver_number, date, x, y, z
        FROM location
        WHERE ({' OR '.join(clauses)})
          AND NOT (x = '0' AND y = '0')
    """, bronze)

    df = _to_num(df, ["session_key", "driver_number", "x", "y", "z"])
    df = df.dropna(subset=["session_key", "driver_number", "x", "y"])
    df["session_key"] = df["session_key"].astype(int)
    df["driver_number"] = df["driver_number"].astype(int)
    df["date"] = pd.to_datetime(df["date"], format="ISO8601", utc=True)
    return df


# --- tracing ---------------------------------------------------------------------

def resample_path(x: np.ndarray, y: np.ndarray, z: np.ndarray, t: np.ndarray,
                  n: int) -> pd.DataFrame:
    """
    Respace a path by distance, and record how far through the lap in TIME
    each of those points was reached.

    Raw samples are evenly spaced in time, which means they bunch up in the
    corners and stretch down the straights: the slowest part of the lap gets
    the most points. Respacing by cumulative distance gives an outline whose
    point density is uniform, which is what draws a clean track.

    But the app places a car using how far through its lap it is in TIME, and
    time and distance are not interchangeable around a lap. A car a third of
    the way through Monaco by the clock is nowhere near a third of the way
    round by distance, because it has just crawled through the hairpin. Using
    one for the other puts every car too far forward in the slow sections and
    too far back on the straights, worst exactly where the track is twistiest
    and the error is most visible.

    So both parameterisations ship. `path_fraction` is distance along the lap
    and draws the outline; `time_fraction` is elapsed time on the reference
    lap and is what a car position is looked up against.
    """
    d = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    if d[-1] <= 0 or t[-1] <= t[0]:
        return pd.DataFrame()
    target = np.linspace(0.0, d[-1], n)
    t_norm = (t - t[0]) / (t[-1] - t[0])

    # z is elevation. A dropout writes exactly 0 rather than a null, and one 0
    # among values around 2,380 would carve a 238 m canyon through the middle
    # of the circuit, so those samples are dropped and interpolated across
    # rather than averaged in. Between 0.4% and 9.6% of samples per circuit
    # are dropouts on this definition.
    #
    # The test is "not zero", not "above zero". The survey datum is arbitrary
    # and differs by circuit: 21 of 22 sit above it, but Yas Marina runs from
    # -243 to 0 and Baku from -247 to 21. Requiring a positive value threw
    # away every Yas Marina reading, leaving that circuit with no elevation at
    # all, and kept only Baku's handful of spurious positives, which is why
    # Baku reported 2 m of relief against a published 20 m.
    good = z != 0
    z_m = (np.interp(target, d[good], z[good])
           if good.sum() >= 2 else np.full(n, np.nan))

    return pd.DataFrame({
        "seq": np.arange(n),
        "x": np.interp(target, d, x),
        "y": np.interp(target, d, y),
        "z": z_m,
        "path_fraction": target / d[-1],
        "time_fraction": np.interp(target, d, t_norm),
    })


def trace_outline(pos: pd.DataFrame, lap: pd.Series) -> pd.DataFrame:
    """The x/y path of one lap, respaced. Empty when the lap is too gappy."""
    start = pd.to_datetime(lap.date_start, format="ISO8601", utc=True)
    end = start + pd.to_timedelta(float(lap.lap_duration), unit="s")

    seg = pos[(pos.session_key == lap.session_key)
              & (pos.driver_number == lap.driver_number)
              & (pos.date >= start) & (pos.date <= end)].sort_values("date")

    if len(seg) < MIN_OUTLINE_SAMPLES:
        return pd.DataFrame()

    elapsed = (seg.date - start).dt.total_seconds().to_numpy(float)
    return resample_path(seg.x.to_numpy(float) / UNITS_PER_METRE,
                         seg.y.to_numpy(float) / UNITS_PER_METRE,
                         seg.z.fillna(0).to_numpy(float) / UNITS_PER_METRE,
                         elapsed, OUTLINE_POINTS)


def build_outlines(pos: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """One outline per circuit, from the first candidate lap that traces."""
    out = []
    for circuit_key, laps in candidates.groupby("circuit_key"):
        for lap in laps.itertuples():
            path = trace_outline(pos, lap)
            if path.empty:
                continue
            path["circuit_key"] = circuit_key
            path["circuit_short_name"] = lap.circuit_short_name
            path["source_session_key"] = lap.session_key
            path["source_session"] = f"{lap.year} {lap.session_name}"
            path["source_driver_number"] = lap.driver_number
            path["source_lap_number"] = lap.lap_number
            path["source_lap_duration"] = round(float(lap.lap_duration), 3)
            out.append(path)
            break
    if not out:
        return pd.DataFrame()

    df = pd.concat(out, ignore_index=True)

    # Elevation is only meaningful as a difference. The raw datum is an
    # arbitrary offset that differs by circuit, so it is rebased to zero at
    # each circuit's lowest point and the number becomes metres of climb.
    df["z"] = df.z - df.groupby("circuit_key").z.transform("min")

    for c in ("x", "y", "z"):
        df[c] = df[c].round(2)
    for c in ("path_fraction", "time_fraction"):
        df[c] = df[c].round(6)
    cols = ["circuit_key", "circuit_short_name", "seq", "x", "y", "z",
            "path_fraction", "time_fraction",
            "source_session_key", "source_session", "source_driver_number",
            "source_lap_number", "source_lap_duration"]
    return df[cols]


# --- the one measured race -------------------------------------------------------

def build_measured(pos: pd.DataFrame, silver,
                   session_keys: list[int]) -> pd.DataFrame:
    """
    Recorded positions for every race that has them, thinned to fit the budget.

    The stride is derived from how much data there is rather than fixed, so
    the table stays roughly the same size as races are added. At 3.7Hz a
    stride of 2 still moves a dot smoothly; beyond about 8 it starts to skip,
    which is the point at which fewer races would be the better trade.

    Lap number is attached by nearest preceding lap start, so the app can show
    measured dots for the same lap the rest of the page is describing.
    """
    df = pos[pos.session_key.isin(session_keys)].copy()
    if df.empty:
        return pd.DataFrame()

    stride = max(MIN_MEASURED_STRIDE,
                 int(np.ceil(len(df) / MEASURED_ROW_BUDGET)))
    df = df.sort_values(["session_key", "driver_number", "date"])
    keep = df.groupby(["session_key", "driver_number"]).cumcount() % stride == 0
    df = df[keep]
    print(f"  measured positions thinned by {stride} "
          f"(1 sample every {stride / 3.7:.1f}s)")

    laps = pd.read_sql(f"""
        SELECT session_key, driver_number, lap_number, date_start
        FROM silver_laps
        WHERE session_key IN ({','.join(str(k) for k in session_keys)})
          AND date_start IS NOT NULL
    """, silver)
    laps["date_start"] = pd.to_datetime(laps["date_start"], format="ISO8601",
                                        utc=True)
    laps = laps.sort_values("date_start")

    df = pd.merge_asof(df.sort_values("date"), laps,
                       left_on="date", right_on="date_start",
                       by=["session_key", "driver_number"],
                       direction="backward")

    for axis in ("x", "y", "z"):
        if axis in df:
            df[axis] = (df[axis] / UNITS_PER_METRE).round(2)
    cols = ["session_key", "driver_number", "date", "lap_number", "x", "y", "z"]
    out = df[[c for c in cols if c in df]]
    return out.sort_values(["session_key", "driver_number", "date"]) \
              .reset_index(drop=True)


# --- coverage --------------------------------------------------------------------

def build_coverage(silver, outlines: pd.DataFrame, measured: pd.DataFrame,
                   car_data_keys: set[int]) -> pd.DataFrame:
    """
    One row per Grand Prix, stating what that race can and cannot show.

    This table exists so the app never has to guess. Every "not recorded"
    message it displays is read from here rather than inferred from an empty
    query result, which is the difference between a stated limit and a bug.
    """
    races = pd.read_sql("""
        SELECT s.session_key, s.year, m.meeting_name AS race_name,
               s.circuit_key, s.circuit_short_name, s.date_start
        FROM silver_sessions s
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        WHERE s.session_name = 'Race' AND s.is_cancelled = 0
          AND s.date_start < datetime('now')
        ORDER BY s.year, s.date_start
    """, silver)

    has_outline = set(outlines.circuit_key) if not outlines.empty else set()
    measured_keys = set(measured.session_key) if not measured.empty else set()

    races["has_outline"] = races.circuit_key.isin(has_outline).astype(int)
    races["has_measured_xy"] = races.session_key.isin(measured_keys).astype(int)
    races["has_car_data"] = races.session_key.isin(car_data_keys).astype(int)

    def map_note(r):
        if not r.has_outline:
            return ("No position data has been retrieved for this circuit, so "
                    "it has no track map. Every other panel works.")
        if r.has_measured_xy:
            return ("Recorded car positions, not derived ones. Coverage runs "
                    "as far as the position feed does for this race.")
        return ("Track map available. Car positions on it are derived from lap "
                "timing, not recorded, so they are accurate for order and "
                "spacing but not for the racing line.")

    def telemetry_note(r):
        if r.has_car_data:
            return ("Speed, gear, throttle, brake, rpm and DRS are recorded "
                    "for this race.")
        # Deliberately "not retrieved" rather than "not recorded". The old
        # wording asserted the data did not exist, which was wrong: OpenF1
        # holds it and the ingester was dropping it on an unhandled HTTP 422.
        return ("Speed, gear, throttle, brake, rpm and DRS have not been "
                "retrieved for this race. The data exists upstream and can be "
                "backfilled; it is not published here yet.")

    races["map_note"] = races.apply(map_note, axis=1)
    races["telemetry_note"] = races.apply(telemetry_note, axis=1)

    return races.drop(columns=["date_start"])


# --- runner ----------------------------------------------------------------------

TABLES = ["map_circuit_outline", "map_measured_xy", "map_coverage"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the race map layer.")
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
    print("RACE MAP LAYER")
    print(f"silver: {DB_PATH}")
    print(f"bronze: {BRONZE_DB_PATH}")
    print(f"python: {sys.version.split()[0]}  pandas {pd.__version__}")
    print("=" * 74)

    silver = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    bronze = sqlite3.connect(f"file:{BRONZE_DB_PATH}?mode=ro", uri=True)

    races = pd.read_sql("""
        SELECT DISTINCT circuit_key, circuit_short_name
        FROM silver_sessions
        WHERE session_name = 'Race' AND is_cancelled = 0
    """, silver)
    print(f"\ncircuits raced: {len(races)}")

    loc_sessions = load_location_sessions(bronze, silver)
    print(f"sessions with position data: {len(loc_sessions)}")

    candidates = pick_trace_candidates(silver, loc_sessions, races)
    n_circuits = candidates.circuit_key.nunique() if not candidates.empty else 0
    print(f"circuits with a candidate lap: {n_circuits}")

    pairs = set(zip(candidates.session_key, candidates.driver_number)) \
        if not candidates.empty else set()

    # Every Grand Prix that actually has position data gets its real x/y, and
    # every one that has car telemetry is flagged. Both are discovered from
    # bronze rather than listed, so a later backfill needs no code change.
    gp = pd.read_sql("""
        SELECT session_key FROM silver_sessions
        WHERE session_name = 'Race' AND is_cancelled = 0
    """, silver).session_key
    measured_keys = sorted(set(gp) & set(loc_sessions.session_key))
    car_keys = pd.read_sql("SELECT DISTINCT session_key FROM car_data", bronze)
    car_keys = set(pd.to_numeric(car_keys.session_key,
                                 errors="coerce").dropna().astype(int))
    print(f"races with recorded positions: {len(measured_keys)}")
    print(f"races with car telemetry:      {len(set(gp) & car_keys)}")

    t0 = time.time()
    pos = fetch_positions(bronze, pairs, set(measured_keys))
    print(f"position rows fetched: {len(pos):,}  ({time.time() - t0:.1f}s, "
          "one scan)")

    outlines = build_outlines(pos, candidates)
    traced = outlines.circuit_key.nunique() if not outlines.empty else 0
    print(f"circuits traced: {traced}")

    measured = build_measured(pos, silver, measured_keys)
    print(f"measured positions: {len(measured):,} rows across "
          f"{measured.session_key.nunique() if len(measured) else 0} races")

    coverage = build_coverage(silver, outlines, measured, car_keys)
    silver.close()
    bronze.close()

    mapped = int(coverage.has_outline.sum())
    print(f"\nraces with a map: {mapped} of {len(coverage)}")
    no_map = coverage[coverage.has_outline == 0]
    if not no_map.empty:
        gone = no_map.groupby("circuit_short_name").size()
        print("  no map: " + ", ".join(f"{c} ({n} races)"
                                       for c, n in gone.items()))

    frames = {"map_circuit_outline": outlines,
              "map_measured_xy": measured,
              "map_coverage": coverage}

    print()
    for name in TABLES:
        df = frames[name].copy()
        if df.empty:
            print(f"  [WARN] {name} is empty")
            continue
        df["generated_at"] = generated_at
        if not args.dry_run:
            df.to_csv(DASHBOARD_DIR / f"{name}.csv", index=False)
        print(f"  {name:22s} {len(df):>7,} rows x {len(df.columns):>2} cols"
              + ("   (dry run, not written)" if args.dry_run else ""))

    print("\n" + "=" * 74)
    print("Car placement is computed in the app from fact_lap, not shipped here.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
