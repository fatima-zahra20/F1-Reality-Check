"""
s04_descriptive.py — builds the descriptive serving layer for the dashboard.

Produces a star schema as CSVs in outputs/dashboard/:

    dim_race           one row per race          (~81)
    dim_driver         driver x season           (~90)
    dim_team           one row per constructor   (~11)
    fact_driver_race   driver x race             (~1,700)
    fact_lap           driver x race x lap       (~100k)
    fact_event         one row per notable moment
    fact_championship  team x race, standings before and after

Design decisions
----------------
DROP AND REWRITE, never append. These are derived views, not a log: every row is
recomputed from silver, so a rebuild is the truth as of now. Appending would
duplicate races, and "append only new races" would still be wrong, because a
backfill can change an old race — as happened on 2026-07-27 when nine races
gained data.

NATURAL KEYS ONLY. No surrogate ids generated at build time: if dim_driver
assigned driver_id = 1,2,3... those numbers could shift when a new driver
appears, silently breaking every relationship built on them. Keys are
session_key, driver_number, and normalised team_name.

DRIVER NUMBERS ARE NOT DRIVERS. They are reassigned between seasons, so
dim_driver is grained by driver_number x year and must be joined with the
year from dim_race. See build_dim_driver.

DYNAMIC SCOPE. "All completed, non-cancelled races with both laps and results" —
never a hardcoded year or session list, so new races flow through with no edits.

RACES ONLY. Sprints and qualifying are excluded; the dashboard tells the story of
a Grand Prix. Sprint data remains in silver if it is wanted later.

CADILLAC INCLUDED. Their exclusion elsewhere is a modelling decision (no 2023-25
history, so trailing features are undefined), not a descriptive one.

CLEAN LAPS use silver_lap_flags.neutralised, not the old caution_flag. See
NOTES_LOG open question A: the previous logic misclassified about one lap in
nine.

Usage
-----
    python pipeline\\s04_descriptive.py
    python pipeline\\s04_descriptive.py --no-csv     # write to gold_f1.db only
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH, OUTPUTS_DIR  # noqa: E402 

DASHBOARD_DIR = OUTPUTS_DIR / "dashboard"

# A clean lap longer than this multiple of its session's median clean lap is a
# car sitting in a red-flag queue, not a lap. Derived per session rather than
# hardcoded in seconds — see the note in build_fact_driver_race. s05_diagnostic
# applies the same rule.
LAP_OUTLIER_FACTOR = 2.0

# Constructor renames across 2023-2026. Mirrors data_prep.TEAM_NAME_MAP.
# Cadillac deliberately unmapped — a genuinely new 2026 constructor.
TEAM_NAME_MAP = {
    "AlphaTauri": "RB Family",
    "RB": "RB Family",
    "Racing Bulls": "RB Family",
    "Alfa Romeo": "Sauber Family",
    "Kick Sauber": "Sauber Family",
    "Audi": "Sauber Family",
}

# Every completed Grand Prix with both laps and results. The EXISTS clauses are
# what make this dynamic — no year list to maintain.
RACE_SCOPE = """
    SELECT s.session_key, s.meeting_key
    FROM silver_sessions s
    WHERE s.session_name = 'Race'
      AND s.is_cancelled = 0
      AND s.date_start < datetime('now')
      AND EXISTS (SELECT 1 FROM silver_laps l WHERE l.session_key = s.session_key)
      AND EXISTS (SELECT 1 FROM silver_session_result r WHERE r.session_key = s.session_key)
