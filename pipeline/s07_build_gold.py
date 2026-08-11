"""
Build the gold layer.

WHAT GOLD IS FOR
================
Silver is a clean mirror of the API. Gold is the layer every question reads
from: descriptive queries, diagnostic tests, the predictive work, the
perfect-lap tool, the current Streamlit dashboard and the application meant to
replace it. It is wide and denormalised on purpose, so a question is a filter
rather than a chain of joins.

Three rules the whole layer obeys:

  1. IT FLAGS, IT DOES NOT FILTER. Every row in the silver source survives into
     gold. A red flag lap, a 2,485 second pit stop and a phantom stint are all
     real records of real events, and some future question will ask about them.
     A consumer that wants them gone selects on a flag.

  2. NO CONSTANT THAT DECIDES THE ANSWER. Every threshold in here was either
     measured or removed. Where a rule needed a cutoff that changed the result,
     the rule was replaced rather than tuned. NOTES_LOG #47 and #49 record the
     two times that mattered.

  3. IT DOES NOT HOLD THE TRAINING MATRIX. That is assembled outside the
     database, because a training matrix encodes modelling decisions (which
     seasons, which lag, which target) and gold must not take sides on those.

WHY IT EXISTS
=============
GOLD_INVENTORY.md scanned 54 files and found "which laps count" answered five
ways across 14 call sites, the documented answer used at none of them, race
scoping rewritten 116 times, and team normalization applied by hand at 21
places with s03_verify warning about it on every run. Every one of those is a
property of the data being re-decided by whoever happens to be querying.

THE DECISIONS, AND THE EVIDENCE FOR EACH
========================================
Valid lap (NOTES_LOG #49). The documented 60-300s window is not a validity
rule. Its floor is inert: zero laps of 239,102 are under 60s and the fastest
lap in the dataset is 63.971s. Its ceiling was redundant with `neutralised`;
after fixing the safety-car-start bug it removes one lap in 81,689. So
`is_valid_lap` is `lap_duration IS NOT NULL`, and `is_representative_lap`
composes it with the flags. `pace_ratio` keeps the one remaining outlier
visible instead of deleting it.

Pit duration. The "outliers up to 16,921s" are not errors. 96.4% of race stops
over 60 seconds happened under a caution, 92% under a red flag, and the extreme
tail is Zandvoort 2023 laps 63-64 where the field sat in the pit lane during a
stoppage. Scoping by session type and joining the lap's own caution flag
explains them, so no duration fence is baked in anywhere. Green race stops sit
at a 23.3s median and a 41.2s 99th percentile.

lane_duration is dropped: it is byte-identical to pit_duration across all
22,898 populated rows, maximum absolute difference 0.0.

stop_duration is kept but its coverage is published rather than assumed.
`STOP_DURATION_MIN_YEAR = 2024` reads as "usable from 2024"; actual race
coverage is 0%, 18.1%, 85.5%, 33.8% by year. Comparing 2024 against 2025 on
this column compares an 18% sample against an 85% one.

Stints. 27 phantoms (lap_end < lap_start), 42 genuine overlaps and 56 coverage
gaps, all flagged, none removed. An apparent 13,959 overlaps turned out to be
the convention that a stint ends on the lap the car pits and the next begins on
that same lap. Testing the boundary rather than assuming it is why that is 42
and not 13,959.

Usage:
    python pipeline\\s07_build_gold.py                    # plan only
    python pipeline\\s07_build_gold.py --execute          # build everything
    python pipeline\\s07_build_gold.py --execute --only gold_lap gold_pit
    python pipeline\\s07_build_gold.py --list
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH, GOLD_DB_PATH  # noqa: E402

# Year-over-year renames collapsed into one label so a multi-year grouping does
# not silently split a team in two. Lifted from data_prep.TEAM_NAME_MAP, which
# 21 call sites currently have to remember to apply by hand and which
# s03_verify warns about on every run. Cadillac is deliberately absent: a new
# constructor for 2026, not a rename.
TEAM_NAME_MAP = {
    "AlphaTauri": "RB Family",
    "RB": "RB Family",
    "Racing Bulls": "RB Family",
    "Alfa Romeo": "Sauber Family",
    "Kick Sauber": "Sauber Family",
    "Audi": "Sauber Family",
}

# A grid is published against the session that SET it, not the one it applies
# to. Attaching a race to its grid therefore needs this hop, which is the sort
# of thing every consumer currently rediscovers.
GRID_SOURCE = {"Race": "Qualifying", "Sprint": "Sprint Qualifying"}

BUILDERS: dict[str, callable] = {}
ORDER: list[str] = []


def builds(name: str):
    """Registers a table builder so --only and --list stay in sync with reality."""
    def wrap(fn):
        BUILDERS[name] = fn
        ORDER.append(name)
        return fn
    return wrap


def conform_team(s: pd.Series) -> pd.Series:
    return s.replace(TEAM_NAME_MAP)


# ============================================================================
# DIMENSIONS
# ============================================================================

@builds("gold_session")
def build_session(con) -> pd.DataFrame:
    """
    One row per session, with meeting and circuit already attached.

    This is the table that kills the 116 rewrites of the laps -> sessions ->
    meetings join for everything that is not a lap. The derived columns at the
    end are the session-level constants every pace question needs and currently
    recomputes: the green median lap, and how much of the session was
    neutralised.
    """
    s = pd.read_sql("""
        SELECT s.session_key, s.session_type, s.session_name,
               s.date_start AS session_date_start, s.date_end AS session_date_end,
               s.year, s.gmt_offset, s.is_cancelled,
               s.meeting_key, s.circuit_key, s.circuit_short_name,
               s.country_code, s.country_name, s.location,
               m.meeting_name, m.meeting_official_name, m.circuit_type,
               m.date_start AS meeting_date_start
        FROM silver_sessions s
        LEFT JOIN silver_meetings m ON m.meeting_key = s.meeting_key
    """, con)

    stats = pd.read_sql("""
        SELECT l.session_key,
               COUNT(*)                                  AS n_laps,
               COUNT(DISTINCT l.driver_number)           AS n_drivers,
               SUM(CASE WHEN f.neutralised = 1 THEN 1 ELSE 0 END)
                                                         AS n_neutralised_laps,
               SUM(CASE WHEN f.sc_flag  = 1 THEN 1 ELSE 0 END) AS n_sc_laps,
               SUM(CASE WHEN f.vsc_flag = 1 THEN 1 ELSE 0 END) AS n_vsc_laps,
               SUM(CASE WHEN f.red_flag = 1 THEN 1 ELSE 0 END) AS n_red_laps
        FROM silver_laps l
        JOIN silver_lap_flags f
          ON  f.session_key   = l.session_key
          AND f.driver_number = l.driver_number
          AND f.lap_number    = l.lap_number
        GROUP BY l.session_key
    """, con)

    green = pd.read_sql("""
        SELECT l.session_key, l.lap_duration
        FROM silver_laps l
        JOIN silver_lap_flags f
          ON  f.session_key   = l.session_key
          AND f.driver_number = l.driver_number
          AND f.lap_number    = l.lap_number
        WHERE l.lap_duration IS NOT NULL AND f.neutralised = 0
          AND COALESCE(l.is_pit_out_lap, 0) = 0
    """, con).groupby("session_key").lap_duration.agg(
        green_median_lap_s="median", best_lap_s="min",
        n_representative_laps="size")

    periods = pd.read_sql("""
        SELECT session_key, COUNT(*) AS n_caution_periods,
               SUM(duration_seconds) AS caution_seconds
        FROM silver_caution_periods GROUP BY session_key
    """, con)

    out = (s.merge(stats, on="session_key", how="left")
             .merge(green, on="session_key", how="left")
             .merge(periods, on="session_key", how="left"))
    for c in ("n_laps", "n_drivers", "n_neutralised_laps", "n_sc_laps",
              "n_vsc_laps", "n_red_laps", "n_representative_laps",
              "n_caution_periods"):
        out[c] = out[c].fillna(0).astype(int)
    out["has_laps"] = (out.n_laps > 0).astype(int)
    return out


@builds("gold_driver")
def build_driver(con) -> pd.DataFrame:
    """
    One row per PERSON, keyed (driver_number, full_name).

    A DRIVER NUMBER IS NOT A DRIVER, and this table was built wrong once before
    being caught. Numbers are reassigned between seasons and lent to reserves
    for practice sessions: 34 of 57 numbers in this dataset belong to more than
    one person. Number 1 is Verstappen 2023-2025, Paul Aron in 2023 and Norris
    in 2026. Number 3 is Ricciardo, then O'Sullivan, then Verstappen.

    Keying on driver_number alone and letting the most recent name win produced
    57 rows in which every Verstappen lap resolved to Norris. Nothing would have
    reported that; it would simply have been wrong on every page.

    (driver_number, year) is what s04's dim_driver uses and is closer, but still
    not unique: number 1 in 2023 is both Verstappen and Aron. The person is the
    grain, so the person is the key.

    Facts do not join through here. gold_lap and gold_entry already carry
    driver_full_name resolved per session, which is the only place the mapping
    is unambiguous. This table is for lookups and rosters.
    """
    d = pd.read_sql("""
        SELECT d.driver_number, d.full_name, d.broadcast_name, d.name_acronym,
               d.first_name, d.last_name, d.country_code, d.headshot_url,
               d.team_name, d.team_colour, s.year, s.date_start
        FROM silver_drivers d
        JOIN silver_sessions s USING (session_key)
        WHERE d.full_name IS NOT NULL
        ORDER BY s.date_start
    """, con)

    def latest(g):
        return pd.Series({
            "broadcast_name": g.broadcast_name.ffill().iloc[-1],
            "name_acronym": g.name_acronym.ffill().iloc[-1],
            "first_name": g.first_name.ffill().iloc[-1],
            "last_name": g.last_name.ffill().iloc[-1],
            "country_code": g.country_code.ffill().iloc[-1],
            "headshot_url": g.headshot_url.ffill().iloc[-1],
            "team_colour": g.team_colour.ffill().iloc[-1],
            "last_team_name": conform_team(g.team_name.ffill()).iloc[-1],
            "first_year": int(g.year.min()),
            "last_year": int(g.year.max()),
            "n_sessions": int(len(g)),
            "shares_number": 0,  # filled in below
        })

    out = (d.groupby(["driver_number", "full_name"])
           .apply(latest, include_groups=False).reset_index())
    # Flag the numbers that are shared, so a consumer joining on driver_number
    # alone can be told it is ambiguous instead of quietly getting one of them.
    shared = out.groupby("driver_number").full_name.transform("size") > 1
    out["shares_number"] = shared.astype(int)
    return out


@builds("gold_entry")
def build_entry(con) -> pd.DataFrame:
    """
    One row per (session, driver): who drove for whom, with the team conformed.

    The join every team-level question needs. team_name_raw is kept beside the
    conformed label so an audit can always see what the API actually said.
    """
    e = pd.read_sql("""
        SELECT d.session_key, d.driver_number, d.meeting_key,
               d.team_name AS team_name_raw, d.team_colour,
               d.full_name AS driver_full_name, d.name_acronym AS driver_acronym,
               s.year, s.session_name, s.session_type
        FROM silver_drivers d
        JOIN silver_sessions s USING (session_key)
    """, con)
    e["team_name"] = conform_team(e.team_name_raw)
    # 14 rows of the 2023 Hungarian GP young-driver test carry no team at all.
    # data_prep drops them. Gold keeps the row and flags it, because dropping
    # an entry is a consumer's decision.
    e["has_team"] = e.team_name.notna().astype(int)
    return e


# ============================================================================
# FACTS
# ============================================================================

@builds("gold_lap")
def build_lap(con) -> pd.DataFrame:
    """
    The lap fact, with session, meeting, team and driver context on the row.

    is_valid_lap is the first conformed column in the layer and the evidence
    for it is in the module docstring and NOTES_LOG #49.
    """
    laps = pd.read_sql("""
        SELECT
            l.session_key, l.driver_number, l.lap_number, l.meeting_key,
            l.date_start, l.lap_duration,
            l.duration_sector_1, l.duration_sector_2, l.duration_sector_3,
            l.i1_speed, l.i2_speed, l.st_speed,
            COALESCE(l.is_pit_out_lap, 0)          AS is_pit_out_lap,
            f.sc_flag, f.vsc_flag, f.red_flag,
            f.yellow_sector_flag, f.neutralised,
            s.year, s.session_type, s.session_name,
            s.circuit_key, s.circuit_short_name, s.country_name, s.location,
            s.date_start                            AS session_date_start,
            m.meeting_name,
            d.team_name                             AS team_name_raw,
            d.full_name                             AS driver_full_name,
            d.name_acronym                          AS driver_acronym
        FROM silver_laps l
        JOIN silver_lap_flags f
          ON  f.session_key   = l.session_key
          AND f.driver_number = l.driver_number
          AND f.lap_number    = l.lap_number
        JOIN silver_sessions s ON s.session_key = l.session_key
        LEFT JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        LEFT JOIN silver_drivers d
          ON  d.session_key   = l.session_key
          AND d.driver_number = l.driver_number
    """, con)

    df = laps
    df["team_name"] = conform_team(df.team_name_raw)
    df["is_valid_lap"] = df.lap_duration.notna().astype(int)

    # neutralised is NULL for the 480 laps with no date_start, which cannot be
    # placed against a caution period. A null is treated as "not known to be
    # clean" rather than as clean.
    clean = (df.neutralised.fillna(1) == 0) & (df.is_pit_out_lap == 0)
    df["is_representative_lap"] = (df.is_valid_lap.astype(bool)
                                   & clean).astype(int)

    # Reference is the median of that session's own representative laps. A
    # median needs no threshold and no single car can move it, the same
    # reasoning the restart rule rests on (NOTES_LOG #47).
    green = (df[df.is_representative_lap == 1]
             .groupby("session_key").lap_duration.median())
    df["session_green_median_s"] = df.session_key.map(green)
    df["pace_ratio"] = df.lap_duration / df.session_green_median_s

    df["date_end"] = (
        pd.to_datetime(df.date_start, format="ISO8601", utc=True,
                       errors="coerce")
        + pd.to_timedelta(df.lap_duration, unit="s")
    ).dt.strftime("%Y-%m-%dT%H:%M:%S.%f%z")

    return df[[
        "session_key", "driver_number", "lap_number", "meeting_key",
        "year", "session_type", "session_name", "session_date_start",
        "circuit_key", "circuit_short_name", "country_name", "location",
        "meeting_name", "team_name", "team_name_raw", "driver_full_name",
        "driver_acronym", "date_start", "date_end", "lap_duration",
        "duration_sector_1", "duration_sector_2", "duration_sector_3",
        "i1_speed", "i2_speed", "st_speed", "is_pit_out_lap",
        "sc_flag", "vsc_flag", "red_flag", "yellow_sector_flag", "neutralised",
        "is_valid_lap", "is_representative_lap",
        "session_green_median_s", "pace_ratio",
    ]]


@builds("gold_stint")
def build_stint(con) -> pd.DataFrame:
    """
    Tyre stints, with the three structural defects flagged rather than removed.

    THE BOUNDARY CONVENTION, which cost a wrong measurement to find. A stint
    ends on the lap the car pits and the next stint begins on that SAME lap, so
    `lap_start <= previous lap_end` matches 13,914 perfectly normal stints.
    Only `lap_start < previous lap_end` is a real overlap, and there are 42.
    Testing the boundary instead of assuming it is the difference between
    flagging 40% of the table and flagging 0.2% of it.
    """
    st = pd.read_sql("""
        SELECT st.session_key, st.driver_number, st.stint_number,
               st.meeting_key, st.lap_start, st.lap_end,
               st.compound, st.tyre_age_at_start,
               s.year, s.session_name, s.session_type, s.circuit_short_name,
               d.team_name AS team_name_raw
        FROM silver_stints st
        JOIN silver_sessions s USING (session_key)
        LEFT JOIN silver_drivers d
          ON  d.session_key   = st.session_key
          AND d.driver_number = st.driver_number
        ORDER BY st.session_key, st.driver_number, st.stint_number
    """, con)

    st["team_name"] = conform_team(st.team_name_raw)
    st["stint_laps"] = st.lap_end - st.lap_start + 1

    # 27 rows end before they start. Two shapes: an off-by-one on testing days,
    # and lap_start=1 lap_end=0 for a car that never completed a lap.
    st["is_phantom_stint"] = (st.lap_end < st.lap_start).fillna(False).astype(int)
    st["has_lap_range"] = st[["lap_start", "lap_end"]].notna().all(axis=1).astype(int)

    prev_end = st.groupby(["session_key", "driver_number"]).lap_end.shift()
    st["overlaps_previous"] = (st.lap_start < prev_end).fillna(False).astype(int)
    st["gap_from_previous"] = st.lap_start - prev_end

    st["is_valid_stint"] = ((st.has_lap_range == 1)
                            & (st.is_phantom_stint == 0)
                            & (st.overlaps_previous == 0)).astype(int)
    # TEST_UNKNOWN and UNKNOWN are the API saying it does not know, not a
    # compound. Kept as-is, flagged so a compound analysis can exclude them
    # without hardcoding the two strings at every call site.
    st["has_known_compound"] = (~st.compound.isna()
                                & ~st.compound.isin(["UNKNOWN", "TEST_UNKNOWN"])
                                ).astype(int)
    return st.drop(columns=["session_type"])


@builds("gold_pit")
def build_pit(con) -> pd.DataFrame:
    """
    Pit stops, with each stop joined to the caution state of its own lap.

    WHY THERE IS NO DURATION FENCE. The inventory listed "pit duration outliers
    up to 16,921s" as something gold should filter. Measuring them showed they
    are not errors. 96.4% of race stops over 60 seconds happened under a
    caution and 92% under a red flag; the extreme tail is Zandvoort 2023 laps
    63-64, the whole field sitting in the pit lane during a stoppage. Once the
    session type is scoped and the lap's own caution flag is attached, green
    race stops have a 23.3s median and a 41.2s 99th percentile and there is
    nothing left to fence off.

    The Tukey fence at pit_stops_05.sql was re-derived and is population
    dependent: 36.76s over all race stops, 29.65s over green race stops only.
    A number that moves with the query is a local choice, so it stays at that
    call site rather than becoming a property of the data.

    lane_duration is NOT carried. It is byte-identical to pit_duration across
    all 22,898 populated rows, maximum absolute difference 0.0.

    stop_duration IS carried, but see has_stop_duration: race coverage runs
    0%, 18.1%, 85.5%, 33.8% by year, so `STOP_DURATION_MIN_YEAR = 2024` badly
    overstates what is available in 2024.
    """
    p = pd.read_sql("""
        SELECT p.session_key, p.driver_number, p.lap_number, p.meeting_key,
               p.date, p.stop_duration, p.pit_duration,
               f.sc_flag, f.vsc_flag, f.red_flag, f.neutralised,
               s.year, s.session_name, s.session_type, s.circuit_short_name,
               d.team_name AS team_name_raw
        FROM silver_pit p
        JOIN silver_sessions s ON s.session_key = p.session_key
        LEFT JOIN silver_lap_flags f
          ON  f.session_key   = p.session_key
          AND f.driver_number = p.driver_number
          AND f.lap_number    = p.lap_number
        LEFT JOIN silver_drivers d
          ON  d.session_key   = p.session_key
          AND d.driver_number = p.driver_number
    """, con)

    p["team_name"] = conform_team(p.team_name_raw)
    p["under_caution"] = p.neutralised.fillna(0).astype(int)
    p["has_stop_duration"] = p.stop_duration.notna().astype(int)
    p["is_race_stop"] = (p.session_name == "Race").astype(int)
    # The conformed population for "how long does a pit stop take".
    p["is_green_race_stop"] = ((p.is_race_stop == 1)
                               & (p.under_caution == 0)
                               & p.pit_duration.notna()).astype(int)
    return p.drop(columns=["session_type"])


@builds("gold_session_result")
def build_session_result(con) -> pd.DataFrame:
    """
    Classified result per driver per session, with the grid they started from.

    THE GRID HOP. silver_starting_grid is published against the session that
    SET the grid, never the one it applies to, so it exists only on Qualifying
    and Sprint Qualifying rows. Attaching a race to its grid means going out to
    the meeting and back in to the qualifying session. Doing that here is the
    point: every consumer currently rediscovers it or silently goes without.
    """
    r = pd.read_sql("""
        SELECT r.session_key, r.driver_number, r.meeting_key,
               r.position, r.number_of_laps, r.dnf, r.dns, r.dsq,
               r.duration_race_seconds, r.gap_to_leader_seconds,
               r.gap_to_leader_laps, r.points,
               s.year, s.session_name, s.session_type, s.circuit_short_name,
               s.meeting_key AS s_meeting_key,
               d.team_name AS team_name_raw,
               d.full_name AS driver_full_name, d.name_acronym AS driver_acronym
        FROM silver_session_result r
        JOIN silver_sessions s ON s.session_key = r.session_key
        LEFT JOIN silver_drivers d
          ON  d.session_key   = r.session_key
          AND d.driver_number = r.driver_number
    """, con)
    r["team_name"] = conform_team(r.team_name_raw)

    grid = pd.read_sql("""
        SELECT g.driver_number, g.position AS grid_position,
               g.lap_duration AS grid_lap_duration,
               s.meeting_key, s.session_name AS grid_session_name
        FROM silver_starting_grid g
        JOIN silver_sessions s USING (session_key)
    """, con)

    r["grid_session_name"] = r.session_name.map(GRID_SOURCE)
    out = r.merge(grid, on=["meeting_key", "driver_number", "grid_session_name"],
                  how="left")
    out["has_grid"] = out.grid_position.notna().astype(int)
    out["positions_gained"] = out.grid_position - out.position
    out["classified"] = ((out.dnf.fillna(0) == 0) & (out.dns.fillna(0) == 0)
                         & (out.dsq.fillna(0) == 0)
                         & out.position.notna()).astype(int)
    return out.drop(columns=["session_type", "s_meeting_key"])


@builds("gold_weather")
def build_weather(con) -> pd.DataFrame:
    """Weather readings with session context. Grain is (session_key, date)."""
    return pd.read_sql("""
        SELECT w.session_key, w.date, w.meeting_key,
               w.air_temperature, w.track_temperature, w.humidity, w.pressure,
               w.rainfall, w.wind_speed, w.wind_direction,
               s.year, s.session_name, s.circuit_short_name
        FROM silver_weather w JOIN silver_sessions s USING (session_key)
    """, con)


@builds("gold_overtake")
def build_overtake(con) -> pd.DataFrame:
    """
    Overtakes, with both drivers' teams conformed.

    Two joins to gold_entry rather than one, because "did a McLaren pass a
    Ferrari" needs both sides and every consumer currently joins only one.
    """
    o = pd.read_sql("""
        SELECT o.session_key, o.date, o.meeting_key, o.position,
               o.overtaking_driver_number, o.overtaken_driver_number,
               s.year, s.session_name, s.circuit_short_name
        FROM silver_overtakes o JOIN silver_sessions s USING (session_key)
    """, con)
    ent = pd.read_sql("""
        SELECT session_key, driver_number, team_name FROM silver_drivers
    """, con)
    ent["team_name"] = conform_team(ent.team_name)
    for side in ("overtaking", "overtaken"):
        o = o.merge(ent.rename(columns={
            "driver_number": f"{side}_driver_number",
            "team_name": f"{side}_team_name"}),
            on=["session_key", f"{side}_driver_number"], how="left")
    o["same_team"] = (o.overtaking_team_name == o.overtaken_team_name).astype(int)
    return o


@builds("gold_position")
def build_position(con) -> pd.DataFrame:
    """Position over time. Grain is (session_key, driver_number, date)."""
    return pd.read_sql("""
        SELECT p.session_key, p.driver_number, p.meeting_key, p.date, p.position,
               s.year, s.session_name, s.circuit_short_name
        FROM silver_position p JOIN silver_sessions s USING (session_key)
    """, con)


@builds("gold_race_control")
def build_race_control(con) -> pd.DataFrame:
    """
    Race control messages, with the classification the caution builder uses.

    is_red_flag_message reproduces s02b_caution_flags.is_red_flag, including
    the prose spelling that went undetected until 2026-08-11, and excluding the
    27 'RED FLAG INFRINGEMENT' stewards' notes that a naive substring match
    would turn into invented suspensions.
    """
    rc = pd.read_sql("""
        SELECT rc.id, rc.session_key, rc.meeting_key, rc.date, rc.driver_number,
               rc.lap_number, rc.category, rc.flag, rc.scope, rc.sector,
               rc.qualifying_phase, rc.message,
               s.year, s.session_name, s.circuit_short_name
        FROM silver_race_control rc JOIN silver_sessions s USING (session_key)
    """, con)
    m = rc.message.fillna("").str.strip().str.upper()
    rc["is_red_flag_message"] = (
        ((rc.category == "Flag") & (rc.flag == "RED"))
        | m.str.startswith("RED FLAG")).astype(int)
    rc["is_safety_car_message"] = (rc.category == "SafetyCar").astype(int)
    rc["is_sc_start_announcement"] = m.str.contains(
        r"START(?:ED)?\s+BEHIND THE SAFETY CAR", regex=True).astype(int)
    return rc


@builds("gold_championship")
def build_championship(con) -> pd.DataFrame:
    """
    Driver and constructor standings in one table, distinguished by entity_type.

    Kept together because every question about standings asks the same shape of
    it, and two near-identical tables would mean two near-identical queries.
    """
    d = pd.read_sql("""
        SELECT c.session_key, c.meeting_key, c.driver_number,
               NULL AS team_name_raw,
               c.position_start, c.position_current,
               c.points_start, c.points_current,
               s.year, s.session_name
        FROM silver_championship_drivers c
        JOIN silver_sessions s USING (session_key)
    """, con)
    d["entity_type"] = "driver"

    t = pd.read_sql("""
        SELECT c.session_key, c.meeting_key, NULL AS driver_number,
               c.team_name AS team_name_raw,
               c.position_start, c.position_current,
               c.points_start, c.points_current,
               s.year, s.session_name
        FROM silver_championship_teams c
        JOIN silver_sessions s USING (session_key)
    """, con)
    t["entity_type"] = "team"

    out = pd.concat([d, t], ignore_index=True)
    out["team_name"] = conform_team(out.team_name_raw)
    out["points_gained"] = out.points_current - out.points_start
    out["positions_gained"] = out.position_start - out.position_current
    return out


# ============================================================================
# AGGREGATES
# The tables built so questions become a filter instead of a join and a
# groupby. Each one is derived from the facts above, never from silver, so a
# defect fixed once is fixed everywhere.
# ============================================================================

@builds("gold_agg_driver_session")
def build_agg_driver_session(gold: dict) -> pd.DataFrame:
    """
    One row per driver per session: the shape most questions actually want.

    Pace is summarised over representative laps only, so a driver who spent
    half the race behind a safety car is not recorded as slow. Counts of the
    excluded laps sit beside it so that choice stays visible.
    """
    lap = gold["gold_lap"]
    rep = lap[lap.is_representative_lap == 1]

    pace = rep.groupby(["session_key", "driver_number"]).agg(
        representative_laps=("lap_duration", "size"),
        median_lap_s=("lap_duration", "median"),
        best_lap_s=("lap_duration", "min"),
        mean_lap_s=("lap_duration", "mean"),
        sd_lap_s=("lap_duration", "std"),
        median_pace_ratio=("pace_ratio", "median"),
    )
    allx = lap.groupby(["session_key", "driver_number"]).agg(
        laps_recorded=("lap_number", "size"),
        valid_laps=("is_valid_lap", "sum"),
        neutralised_laps=("neutralised", "sum"),
        pit_out_laps=("is_pit_out_lap", "sum"),
        max_lap_number=("lap_number", "max"),
    )
    ctx = lap.groupby(["session_key", "driver_number"]).first()[
        ["year", "session_name", "circuit_short_name", "meeting_name",
         "team_name", "driver_full_name", "driver_acronym", "meeting_key"]]

    out = ctx.join(allx).join(pace).reset_index()

    st = gold["gold_stint"]
    st_agg = st[st.is_valid_stint == 1].groupby(
        ["session_key", "driver_number"]).agg(
        n_stints=("stint_number", "size"),
        compounds_used=("compound", lambda s: ",".join(
            sorted(set(s.dropna())))),
    )
    out = out.merge(st_agg, on=["session_key", "driver_number"], how="left")

    pit = gold["gold_pit"]
    pit_all = pit.groupby(["session_key", "driver_number"]).agg(
        n_pit_stops=("lap_number", "size"))
    pit_green = pit[pit.is_green_race_stop == 1].groupby(
        ["session_key", "driver_number"]).agg(
        median_green_pit_s=("pit_duration", "median"),
        total_green_pit_s=("pit_duration", "sum"))
    out = (out.merge(pit_all, on=["session_key", "driver_number"], how="left")
              .merge(pit_green, on=["session_key", "driver_number"], how="left"))

    for c in ("n_stints", "n_pit_stops"):
        out[c] = out[c].fillna(0).astype(int)
    return out


@builds("gold_agg_driver_race")
def build_agg_driver_race(gold: dict) -> pd.DataFrame:
    """
    The wide race-level table: result, grid, pace, tyres, pit work, standings.

    This is the one the inventory's 116 rewritten joins were all reaching for.
    Restricted to Race and Sprint, because a classified result only means
    something there.
    """
    res = gold["gold_session_result"]
    res = res[res.session_name.isin(["Race", "Sprint"])].copy()

    agg = gold["gold_agg_driver_session"]
    keep = [c for c in agg.columns if c not in
            ("year", "session_name", "circuit_short_name", "meeting_name",
             "team_name", "driver_full_name", "driver_acronym", "meeting_key")]
    out = res.merge(agg[keep], on=["session_key", "driver_number"], how="left")

    champ = gold["gold_championship"]
    champ = champ[champ.entity_type == "driver"][
        ["session_key", "driver_number", "position_start", "position_current",
         "points_start", "points_current"]].rename(columns={
            "position_start": "champ_position_before",
            "position_current": "champ_position_after",
            "points_start": "champ_points_before",
            "points_current": "champ_points_after"})
    champ["driver_number"] = champ.driver_number.astype("Int64")
    out["driver_number"] = out.driver_number.astype("Int64")
    out = out.merge(champ, on=["session_key", "driver_number"], how="left")

    sess = gold["gold_session"][
        ["session_key", "green_median_lap_s", "n_neutralised_laps",
         "n_caution_periods", "session_date_start"]]
    return out.merge(sess, on="session_key", how="left")


@builds("gold_agg_driver_season")
def build_agg_driver_season(gold: dict) -> pd.DataFrame:
    """Per driver per season, over races and sprints."""
    r = gold["gold_agg_driver_race"]
    out = r.groupby(["year", "driver_number"]).agg(
        driver_full_name=("driver_full_name", "last"),
        driver_acronym=("driver_acronym", "last"),
        team_name=("team_name", "last"),
        races=("session_key", "size"),
        classified=("classified", "sum"),
        dnf=("dnf", "sum"),
        wins=("position", lambda s: int((s == 1).sum())),
        podiums=("position", lambda s: int((s <= 3).sum())),
        points=("points", "sum"),
        best_finish=("position", "min"),
        median_finish=("position", "median"),
        median_grid=("grid_position", "median"),
        mean_positions_gained=("positions_gained", "mean"),
        median_pace_ratio=("median_pace_ratio", "median"),
        total_laps=("laps_recorded", "sum"),
        total_pit_stops=("n_pit_stops", "sum"),
    ).reset_index()
    return out


@builds("gold_agg_team_season")
def build_agg_team_season(gold: dict) -> pd.DataFrame:
    """Per team per season, on the conformed team label."""
    r = gold["gold_agg_driver_race"]
    r = r[r.team_name.notna()]
    return r.groupby(["year", "team_name"]).agg(
        entries=("session_key", "size"),
        drivers=("driver_number", "nunique"),
        classified=("classified", "sum"),
        dnf=("dnf", "sum"),
        wins=("position", lambda s: int((s == 1).sum())),
        podiums=("position", lambda s: int((s <= 3).sum())),
        points=("points", "sum"),
        median_finish=("position", "median"),
        median_grid=("grid_position", "median"),
        median_pace_ratio=("median_pace_ratio", "median"),
        median_green_pit_s=("median_green_pit_s", "median"),
        total_pit_stops=("n_pit_stops", "sum"),
    ).reset_index()


@builds("gold_agg_circuit_season")
def build_agg_circuit_season(gold: dict) -> pd.DataFrame:
    """
    Per circuit per season, from the session table rather than the lap table.

    Race-only, because a circuit's character is a property of how the race runs
    there, and practice sessions would dominate any lap-weighted average.
    """
    s = gold["gold_session"]
    s = s[(s.session_name == "Race") & (s.has_laps == 1)]
    return s.groupby(["year", "circuit_short_name"]).agg(
        sessions=("session_key", "size"),
        circuit_key=("circuit_key", "first"),
        country_name=("country_name", "first"),
        green_median_lap_s=("green_median_lap_s", "median"),
        best_lap_s=("best_lap_s", "min"),
        n_laps=("n_laps", "sum"),
        n_neutralised_laps=("n_neutralised_laps", "sum"),
        n_caution_periods=("n_caution_periods", "sum"),
        caution_seconds=("caution_seconds", "sum"),
    ).reset_index().assign(
        neutralised_share=lambda d: (d.n_neutralised_laps / d.n_laps).round(4))


# Indexes, chosen from the predicates the existing consumers actually open
# with rather than from every column.
INDEXES = {
    "gold_session": ["(year, session_name)", "(meeting_key)",
                     "(circuit_short_name, year)"],
    "gold_entry": ["(session_key)", "(year, team_name)", "(driver_number)"],
    "gold_driver": ["(driver_number)", "(full_name)"],
    "gold_lap": ["(session_key)", "(year, session_name)",
                 "(year, driver_number)", "(year, team_name)",
                 "(circuit_short_name, year)",
                 "(is_representative_lap, session_name, year)"],
    "gold_stint": ["(session_key, driver_number)", "(year, compound)",
                   "(is_valid_stint, year)"],
    "gold_pit": ["(session_key, driver_number)", "(year, team_name)",
                 "(is_green_race_stop, year)"],
    "gold_session_result": ["(session_key)", "(year, session_name)",
                            "(year, team_name)", "(driver_number, year)"],
    "gold_weather": ["(session_key)"],
    "gold_overtake": ["(session_key)", "(year, session_name)"],
    "gold_position": ["(session_key, driver_number)"],
    "gold_race_control": ["(session_key)", "(is_red_flag_message)"],
    "gold_championship": ["(year, entity_type)", "(session_key)"],
    "gold_agg_driver_session": ["(session_key)", "(year, driver_number)"],
    "gold_agg_driver_race": ["(year, driver_number)", "(year, team_name)",
                             "(session_key)"],
    "gold_agg_driver_season": ["(year)", "(driver_number)"],
    "gold_agg_team_season": ["(year)", "(team_name)"],
    "gold_agg_circuit_season": ["(year)"],
}

# Aggregates read the finished facts, not silver, so a defect fixed once is
# fixed everywhere downstream.
FROM_GOLD = {"gold_agg_driver_session", "gold_agg_driver_race",
             "gold_agg_driver_season", "gold_agg_team_season",
             "gold_agg_circuit_season"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the gold layer.")
    ap.add_argument("--execute", action="store_true",
                    help="write gold_f1.db; without this nothing is written")
    ap.add_argument("--only", nargs="*", metavar="TABLE",
                    help="build a subset (dependencies are built too)")
    ap.add_argument("--list", action="store_true", help="list tables and exit")
    args = ap.parse_args()

    if args.list:
        for name in ORDER:
            kind = "aggregate" if name in FROM_GOLD else "fact/dimension"
            print(f"  {name:28s} {kind}")
        return 0

    print("=" * 74)
    print("GOLD LAYER BUILD")
    print(f"source: {DB_PATH}")
    print(f"target: {GOLD_DB_PATH}")
    print("=" * 74)

    if not DB_PATH.exists():
        print(f"[FAIL] silver database not found at {DB_PATH}")
        return 1

    wanted = set(args.only) if args.only else set(ORDER)
    unknown = wanted - set(ORDER)
    if unknown:
        print(f"[FAIL] unknown table(s): {sorted(unknown)}")
        return 1
    # An aggregate needs every fact, so asking for one implies all of them.
    if wanted & FROM_GOLD:
        wanted |= {n for n in ORDER if n not in FROM_GOLD}

    t0 = time.time()
    built: dict[str, pd.DataFrame] = {}
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        for name in ORDER:
            if name not in wanted:
                continue
            t1 = time.time()
            fn = BUILDERS[name]
            df = fn(built) if name in FROM_GOLD else fn(con)
            built[name] = df
            print(f"  {name:28s} {len(df):>9,} rows x {len(df.columns):>2} cols"
                  f"   {time.time() - t1:>5.1f}s")
    finally:
        con.close()

    if not args.execute:
        print("\nPlan only. Re-run with --execute to write gold_f1.db.")
        return 0

    print(f"\nWriting {GOLD_DB_PATH.name}...")
    GOLD_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(str(GOLD_DB_PATH))
    try:
        out.execute("""
            CREATE TABLE IF NOT EXISTS _gold_build_state (
                table_name  TEXT PRIMARY KEY,
                gold_rows   INTEGER NOT NULL,
                gold_cols   INTEGER NOT NULL,
                built_at    TEXT NOT NULL)
        """)
        for name, df in built.items():
            out.execute(f"DROP TABLE IF EXISTS {name}")
            df.to_sql(name, out, if_exists="replace", index=False,
                      chunksize=20_000)
            for i, cols in enumerate(INDEXES.get(name, []), start=1):
                out.execute(f"CREATE INDEX ix_{name}_{i} ON {name}{cols}")
            out.execute("""INSERT OR REPLACE INTO _gold_build_state
                           VALUES (?, ?, ?, datetime('now'))""",
                        (name, len(df), len(df.columns)))
        out.commit()
        out.execute("ANALYZE")
        out.commit()
    finally:
        out.close()

    size_mb = GOLD_DB_PATH.stat().st_size / 1e6
    total = sum(len(d) for d in built.values())
    print(f"  {len(built)} tables, {total:,} rows, {size_mb:,.1f} MB")
    print(f"\nDone in {time.time() - t0:.1f}s.")
    print("Rerun after any silver rebuild or s02b_caution_flags run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
