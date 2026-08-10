"""
s02_build_silver.py — rebuilds silver tables from the bronze (raw) tables.

Derived from SCHEMA MODELING/to_silver.sql. That file cannot be executed
programmatically as written: several statements are missing terminating
semicolons, and it interleaves `SELECT COUNT(*)` verification queries that are
interactive checks rather than build steps. The DDL and transforms here are
otherwise identical to it.

Each table is DROP / CREATE / INSERT, so the build is idempotent — rebuilding
produces the same result regardless of prior state.

Selective by design: telemetry (silver_car_data, silver_location) is ~35M rows
and is excluded unless explicitly requested, because most rebuilds don't touch it.

Usage
-----
    python pipeline\\s02_build_silver.py --list
    python pipeline\\s02_build_silver.py                      # all except telemetry
    python pipeline\\s02_build_silver.py --tables laps session_result
    python pipeline\\s02_build_silver.py --include-telemetry  # everything

After a backfill, rebuild the tables whose bronze data changed:
    python pipeline\\s02_build_silver.py --tables laps position weather \\
        race_control stints session_result intervals overtakes starting_grid
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH, BRONZE_DB_PATH  # noqa: E402

# Each entry: bronze source table -> list of statements, executed in order.
# Keys are the bare names; silver tables are silver_<key>.
BUILD: dict[str, list[str]] = {}

BUILD["meetings"] = ["""
DROP TABLE IF EXISTS silver_meetings
""", """
CREATE TABLE silver_meetings (
    meeting_key           INTEGER PRIMARY KEY,
    meeting_name          TEXT    NOT NULL,
    meeting_official_name TEXT    NOT NULL,
    "location"            TEXT    NOT NULL,
    country_key           INTEGER NOT NULL,
    country_code          TEXT    NOT NULL,
    country_name          TEXT    NOT NULL,
    country_flag          TEXT    NOT NULL,
    circuit_key           INTEGER NOT NULL,
    circuit_short_name    TEXT    NOT NULL,
    circuit_type          TEXT    NOT NULL,
    circuit_info_url      TEXT    NOT NULL,
    circuit_image         TEXT    NOT NULL,
    gmt_offset            TEXT    NOT NULL,
    date_start            TEXT    NOT NULL,
    date_end              TEXT    NOT NULL,
    year                  INTEGER NOT NULL,
    is_cancelled          INTEGER NOT NULL CHECK (is_cancelled IN (0, 1))
)
""", """
INSERT INTO silver_meetings
SELECT
    CAST(meeting_key AS INTEGER),
    meeting_name,
    meeting_official_name,
    "location",
    CAST(country_key AS INTEGER),
    country_code,
    country_name,
    country_flag,
    CAST(circuit_key AS INTEGER),
    circuit_short_name,
    circuit_type,
    circuit_info_url,
    circuit_image,
    gmt_offset,
    date_start,
    date_end,
    CAST(year AS INTEGER),
    CASE is_cancelled WHEN 'True' THEN 1 WHEN 'False' THEN 0 END
FROM bronze.meetings
"""]

BUILD["sessions"] = ["""
DROP TABLE IF EXISTS silver_sessions
""", """
CREATE TABLE silver_sessions (
    session_key         INTEGER PRIMARY KEY,
    session_type        TEXT    NOT NULL CHECK (session_type IN ('Practice', 'Race', 'Qualifying')),
    session_name        TEXT    NOT NULL,
    date_start          TEXT    NOT NULL,
    date_end            TEXT    NOT NULL,
    meeting_key         INTEGER NOT NULL,
    circuit_key         INTEGER NOT NULL,
    circuit_short_name  TEXT    NOT NULL,
    country_key         INTEGER NOT NULL,
    country_code        TEXT    NOT NULL,
    country_name        TEXT    NOT NULL,
    location            TEXT    NOT NULL,
    gmt_offset          TEXT    NOT NULL,
    year                INTEGER NOT NULL,
    is_cancelled        INTEGER NOT NULL CHECK (is_cancelled IN (0, 1)),
    FOREIGN KEY (meeting_key) REFERENCES silver_meetings(meeting_key)
)
""", """
INSERT INTO silver_sessions
SELECT
    CAST(session_key AS INTEGER),
    session_type,
    session_name,
    date_start,
    date_end,
    CAST(meeting_key AS INTEGER),
    CAST(circuit_key AS INTEGER),
    circuit_short_name,
    CAST(country_key AS INTEGER),
    country_code,
    country_name,
    location,
    gmt_offset,
    CAST(year AS INTEGER),
    CASE is_cancelled WHEN 'True' THEN 1 WHEN 'False' THEN 0 END