"""


def normalize_teams(df: pd.DataFrame, col: str = "team_name") -> pd.DataFrame:
    df = df.copy()
    df[col] = df[col].replace(TEAM_NAME_MAP)
    return df


# --- dimensions ------------------------------------------------------------------

def build_dim_race(con) -> pd.DataFrame:
    """One row per race: the dashboard's picker, plus race-level context."""
    df = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT
            s.session_key,
            m.meeting_key,
            m.year,
            m.meeting_name              AS race_name,
            m.circuit_short_name        AS circuit,
            m.country_name              AS country,
            m.location,
            m.circuit_type,
            s.date_start                AS race_date,
            (SELECT MAX(lap_number) FROM silver_laps l
              WHERE l.session_key = s.session_key)          AS total_laps,
            (SELECT COUNT(*) FROM silver_session_result r
              WHERE r.session_key = s.session_key)          AS entrants,
            (SELECT COUNT(*) FROM silver_session_result r
              WHERE r.session_key = s.session_key AND r.dnf = 1) AS dnf_count,
            (SELECT COUNT(*) FROM silver_caution_periods p
              WHERE p.session_key = s.session_key AND p.kind = 'SC')  AS safety_car_periods,
            (SELECT COUNT(*) FROM silver_caution_periods p
              WHERE p.session_key = s.session_key AND p.kind = 'VSC') AS vsc_periods,
            (SELECT COUNT(*) FROM silver_caution_periods p
              WHERE p.session_key = s.session_key AND p.kind = 'RED') AS red_flag_periods,
            (SELECT ROUND(AVG(w.track_temperature), 1) FROM silver_weather w
              WHERE w.session_key = s.session_key)          AS avg_track_temp,
            (SELECT ROUND(AVG(w.air_temperature), 1) FROM silver_weather w
              WHERE w.session_key = s.session_key)          AS avg_air_temp,
            (SELECT ROUND(100.0 * SUM(w.rainfall) / COUNT(*), 1) FROM silver_weather w
              WHERE w.session_key = s.session_key)          AS pct_samples_wet
        FROM scope
        JOIN silver_sessions s ON s.session_key = scope.session_key
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        ORDER BY s.date_start
    """, con)

    # Rainfall is a state flag, not an amount — see NOTES_LOG #17. A race counts
    # as wet if any sample recorded rain.
    df["is_wet_race"] = (df["pct_samples_wet"].fillna(0) > 0).astype(int)

    # Round number within season, derived rather than stored.
    df["round"] = df.groupby("year")["race_date"].rank(method="dense").astype(int)

    return df


def build_dim_driver(con) -> pd.DataFrame:
    """
    One row per driver_number x year.

    Numbers are reused. #1 passes to the reigning champion, and 34 numbers in
    the archive have belonged to more than one person. Keyed on driver_number
    alone, this table merged Verstappen's 2023-2025 Red Bull races into Lando
    Norris's row — 81 races entered, first_year 2023, carrying Verstappen's NED
    country code. Every chart labelled by driver name inherited that.

    Season is the smallest grain at which a number reliably points at one
    person, and it joins cleanly: facts carry session_key, dim_race carries
    year.

    Identity attributes (acronym, country, headshot) are resolved per full_name
    across all seasons rather than per row, because they describe the human, not
    the season — and OpenF1 drops country_code for whole seasons at a time, so
    a within-season lookup would return null for drivers it has known since 2023.
    """
    df = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT
            d.driver_number,
            m.year,
            d.full_name,
            d.name_acronym,
            d.broadcast_name,
            d.country_code,
            d.headshot_url,
            s.date_start,
            scope.session_key
        FROM scope
        JOIN silver_drivers d ON d.session_key = scope.session_key
        JOIN silver_sessions s ON s.session_key = scope.session_key
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        WHERE d.full_name IS NOT NULL
    """, con)

    # Within a season a number occasionally appears under two names (a reserve
    # standing in). The one who actually raced most is the driver; report the
    # rest rather than silently picking.
    per_season = (df.groupby(["driver_number", "year", "full_name"])["session_key"]
                    .nunique().rename("races_entered").reset_index())

    contested = per_season.groupby(["driver_number", "year"]).filter(
        lambda g: len(g) > 1
    )
    for (num, yr), g in contested.groupby(["driver_number", "year"]):
        names = ", ".join(f"{r.full_name} ({r.races_entered})"
                          for r in g.itertuples())
        print(f"  note: #{num} in {yr} raced under more than one name — {names}")

    season = (per_season.sort_values("races_entered")
                        .groupby(["driver_number", "year"], as_index=False)
                        .last())

    # Attributes describe the person, so resolve them per name across all
    # seasons, taking the most recent non-null of each.
    def last_valid(s: pd.Series):
        s = s.dropna()
        return s.iloc[-1] if len(s) else None

    attrs = (df.sort_values("date_start")
               .groupby("full_name", as_index=False)
               .agg({c: last_valid for c in
                     ["name_acronym", "broadcast_name", "country_code",
                      "headshot_url"]}))

    # Career span, keyed on the person. A driver who changes number between
    # seasons still aggregates correctly.
    career = (per_season.groupby("full_name", as_index=False)
                        .agg(first_year=("year", "min"),
                             last_year=("year", "max"),
                             career_races=("races_entered", "sum")))

    out = (season.merge(attrs, on="full_name", how="left")
                 .merge(career, on="full_name", how="left"))

    return out[["driver_number", "year", "full_name", "name_acronym",
                "broadcast_name", "country_code", "headshot_url",
                "races_entered", "first_year", "last_year", "career_races"]] \
        .sort_values(["year", "driver_number"], ignore_index=True)


