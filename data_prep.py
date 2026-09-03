"""
data_prep.py — Shared data loading and cleaning utilities for the F1 Reality Check
project. Import the functions needed rather than re-writing this logic in each
notebook or pipeline step.

Connection policy
-----------------
There is deliberately NO eager module-level connection. Each loader opens a fresh
READ-ONLY connection and closes it when done, because:
  - a read-write connection held open for the process lifetime can be mutated by
    an accidental write, and keeps a .wal file alive
  - DuckDB takes an exclusive lock on a file opened for writing, so one held
    connection stops the pipeline from rebuilding silver at all
  - a connection left open is one nobody remembers to close

New code should use the context manager:

    with get_connection() as con:
        df = read_sql("SELECT ...", con)

The legacy `dbset` name is still importable for the existing notebooks (see the
backward-compatibility section at the bottom), but it is now READ-ONLY and is
only created if something actually references it.

Where the database comes from
-----------------------------
THE PATH IS IMPORTED FROM pipeline/config.py AND IS NOT DECLARED HERE. It used
to be, and that is precisely how this file went stale: it named
"DATA INGESTION/f1.db" directly, so when the project moved to DuckDB it went on
opening the old SQLite file. Nothing failed. The notebooks kept running and kept
analysing a snapshot that stopped being updated, which is the worst of the three
possible outcomes.

Importing config also brings two things worth having here:

  - the pandas 2.x guard, which matters more in a notebook than anywhere else.
    NOTES_LOG #42 records pandas 3.x silently shifting results by a few percent.
  - read_sql, which normalises DuckDB's dtypes back to the ones this project was
    written against. Without it a nullable integer column arrives holding pd.NA
    instead of NaN, and `if row["red_flag"] == 1` raises rather than being False.
"""

import sys
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "pipeline"))
from config import DB_PATH, read_sql  # noqa: E402,F401

# Confirmed team renames across 2023-2026 (see the original data-prep investigation
# notebook for the evidence query and reasoning). Cadillac deliberately NOT mapped --
# genuinely new constructor for 2026, not a rename.
TEAM_NAME_MAP = {
    'AlphaTauri': 'RB Family',
    'RB': 'RB Family',
    'Racing Bulls': 'RB Family',
    'Alfa Romeo': 'Sauber Family',
    'Kick Sauber': 'Sauber Family',
    'Audi': 'Sauber Family',
}


# --- connections -----------------------------------------------------------------