FROM bronze.sessions
"""]

BUILD["drivers"] = ["""
DROP TABLE IF EXISTS silver_drivers
""", """
CREATE TABLE silver_drivers (
    session_key     INTEGER NOT NULL,
    driver_number   INTEGER NOT NULL,
    meeting_key     INTEGER NOT NULL,
    broadcast_name  TEXT,
    full_name       TEXT,
    name_acronym    TEXT,
    team_name       TEXT,
    team_colour     TEXT,
    first_name      TEXT,
    last_name       TEXT,
    headshot_url    TEXT,
    country_code    TEXT,
    PRIMARY KEY (session_key, driver_number)
)
""", """
INSERT INTO silver_drivers
SELECT
    CAST(session_key AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(meeting_key AS INTEGER),
    broadcast_name, full_name, name_acronym,
    team_name, team_colour, first_name, last_name,
    headshot_url, country_code
FROM bronze.drivers
"""]

BUILD["laps"] = ["""
DROP TABLE IF EXISTS silver_laps
""", """
CREATE TABLE silver_laps (
    session_key         INTEGER NOT NULL,
    driver_number       INTEGER NOT NULL,
    lap_number          INTEGER NOT NULL,
    meeting_key         INTEGER NOT NULL,
    date_start          TEXT,
    duration_sector_1   REAL,
    duration_sector_2   REAL,
    duration_sector_3   REAL,
    i1_speed            REAL,
    i2_speed            REAL,
    st_speed            REAL,
    lap_duration        REAL,
    is_pit_out_lap      INTEGER CHECK (is_pit_out_lap IN (0, 1) OR is_pit_out_lap IS NULL),
    segments_sector_1   TEXT,
    segments_sector_2   TEXT,
    segments_sector_3   TEXT,
    PRIMARY KEY (session_key, driver_number, lap_number)
)
""", """
INSERT INTO silver_laps
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(lap_number    AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    date_start,
    CAST(duration_sector_1 AS REAL),
    CAST(duration_sector_2 AS REAL),
    CAST(duration_sector_3 AS REAL),
    CAST(i1_speed AS REAL),
    CAST(i2_speed AS REAL),
    CAST(st_speed AS REAL),
    CAST(lap_duration AS REAL),
    CASE is_pit_out_lap WHEN 'True' THEN 1 WHEN 'False' THEN 0 ELSE NULL END,
    segments_sector_1,
    segments_sector_2,
    segments_sector_3
FROM bronze.laps
"""]

BUILD["stints"] = ["""
DROP TABLE IF EXISTS silver_stints
""", """
CREATE TABLE silver_stints (
    session_key         INTEGER NOT NULL,
    driver_number       INTEGER NOT NULL,
    stint_number        INTEGER NOT NULL,
    meeting_key         INTEGER NOT NULL,
    lap_start           INTEGER,
    lap_end             INTEGER,
    compound            TEXT,
    tyre_age_at_start   INTEGER,
    PRIMARY KEY (session_key, driver_number, stint_number)
)
""", """
INSERT INTO silver_stints
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(stint_number  AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    CAST(lap_start AS INTEGER),
    CAST(lap_end   AS INTEGER),
    compound,
    CAST(tyre_age_at_start AS INTEGER)
FROM bronze.stints
"""]

BUILD["pit"] = ["""
DROP TABLE IF EXISTS silver_pit
""", """
CREATE TABLE silver_pit (
    session_key    INTEGER NOT NULL,
    driver_number  INTEGER NOT NULL,
    lap_number     INTEGER NOT NULL,
    meeting_key    INTEGER NOT NULL,
    "date"         TEXT    NOT NULL,
    stop_duration  REAL,
    lane_duration  REAL,
    pit_duration   REAL,
    PRIMARY KEY (session_key, driver_number, lap_number)
)
""", """
INSERT INTO silver_pit
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(lap_number    AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    "date",
    CAST(stop_duration AS REAL),
    CAST(lane_duration AS REAL),
    CAST(pit_duration  AS REAL)
FROM bronze.pit
"""]

BUILD["position"] = ["""
DROP TABLE IF EXISTS silver_position
""", """
CREATE TABLE silver_position (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key    INTEGER NOT NULL,
    driver_number  INTEGER NOT NULL,
    meeting_key    INTEGER NOT NULL,
    "date"         TEXT    NOT NULL,
    "position"     INTEGER NOT NULL
)
""", """
CREATE INDEX idx_position_session_driver_date
    ON silver_position (session_key, driver_number, "date")
""", """
INSERT INTO silver_position (session_key, driver_number, meeting_key, "date", "position")
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    "date",
    CAST("position"    AS INTEGER)
FROM bronze.position
"""]

BUILD["intervals"] = ["""
DROP TABLE IF EXISTS silver_intervals
""", """
CREATE TABLE silver_intervals (
    session_key            INTEGER NOT NULL,
    driver_number          INTEGER NOT NULL,
    "date"                 TEXT    NOT NULL,
    meeting_key            INTEGER NOT NULL,
    interval_seconds       REAL,
    interval_laps          INTEGER,
    gap_to_leader_seconds  REAL,
    gap_to_leader_laps     INTEGER,
    PRIMARY KEY (session_key, driver_number, "date")
)
""", """
INSERT INTO silver_intervals
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    "date",
    CAST(meeting_key   AS INTEGER),
    CASE
        WHEN "interval" LIKE '%LAP%' THEN NULL
        ELSE CAST("interval" AS REAL)
    END,
    CASE
        WHEN "interval" LIKE '%LAP%'
        THEN CAST(REPLACE(REPLACE(REPLACE("interval", '+', ''), ' LAPS', ''), ' LAP', '') AS INTEGER)
        ELSE NULL
    END,
    CASE
        WHEN gap_to_leader LIKE '%LAP%' THEN NULL
        ELSE CAST(gap_to_leader AS REAL)
    END,
    CASE
        WHEN gap_to_leader LIKE '%LAP%'
        THEN CAST(REPLACE(REPLACE(REPLACE(gap_to_leader, '+', ''), ' LAPS', ''), ' LAP', '') AS INTEGER)
        ELSE NULL
    END
FROM bronze.intervals
"""]

BUILD["overtakes"] = ["""
DROP TABLE IF EXISTS silver_overtakes
""", """
CREATE TABLE silver_overtakes (
    session_key              INTEGER NOT NULL,
    "date"                   TEXT    NOT NULL,
    overtaking_driver_number INTEGER NOT NULL,
    overtaken_driver_number  INTEGER NOT NULL,
    meeting_key              INTEGER NOT NULL,
    "position"               INTEGER NOT NULL,
    PRIMARY KEY (session_key, "date", overtaking_driver_number, overtaken_driver_number),
    CHECK (overtaking_driver_number != overtaken_driver_number)
)
""", """
INSERT INTO silver_overtakes
SELECT
    CAST(session_key              AS INTEGER),
    "date",
    CAST(overtaking_driver_number AS INTEGER),
    CAST(overtaken_driver_number  AS INTEGER),
    CAST(meeting_key              AS INTEGER),
    CAST("position"               AS INTEGER)
FROM bronze.overtakes
"""]

BUILD["race_control"] = ["""
DROP TABLE IF EXISTS silver_race_control
""", """
CREATE TABLE silver_race_control (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key       INTEGER NOT NULL,
    meeting_key       INTEGER NOT NULL,
    "date"            TEXT    NOT NULL,
    driver_number     INTEGER,
    lap_number        INTEGER,
    category          TEXT    NOT NULL CHECK (category IN
                          ('Flag', 'Other', 'SessionStatus', 'Drs', 'SafetyCar', 'CarEvent')),
    flag              TEXT,
    scope             TEXT    CHECK (scope IS NULL OR scope IN ('Sector', 'Driver', 'Track')),
    sector            INTEGER,
    qualifying_phase  INTEGER CHECK (qualifying_phase IS NULL OR qualifying_phase IN (1, 2, 3)),
    message           TEXT    NOT NULL
)
""", """
CREATE INDEX idx_race_control_session_date
    ON silver_race_control (session_key, "date")
""", """
INSERT INTO silver_race_control (
    session_key, meeting_key, "date", driver_number, lap_number,
    category, flag, scope, sector, qualifying_phase, message
)
SELECT
    CAST(session_key   AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    "date",
    CAST(driver_number AS INTEGER),
    CAST(lap_number    AS INTEGER),
    category,
    flag,
    scope,
    CAST(sector AS INTEGER),
    CAST(qualifying_phase AS INTEGER),
    message
FROM bronze.race_control
"""]

BUILD["session_result"] = ["""
DROP TABLE IF EXISTS silver_session_result
""", """
CREATE TABLE silver_session_result (
    session_key            INTEGER NOT NULL,
    driver_number          INTEGER NOT NULL,
    meeting_key            INTEGER NOT NULL,
    "position"             INTEGER,
    number_of_laps         INTEGER,
    dnf                    INTEGER NOT NULL CHECK (dnf IN (0, 1)),
    dns                    INTEGER NOT NULL CHECK (dns IN (0, 1)),
    dsq                    INTEGER NOT NULL CHECK (dsq IN (0, 1)),
    duration_race_seconds  REAL,
    duration_quali_json    TEXT,
    gap_to_leader_seconds  REAL,
    gap_to_leader_laps     INTEGER,
    gap_to_leader_quali_json TEXT,
    points                 REAL,
    PRIMARY KEY (session_key, driver_number)
)
""", """
INSERT INTO silver_session_result
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    CAST("position"    AS INTEGER),
    CAST(number_of_laps AS INTEGER),
    CASE dnf WHEN 'True' THEN 1 WHEN 'False' THEN 0 END,
    CASE dns WHEN 'True' THEN 1 WHEN 'False' THEN 0 END,
    CASE dsq WHEN 'True' THEN 1 WHEN 'False' THEN 0 END,
    CASE WHEN duration LIKE '[%' THEN NULL ELSE CAST(duration AS REAL) END,
    CASE WHEN duration LIKE '[%' THEN duration ELSE NULL END,
    CASE
        WHEN gap_to_leader LIKE '[%' THEN NULL
        WHEN gap_to_leader LIKE '%LAP%' THEN NULL
        ELSE CAST(gap_to_leader AS REAL)
    END,
    CASE
        WHEN gap_to_leader LIKE '%LAP%'
        THEN CAST(REPLACE(REPLACE(REPLACE(gap_to_leader, '+', ''), ' LAPS', ''), ' LAP', '') AS INTEGER)
        ELSE NULL
    END,
    CASE WHEN gap_to_leader LIKE '[%' THEN gap_to_leader ELSE NULL END,
    CAST(points AS REAL)
FROM bronze.session_result
"""]

BUILD["starting_grid"] = ["""
DROP TABLE IF EXISTS silver_starting_grid
""", """
CREATE TABLE silver_starting_grid (
    session_key    INTEGER NOT NULL,
    driver_number  INTEGER NOT NULL,
    meeting_key    INTEGER NOT NULL,
    "position"     INTEGER NOT NULL,
    lap_duration   REAL,
    PRIMARY KEY (session_key, driver_number)
)
""", """
INSERT INTO silver_starting_grid
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    CAST("position"    AS INTEGER),
    CAST(lap_duration  AS REAL)
FROM bronze.starting_grid
"""]

BUILD["team_radio"] = ["""
DROP TABLE IF EXISTS silver_team_radio
""", """
CREATE TABLE silver_team_radio (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key    INTEGER NOT NULL,
    driver_number  INTEGER NOT NULL,
    "date"         TEXT    NOT NULL,
    meeting_key    INTEGER NOT NULL,
    recording_url  TEXT    NOT NULL,
    UNIQUE (session_key, driver_number, "date")
)
""", """
CREATE INDEX idx_team_radio_session_driver
    ON silver_team_radio (session_key, driver_number)
""", """
INSERT INTO silver_team_radio (session_key, driver_number, "date", meeting_key, recording_url)
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    "date",
    CAST(meeting_key   AS INTEGER),
    recording_url
FROM bronze.team_radio
"""]

BUILD["weather"] = ["""
DROP TABLE IF EXISTS silver_weather
""", """
CREATE TABLE silver_weather (
    session_key         INTEGER NOT NULL,
    "date"              TEXT    NOT NULL,
    meeting_key         INTEGER NOT NULL,
    humidity            REAL    NOT NULL,
    pressure            REAL    NOT NULL,
    rainfall            INTEGER NOT NULL CHECK (rainfall IN (0, 1)),
    track_temperature   REAL    NOT NULL,
    air_temperature     REAL    NOT NULL,
    wind_speed          REAL    NOT NULL,
    wind_direction      INTEGER NOT NULL CHECK (wind_direction BETWEEN 0 AND 360),
    PRIMARY KEY (session_key, "date")
)
""", """
INSERT INTO silver_weather
SELECT DISTINCT
    CAST(session_key AS INTEGER),
    "date",
    CAST(meeting_key AS INTEGER),
    CAST(humidity          AS REAL),
    CAST(pressure          AS REAL),
    CAST(rainfall          AS INTEGER),
    CAST(track_temperature AS REAL),
    CAST(air_temperature   AS REAL),
    CAST(wind_speed        AS REAL),
    CAST(wind_direction    AS INTEGER)
FROM bronze.weather
"""]

BUILD["championship_drivers"] = ["""
DROP TABLE IF EXISTS silver_championship_drivers
""", """
CREATE TABLE silver_championship_drivers (
    session_key       INTEGER NOT NULL,
    driver_number     INTEGER NOT NULL,
    meeting_key       INTEGER NOT NULL,
    position_start    INTEGER,
    position_current  INTEGER NOT NULL,
    points_start      REAL    NOT NULL,
    points_current    REAL    NOT NULL,
    PRIMARY KEY (session_key, driver_number)
)
""", """
INSERT INTO silver_championship_drivers
SELECT
    CAST(session_key      AS INTEGER),
    CAST(driver_number    AS INTEGER),
    CAST(meeting_key      AS INTEGER),
    CAST(position_start   AS INTEGER),
    CAST(position_current AS INTEGER),
    CAST(points_start     AS REAL),
    CAST(points_current   AS REAL)
FROM bronze.championship_drivers
"""]

BUILD["championship_teams"] = ["""
DROP TABLE IF EXISTS silver_championship_teams
""", """
CREATE TABLE silver_championship_teams (
    session_key       INTEGER NOT NULL,
    team_name         TEXT    NOT NULL,
    meeting_key       INTEGER NOT NULL,
    position_start    INTEGER,
    position_current  INTEGER NOT NULL,
    points_start      REAL    NOT NULL,
    points_current    REAL    NOT NULL,
    PRIMARY KEY (session_key, team_name)
)
""", """
INSERT INTO silver_championship_teams
SELECT
    CAST(session_key      AS INTEGER),
    team_name,
    CAST(meeting_key      AS INTEGER),
    CAST(position_start   AS INTEGER),
    CAST(position_current AS INTEGER),
    CAST(points_start     AS REAL),
    CAST(points_current   AS REAL)
FROM bronze.championship_teams
"""]



# --- runner ----------------------------------------------------------------------

def count(con: sqlite3.Connection, table: str, schema: str = "main"):
    try:
        return con.execute(f'SELECT COUNT(*) FROM {schema}."{table}"').fetchone()[0]
    except sqlite3.OperationalError:
        return None


def record_build_state(con: sqlite3.Connection, name: str,
                       bronze_rows: int, silver_rows: int) -> None:
    """
    Record how much bronze this silver table was built from.

    Why this exists. s01_backfill.py writes straight into bronze and is not a
    pipeline step, so run_pipeline never learns that anything arrived: it decides
    whether to rebuild from the row count s01_ingest prints. A backfill therefore
    leaves silver silently behind bronze until somebody thinks to pass
    --force-rebuild.

    That is not hypothetical. On 2026-07-27 a backfill recovered 324,207 rows
    into bronze, the diagnostic notebooks were then run against a silver that did
    not contain them, and nothing anywhere reported a problem.

    Storing the bronze count at build time turns that into something the gate can
    check: if bronze holds more rows now than when silver was last built from it,
    silver is stale, and it can say so by name.
    """
    con.execute("""
        CREATE TABLE IF NOT EXISTS _silver_build_state (
            table_name   TEXT PRIMARY KEY,
            bronze_rows  INTEGER NOT NULL,
            silver_rows  INTEGER NOT NULL,
            built_at     TEXT    NOT NULL
        )
    """)
    con.execute("""
        INSERT OR REPLACE INTO _silver_build_state
            (table_name, bronze_rows, silver_rows, built_at)
        VALUES (?, ?, ?, datetime('now'))
    """, (name, bronze_rows, silver_rows))
    con.commit()


def build_table(con: sqlite3.Connection, name: str) -> tuple[bool, str]:
    """Runs one table's statements inside a transaction. Returns (ok, message)."""
    silver = f"silver_{name}"
    before = count(con, silver)
    bronze = count(con, name, "bronze")

    if bronze is None:
        return False, f"bronze table '{name}' does not exist"

    started = time.time()
    try:
        con.execute("BEGIN")
        for stmt in BUILD[name]:
            con.execute(stmt)
        con.commit()
    except Exception as exc:
        con.rollback()
        return False, f"{type(exc).__name__}: {exc}"

    after = count(con, silver)
    # After the commit, so a rolled-back build never claims to have happened.
    record_build_state(con, name, bronze, after)
    elapsed = time.time() - started

    delta = "" if before is None else f"  ({after - before:+,})"
    return True, f"{before if before is not None else 0:>10,} -> {after:>10,}{delta}   {elapsed:.1f}s"


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild silver tables from bronze.")
    ap.add_argument("--tables", nargs="*", default=None,
                    help="specific tables to rebuild (bare names, e.g. laps session_result)")
    ap.add_argument("--list", action="store_true", help="list available tables and exit")
    args = ap.parse_args()

    if args.list:
        print("Available tables:")
        for name in BUILD:
            print(f"  {name}")
        return 0

    if args.tables:
        unknown = [t for t in args.tables if t not in BUILD]
        if unknown:
            print(f"[FAIL] unknown table(s): {unknown}")
            print(f"       valid: {list(BUILD)}")
            return 1
        targets = args.tables
    else:
        targets = list(BUILD)

    if not DB_PATH.exists():
        print(f"[FAIL] silver database not found at {DB_PATH}")
        return 1
    if not BRONZE_DB_PATH.exists():
        print(f"[FAIL] bronze database not found at {BRONZE_DB_PATH}")
        return 1

    print("=" * 74)
    print("SILVER BUILD")
    print(f"silver: {DB_PATH}")
    print(f"bronze: {BRONZE_DB_PATH}")
    print(f"tables: {', '.join(targets)}")
    print("=" * 74)

    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-65536")

    # Bronze lives in its own file since the 2026-07-28 split; the build reads
    # from it via ATTACH and writes into main (silver).
    con.execute(f"ATTACH DATABASE '{BRONZE_DB_PATH.as_posix()}' AS bronze")

    failures = []
    for name in targets:
        print(f"\n{name}")
        ok, msg = build_table(con, name)
        if ok:
            print(f"  {msg}")
        else:
            print(f"  [FAIL] {msg}")
            failures.append(name)

    con.close()

    print("\n" + "=" * 74)
    if failures:
        print(f"FAILED: {failures}")
        print("Silver tables left in their previous state (transaction rolled back).")
        print("=" * 74)
        return 1

    print(f"Rebuilt {len(targets)} table(s) successfully.")
    print("Next: python pipeline\\s03_verify.py")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())