def build_dim_team(con) -> pd.DataFrame:
    """One row per constructor, after collapsing renames."""
    df = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT d.team_name, d.team_colour, m.year, scope.session_key
        FROM scope
        JOIN silver_drivers d ON d.session_key = scope.session_key
        JOIN silver_sessions s ON s.session_key = scope.session_key
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        WHERE d.team_name IS NOT NULL
    """, con)

    df = normalize_teams(df)

    out = (df.groupby("team_name")
             .agg(first_year=("year", "min"),
                  last_year=("year", "max"),
                  races=("session_key", "nunique"),
                  team_colour=("team_colour", "last"))
             .reset_index())

    # Record which raw names collapsed into each constructor — makes the
    # normalisation visible in the dashboard rather than hidden.
    raw = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT DISTINCT d.team_name AS raw_name
        FROM scope JOIN silver_drivers d ON d.session_key = scope.session_key
        WHERE d.team_name IS NOT NULL
    """, con)
    raw["team_name"] = raw["raw_name"].replace(TEAM_NAME_MAP)
    aliases = (raw.groupby("team_name")["raw_name"]
                  .apply(lambda s: " / ".join(sorted(s)))
                  .reset_index(name="known_as"))

    return out.merge(aliases, on="team_name", how="left")


# --- facts -----------------------------------------------------------------------