@contextmanager
def get_connection(read_only=True):
    """
    Yields a DuckDB connection to silver and closes it on exit.

    read_only=True (the default) means no code path using this helper can modify
    the database. Pass read_only=False only from an explicit write step.

    TWO DUCKDB BEHAVIOURS TO KNOW ABOUT, both of which sqlite3 did not have.

    A connection opened for writing takes an EXCLUSIVE lock on the file. Holding
    one open in a notebook does not merely risk a stray write, it stops
    s02_build_silver from running at all until the kernel is restarted. That is
    the main reason read_only is the default rather than a nicety.

    Two connections to the same file in one process must agree about read_only.
    Asking for a writable one while `dbset` is already open read-only raises
    "Can't open a connection to same database file with a different
    configuration". Restart the kernel, or do the write from a script.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(f"database not found at {DB_PATH}")

    con = duckdb.connect(str(DB_PATH), read_only=read_only)
    try:
        yield con
    finally:
        con.close()


def _read_sql(query, params=()):
    """Runs a query on a fresh read-only connection and returns a DataFrame."""
    with get_connection() as con:
        return read_sql(query, con, list(params) if params else None)


# --- cleaning --------------------------------------------------------------------

def normalize_team_names(df, col='team_name'):
    """
    Collapses year-over-year team renames into one consistent label.
    Also drops rows with team_name IS NULL -- confirmed isolated to one session
    (2023 Hungarian GP Practice 1, 14 rows, a young-driver test session with no
    team assigned in the source data). No meaningful team bucket to map these to.
    """
    df = df.copy()
    df = df[df[col].notna()]
    df[col] = df[col].replace(TEAM_NAME_MAP)
    return df


# --- loaders ---------------------------------------------------------------------

def load_laps(year=None, session_name=None):
    """
    Loads silver_laps joined to silver_lap_flags, which classifies each lap by
    race-wide neutralisation (sc_flag / vsc_flag / red_flag) and separately by
    sector yellow. `caution_flag` is retained as an alias for `neutralised` for
    backward compatibility with existing notebooks, but now means "race was
    neutralised" rather than "any flag was logged on this lap number".

    Sector yellows are deliberately NOT neutralising: measured at 94.11s mean
    versus 89.19s clean, they cost ~5% and are localised to one sector.
    Safety Car laps average 128.59s (+44%), VSC 109.20s (+22%).
    """
    query = """
    SELECT l.*, s.year, s.session_name, m.meeting_name, d.team_name, d.full_name,
           f.sc_flag, f.vsc_flag, f.red_flag, f.yellow_sector_flag,
           f.neutralised,
           COALESCE(f.neutralised, 0) AS caution_flag
    FROM silver_laps l
    JOIN silver_sessions s ON l.session_key = s.session_key
    JOIN silver_meetings m ON s.meeting_key = m.meeting_key
    JOIN silver_drivers d ON l.session_key = d.session_key AND l.driver_number = d.driver_number
    LEFT JOIN silver_lap_flags f
           ON f.session_key   = l.session_key
          AND f.driver_number = l.driver_number
          AND f.lap_number    = l.lap_number
    WHERE l.lap_duration IS NOT NULL
    """
    params = []
    if year:
        query += " AND s.year = ?"
        params.append(year)
    if session_name:
        query += " AND s.session_name = ?"
        params.append(session_name)
    return normalize_team_names(_read_sql(query, params))


def load_session_results(year=None, session_name=None):
    """Loads silver_session_result using duration_race_seconds (NOT 'duration')."""
    query = """
    SELECT sr.*, s.year, s.session_name, m.meeting_name, d.team_name, d.full_name
    FROM silver_session_result sr
    JOIN silver_sessions s ON sr.session_key = s.session_key
    JOIN silver_meetings m ON s.meeting_key = m.meeting_key
    JOIN silver_drivers d ON sr.session_key = d.session_key AND sr.driver_number = d.driver_number
    WHERE 1=1
    """
    params = []
    if year:
        query += " AND s.year = ?"
        params.append(year)
    if session_name:
        query += " AND s.session_name = ?"
        params.append(session_name)
    return normalize_team_names(_read_sql(query, params))


def check_missing_sessions(year, session_name):
    """
    Flags real ingestion gaps: non-cancelled sessions with zero
    silver_session_result rows.

    Known gaps as of 2026-07-26: the 8 documented 2023 sessions, plus three
    previously undocumented Race gaps found by running this check across all
    seasons -- session_key 9507 (Miami 2024), 9928 (Hungary 2025), 9869
    (Sao Paulo 2025). All three have laps/pits/stints/positions intact; only
    results are missing, so a targeted re-ingestion of the results endpoint
    should recover them.
    """
    query = """
    SELECT m.meeting_name, s.session_name, s.is_cancelled, s.session_key,
           (SELECT COUNT(*) FROM silver_session_result sr
             WHERE sr.session_key = s.session_key) AS row_count
    FROM silver_sessions s
    JOIN silver_meetings m ON s.meeting_key = m.meeting_key
    WHERE s.year = ? AND s.session_name = ?
    """
    df = _read_sql(query, (year, session_name))
    gaps = df[(df['row_count'] == 0) & (df['is_cancelled'] == 0)]
    if not gaps.empty:
        print(f"WARNING: {len(gaps)} non-cancelled session(s) with zero silver_session_result rows:")
        print(gaps[['meeting_name', 'session_key']].to_string(index=False))
    return df


def load_pit_stops(year=None):
    """
    Loads silver_pit. Warns if stop_duration coverage looks sparse.

    Measured coverage as of 2026-07-26: 2023 0.0%, 2024 1.4% (124/8,559),
    2025 7.9% (705/8,946), 2026 3.3% (132/4,051). stop_duration is effectively
    unusable in every season -- prefer lane_duration, which is identical to
    pit_duration in all 20,745 rows where both are populated.
    """
    query = """
    SELECT p.*, s.year, s.session_name, m.meeting_name, d.team_name, d.full_name
    FROM silver_pit p
    JOIN silver_sessions s ON p.session_key = s.session_key
    JOIN silver_meetings m ON s.meeting_key = m.meeting_key
    JOIN silver_drivers d ON p.session_key = d.session_key AND p.driver_number = d.driver_number
    WHERE 1=1
    """
    params = []
    if year:
        query += " AND s.year = ?"
        params.append(year)
    df = _read_sql(query, params)
    coverage = df['stop_duration'].notna().mean() if len(df) else 0.0
    if coverage < 0.10:
        print(f"WARNING: only {coverage:.1%} of rows have stop_duration populated. "
              f"Use lane_duration instead.")
    return normalize_team_names(df)


# --- backward compatibility for existing notebooks -------------------------------
# Notebooks across DIAGNOSTIC ANALYTICS / DATA PROFILING do
# `from data_prep import ..., dbset`. Rather than editing validated notebooks,
# `dbset` is kept as a lazily-created READ-ONLY connection: nothing is opened
# unless something actually references it, so pipeline steps that import only the
# loader functions never hold a connection open.
#
# NOTE: `dbset` is now read-only. A notebook cell that wrote through it
# (INSERT / CREATE TABLE) will now raise
#     duckdb.InvalidInputException: Cannot execute statement of type "CREATE"
#     on database "silver_f1" which is attached in read-only mode
# That is intentional -- such a write should be explicit, via
# get_connection(read_only=False).
#
# IT IS ALSO A DUCKDB CONNECTION NOW, NOT A SQLITE ONE. A notebook cell doing
# `pd.read_sql(..., dbset)` still works, but goes row by row through the DB-API
# and throws away most of the reason for the migration. Prefer the loaders here,
# or read_sql(sql, dbset), which goes through Arrow instead.
#
# New code should use `with get_connection() as con:` instead.

_dbset = None


def __getattr__(name):
    """PEP 562 module-level attribute access — creates `dbset` on first use."""
    global _dbset
    if name == "dbset":
        if _dbset is None:
            _dbset = duckdb.connect(str(DB_PATH), read_only=True)
        return _dbset
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")