def build_fact_driver_race(con) -> pd.DataFrame:
    """One row per driver per race — the dashboard's header panel."""
    df = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT
            r.session_key,
            r.driver_number,
            d.team_name,
            g."position"            AS grid_position,
            g.lap_duration          AS quali_lap_seconds,
            r."position"            AS finish_position,
            r.number_of_laps        AS laps_completed,
            r.dnf, r.dns, r.dsq,
            r.points,
            r.duration_race_seconds AS race_time_seconds,
            r.gap_to_leader_seconds,
            r.gap_to_leader_laps
        FROM scope
        JOIN silver_session_result r ON r.session_key = scope.session_key
        JOIN silver_drivers d
          ON d.session_key = r.session_key AND d.driver_number = r.driver_number
        LEFT JOIN silver_sessions race_s ON race_s.session_key = scope.session_key
        LEFT JOIN silver_sessions quali_s
          ON quali_s.meeting_key = race_s.meeting_key AND quali_s.session_name = 'Qualifying'
        LEFT JOIN silver_starting_grid g
          ON g.session_key = quali_s.session_key AND g.driver_number = r.driver_number
    """, con)

    df = normalize_teams(df)

    # Net positions gained. NULL finish means DNS, so no meaningful delta.
    df["position_change"] = df["grid_position"] - df["finish_position"]
    df["was_lapped"] = df["gap_to_leader_laps"].notna().astype(int)

    # --- pit stops ---
    pits = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT p.session_key, p.driver_number,
               COUNT(*)                        AS pit_stops,
               ROUND(MEDIAN_PLACEHOLDER, 3)    AS median_lane_duration
        FROM scope JOIN silver_pit p ON p.session_key = scope.session_key
        GROUP BY p.session_key, p.driver_number
    """.replace("ROUND(MEDIAN_PLACEHOLDER, 3)", "AVG(p.lane_duration)"), con)
    pits = pits.rename(columns={"median_lane_duration": "mean_lane_duration"})
    df = df.merge(pits, on=["session_key", "driver_number"], how="left")
    df["pit_stops"] = df["pit_stops"].fillna(0).astype(int)

    # --- stints ---
    stints = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT st.session_key, st.driver_number,
               COUNT(*) AS stint_count,
               GROUP_CONCAT(st.compound, ' > ') AS compound_sequence
        FROM scope JOIN silver_stints st ON st.session_key = scope.session_key
        WHERE st.lap_end >= st.lap_start
        GROUP BY st.session_key, st.driver_number
    """, con)
    df = df.merge(stints, on=["session_key", "driver_number"], how="left")

    # --- overtakes, both directions ---
    made = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT o.session_key, o.overtaking_driver_number AS driver_number,
               COUNT(*) AS overtakes_made
        FROM scope JOIN silver_overtakes o ON o.session_key = scope.session_key
        GROUP BY o.session_key, o.overtaking_driver_number
    """, con)
    suffered = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT o.session_key, o.overtaken_driver_number AS driver_number,
               COUNT(*) AS overtakes_suffered
        FROM scope JOIN silver_overtakes o ON o.session_key = scope.session_key
        GROUP BY o.session_key, o.overtaken_driver_number
    """, con)
    df = df.merge(made, on=["session_key", "driver_number"], how="left")
    df = df.merge(suffered, on=["session_key", "driver_number"], how="left")
    for c in ("overtakes_made", "overtakes_suffered"):
        df[c] = df[c].fillna(0).astype(int)

    # --- pace, clean laps only ---
    # neutralised = 0 excludes SC / VSC / red flag laps. NULL is excluded too —
    # unknown status is not assumed clean.
    #
    # The aggregation happens in pandas rather than SQL because it needs the
    # derived lap-duration bound below, and SQLite has no MEDIAN.
    raw = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT l.session_key, l.driver_number, l.lap_duration
        FROM scope
        JOIN silver_laps l ON l.session_key = scope.session_key
        JOIN silver_lap_flags f
          ON  f.session_key   = l.session_key
          AND f.driver_number = l.driver_number
          AND f.lap_number    = l.lap_number
        WHERE l.lap_duration IS NOT NULL
          AND f.neutralised = 0
          AND COALESCE(l.is_pit_out_lap, 0) = 0
    """, con)

    # neutralised = 0 is necessary but not sufficient. A red-flag suspension
    # leaves the car stationary on track with the clock running, and those laps
    # carry neutralised = 0 because no flag was logged against that lap number.
    # Australia 2023 (session_key 7787) contains laps of ~2,000s this way, which
    # dragged its mean clean lap to 564s against a true ~85s.
    #
    # The bound is derived per session from the median of its own clean laps —
    # never a hardcoded seconds value, which would be wrong the moment a slower
    # circuit joined the calendar. Median rather than mean, because the outliers
    # being removed are precisely what corrupts a mean.
    session_median = raw.groupby("session_key")["lap_duration"].transform("median")
    raw = raw[raw["lap_duration"] <= LAP_OUTLIER_FACTOR * session_median]

    pace = (raw.groupby(["session_key", "driver_number"])
               .agg(clean_laps=("lap_duration", "size"),
                    mean_clean_lap=("lap_duration", "mean"),
                    fastest_lap=("lap_duration", "min"))
               .reset_index())
    df = df.merge(pace, on=["session_key", "driver_number"], how="left")

    # Session-median normalisation: raw lap times are not comparable across
    # circuits (NOTES_LOG #35).
    df["session_median_lap"] = df.groupby("session_key")["mean_clean_lap"].transform("median")
    df["pace_vs_session_median"] = (df["mean_clean_lap"] - df["session_median_lap"]).round(3)

    # --- teammate deltas ---
    df["teammate_count"] = df.groupby(["session_key", "team_name"])["driver_number"].transform("count")
    for src, dst in [("grid_position", "teammate_grid_delta"),
                     ("finish_position", "teammate_finish_delta"),
                     ("points", "teammate_points_delta"),
                     ("mean_clean_lap", "teammate_pace_delta")]:
        team_total = df.groupby(["session_key", "team_name"])[src].transform("sum")
        other = team_total - df[src]
        df[dst] = (df[src] - other).where(df["teammate_count"] == 2).round(3)

    return df


def build_fact_lap(con) -> pd.DataFrame:
    """
    One row per driver per lap — the spine of every trace chart.

    Gap to leader is taken as ONE reading per lap (nearest interval sample to the
    lap start), not the full ~4-second series. The fine-grained data stays in
    silver_intervals for the gold layer.
    """
    laps = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT
            l.session_key, l.driver_number, l.lap_number,
            l.date_start,
            l.lap_duration,
            l.duration_sector_1, l.duration_sector_2, l.duration_sector_3,
            l.i1_speed, l.i2_speed, l.st_speed,
            COALESCE(l.is_pit_out_lap, 0) AS is_pit_out_lap,
            f.sc_flag, f.vsc_flag, f.red_flag, f.yellow_sector_flag, f.neutralised
        FROM scope
        JOIN silver_laps l ON l.session_key = scope.session_key
        LEFT JOIN silver_lap_flags f
          ON  f.session_key   = l.session_key
          AND f.driver_number = l.driver_number
          AND f.lap_number    = l.lap_number
        ORDER BY l.session_key, l.driver_number, l.lap_number
    """, con)

    laps["date_start"] = pd.to_datetime(laps["date_start"], format="ISO8601", utc=True)

    # --- tyre compound and age, from stints ---
    stints = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT st.session_key, st.driver_number, st.stint_number,
               st.compound, st.tyre_age_at_start, st.lap_start, st.lap_end
        FROM scope JOIN silver_stints st ON st.session_key = scope.session_key
        WHERE st.lap_end >= st.lap_start
    """, con)

    laps = laps.merge(stints, on=["session_key", "driver_number"], how="left")
    in_stint = (laps["lap_number"] >= laps["lap_start"]) & (laps["lap_number"] <= laps["lap_end"])
    laps.loc[~in_stint.fillna(False), ["stint_number", "compound", "tyre_age_at_start",
                                       "lap_start", "lap_end"]] = pd.NA
    laps = laps.drop_duplicates(subset=["session_key", "driver_number", "lap_number"], keep="first")
    laps["tyre_age"] = laps["tyre_age_at_start"] + (laps["lap_number"] - laps["lap_start"])

    # --- position at the end of each lap ---
    pos = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT p.session_key, p.driver_number, p."date", p."position"
        FROM scope JOIN silver_position p ON p.session_key = scope.session_key
        ORDER BY p."date"
    """, con)
    pos["date"] = pd.to_datetime(pos["date"], format="ISO8601", utc=True)

    laps = laps.sort_values("date_start")
    lap_keyed = laps.dropna(subset=["date_start"]).copy()

    merged = pd.merge_asof(
        lap_keyed.sort_values("date_start"),
        pos.sort_values("date")[["session_key", "driver_number", "date", "position"]]
           .rename(columns={"date": "pos_date"})
           .sort_values("pos_date"),
        left_on="date_start", right_on="pos_date",
        by=["session_key", "driver_number"], direction="backward",
    )

    # --- gaps: one reading per lap ---
    # interval_seconds is the gap to the car ahead, gap_to_leader_seconds the
    # gap to P1. Both are needed to answer whether a driver was fighting,
    # isolated, or lapped: the leader gap alone cannot distinguish a driver
    # locked in a battle from one circulating alone at the same deficit.
    intervals = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT i.session_key, i.driver_number, i."date",
               i.interval_seconds, i.interval_laps,
               i.gap_to_leader_seconds, i.gap_to_leader_laps
        FROM scope JOIN silver_intervals i ON i.session_key = scope.session_key
        ORDER BY i."date"
    """, con)
    intervals["date"] = pd.to_datetime(intervals["date"], format="ISO8601", utc=True)

    merged = pd.merge_asof(
        merged.sort_values("date_start"),
        intervals.sort_values("date")
                 .rename(columns={"date": "gap_date"})
                 .sort_values("gap_date"),
        left_on="date_start", right_on="gap_date",
        by=["session_key", "driver_number"], direction="backward",
    )

    keep = [
        "session_key", "driver_number", "lap_number", "date_start",
        "lap_duration", "duration_sector_1", "duration_sector_2", "duration_sector_3",
        "i1_speed", "i2_speed", "st_speed", "is_pit_out_lap",
        "sc_flag", "vsc_flag", "red_flag", "yellow_sector_flag", "neutralised",
        "stint_number", "compound", "tyre_age",
        "position", "interval_seconds", "interval_laps",
        "gap_to_leader_seconds", "gap_to_leader_laps",
    ]
    out = merged[keep].copy()

    # Pace relative to that race's median clean lap — comparable across circuits.
    # The same derived bound as build_fact_driver_race, applied so both tables
    # normalise against an identical baseline. A median is robust enough that
    # the trimming barely moves it, but "barely" is not "identically", and a
    # dashboard that shows two different medians for one race is a bug report.
    clean = out[(out["neutralised"] == 0) & (out["is_pit_out_lap"] == 0)].copy()
    first_pass = clean.groupby("session_key")["lap_duration"].transform("median")
    clean = clean[clean["lap_duration"] <= LAP_OUTLIER_FACTOR * first_pass]
    medians = clean.groupby("session_key")["lap_duration"].median().rename("session_median_lap")
    out = out.merge(medians, on="session_key", how="left")
    out["lap_vs_median"] = (out["lap_duration"] - out["session_median_lap"]).round(3)

    return out.sort_values(["session_key", "driver_number", "lap_number"])


def build_fact_event(con) -> pd.DataFrame:
    """One row per notable moment — the timeline and annotation layer."""
    frames = []

    pits = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT p.session_key, p.driver_number, p.lap_number, p."date" AS event_time,
               'pit_stop' AS event_type,
               'Pit stop, ' || COALESCE(ROUND(p.lane_duration, 1), '?') || 's in lane' AS detail,
               p.lane_duration AS value
        FROM scope JOIN silver_pit p ON p.session_key = scope.session_key
    """, con)
    frames.append(pits)

    ot_made = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT o.session_key, o.overtaking_driver_number AS driver_number,
               NULL AS lap_number, o."date" AS event_time,
               'overtake_made' AS event_type,
               'Passed car #' || o.overtaken_driver_number ||
                 ' for P' || o."position" AS detail,
               o."position" AS value
        FROM scope JOIN silver_overtakes o ON o.session_key = scope.session_key
    """, con)
    frames.append(ot_made)

    ot_lost = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT o.session_key, o.overtaken_driver_number AS driver_number,
               NULL AS lap_number, o."date" AS event_time,
               'overtake_suffered' AS event_type,
               'Passed by car #' || o.overtaking_driver_number AS detail,
               NULL AS value
        FROM scope JOIN silver_overtakes o ON o.session_key = scope.session_key
    """, con)
    frames.append(ot_lost)

    # Race control messages naming a specific driver.
    rc_driver = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT rc.session_key, rc.driver_number, rc.lap_number,
               rc."date" AS event_time,
               'race_control_driver' AS event_type,
               rc.message AS detail, NULL AS value
        FROM scope JOIN silver_race_control rc ON rc.session_key = scope.session_key
        WHERE rc.driver_number IS NOT NULL
    """, con)
    frames.append(rc_driver)

    # Race-wide messages: no driver_number, so they annotate the whole race.
    rc_race = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT rc.session_key, NULL AS driver_number, rc.lap_number,
               rc."date" AS event_time,
               'race_control' AS event_type,
               rc.message AS detail, NULL AS value
        FROM scope JOIN silver_race_control rc ON rc.session_key = scope.session_key
        WHERE rc.driver_number IS NULL
          AND rc.category IN ('SafetyCar', 'Flag', 'Drs')
          AND COALESCE(rc.scope, '') != 'Sector'
    """, con)
    frames.append(rc_race)

    radio = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT tr.session_key, tr.driver_number, NULL AS lap_number,
               tr."date" AS event_time,
               'team_radio' AS event_type,
               'Team radio' AS detail, NULL AS value
        FROM scope JOIN silver_team_radio tr ON tr.session_key = scope.session_key
    """, con)
    frames.append(radio)

    out = pd.concat(frames, ignore_index=True)
    out["event_time"] = pd.to_datetime(out["event_time"], format="ISO8601", utc=True)
    return out.sort_values(["session_key", "event_time"])


def build_fact_championship(con) -> pd.DataFrame:
    """
    Constructor standings before and after each race.

    The API reports these per session as position/points "start" and "current",
    which is what makes "how did this race move the championship" answerable
    without reconstructing the table by summing results.

    Renamed constructors are collapsed the same way as everywhere else, so a
    team's championship line is continuous across a rename. Where two source
    rows now share a normalised name in one race, points are summed and the
    better (numerically lower) position kept — this cannot happen with the
    current mapping, since no two mapped names competed in the same season,
    but summing is the behaviour that stays correct if one ever does.
    """
    df = pd.read_sql(f"""
        WITH scope AS ({RACE_SCOPE})
        SELECT c.session_key, c.team_name,
               c.position_start, c.position_current,
               c.points_start, c.points_current
        FROM scope JOIN silver_championship_teams c
             ON c.session_key = scope.session_key
    """, con)

    if df.empty:
        return df

    df = normalize_teams(df)
    for col in ["position_start", "position_current", "points_start",
                "points_current"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    out = (df.groupby(["session_key", "team_name"], as_index=False)
             .agg(position_start=("position_start", "min"),
                  position_current=("position_current", "min"),
                  points_start=("points_start", "sum"),
                  points_current=("points_current", "sum")))

    out["points_gained"] = out["points_current"] - out["points_start"]
    out["positions_gained"] = out["position_start"] - out["position_current"]
    return out.sort_values(["session_key", "position_current"])


# --- runner ----------------------------------------------------------------------

BUILDERS = {
    "dim_race": build_dim_race,
    "dim_driver": build_dim_driver,
    "dim_team": build_dim_team,
    "fact_driver_race": build_fact_driver_race,
    "fact_lap": build_fact_lap,
    "fact_event": build_fact_event,
    "fact_championship": build_fact_championship,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the descriptive serving layer.")
    ap.add_argument("--tables", nargs="*", default=None, help="subset to rebuild")
    ap.add_argument("--no-csv", action="store_true", help="write to gold_f1.db only")
    ap.add_argument("--no-db", action="store_true", help="write CSVs only")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[FAIL] silver database not found at {DB_PATH}")
        return 1

    targets = args.tables or list(BUILDERS)
    unknown = [t for t in targets if t not in BUILDERS]
    if unknown:
        print(f"[FAIL] unknown table(s): {unknown}")
        return 1

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    print("=" * 74)
    print("DESCRIPTIVE SERVING LAYER")
    print(f"silver: {DB_PATH}")
    print(f"csv:    {DASHBOARD_DIR}")
    #print(f"gold:   {GOLD_DB_PATH}")
    print("=" * 74)

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    #gold = None if args.no_db else sqlite3.connect(str(GOLD_DB_PATH))

    n_races = pd.read_sql(f"SELECT COUNT(*) AS n FROM ({RACE_SCOPE})", con)["n"].iloc[0]
    print(f"\nscope: {n_races} completed races\n")

    failures = []
    for name in targets:
        started = time.time()
        try:
            df = BUILDERS[name](con)
        except Exception as exc:
            print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
            failures.append(name)
            continue

        # Freshness stamp, so the dashboard can show how current it is.
        df["generated_at"] = generated_at

        if not args.no_csv:
            df.to_csv(DASHBOARD_DIR / f"{name}.csv", index=False)
        #if gold is not None:
            # Drop and rewrite: these are derived views, not a log.
        #    df.to_sql(name, gold, if_exists="replace", index=False)

        print(f"  {name:20s} {len(df):>8,} rows x {len(df.columns):>3} cols  "
              f"{time.time() - started:.1f}s")

    con.close()
    #if gold is not None:
    #    gold.close()

    print("\n" + "=" * 74)
    if failures:
        print(f"FAILED: {failures}")
        print("=" * 74)
        return 1
    print(f"Built {len(targets)} table(s).")
    print(f"Serving layer written to {DASHBOARD_DIR}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())