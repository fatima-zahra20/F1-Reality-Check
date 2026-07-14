"""
data_prep.py — Shared data loading and cleaning utilities for the F1 Reality Check 
diagnostic analysis phase. Import the functions needed rather than re-writing 
this logic in each notebook.
"""

import pandas as pd
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "DATA INGESTION" / "f1.db"
dbset = sqlite3.connect(str(DB_PATH))

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


def load_laps(year=None, session_name=None):
    """
    Loads silver_laps with a caution_flag column: 1 if a SafetyCar/Red/Yellow 
    message was logged on that exact session_key + lap_number, else 0.
    Filter on caution_flag == 0 before any pace regression.
    """
    query = """
    WITH caution_laps AS (
        SELECT session_key, lap_number,
               MAX(CASE WHEN category = 'SafetyCar' THEN 1 
                        WHEN category = 'Flag' AND flag IN ('RED','YELLOW','DOUBLE YELLOW') THEN 1 
                        ELSE 0 END) AS caution_flag
        FROM silver_race_control
        WHERE lap_number IS NOT NULL
        GROUP BY session_key, lap_number
    )
    SELECT l.*, s.year, s.session_name, m.meeting_name, d.team_name, d.full_name,
           COALESCE(c.caution_flag, 0) AS caution_flag
    FROM silver_laps l
    JOIN silver_sessions s ON l.session_key = s.session_key
    JOIN silver_meetings m ON s.meeting_key = m.meeting_key
    JOIN silver_drivers d ON l.session_key = d.session_key AND l.driver_number = d.driver_number
    LEFT JOIN caution_laps c ON l.session_key = c.session_key AND l.lap_number = c.lap_number
    WHERE l.lap_duration IS NOT NULL
    """
    params = []
    if year:
        query += " AND s.year = ?"
        params.append(year)
    if session_name:
        query += " AND s.session_name = ?"
        params.append(session_name)
    df = pd.read_sql(query, dbset, params=params)
    return normalize_team_names(df)


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
    df = pd.read_sql(query, dbset, params=params)
    return normalize_team_names(df)


def check_missing_sessions(year, session_name):
    """Flags real ingestion gaps: non-cancelled sessions with zero silver_session_result rows."""
    query = """
    SELECT m.meeting_name, s.session_name, s.is_cancelled,
           (SELECT COUNT(*) FROM silver_session_result sr WHERE sr.session_key = s.session_key) AS row_count
    FROM silver_sessions s
    JOIN silver_meetings m ON s.meeting_key = m.meeting_key
    WHERE s.year = ? AND s.session_name = ?
    """
    df = pd.read_sql(query, dbset, params=(year, session_name))
    gaps = df[(df['row_count'] == 0) & (df['is_cancelled'] == 0)]
    if not gaps.empty:
        print(f"WARNING: {len(gaps)} non-cancelled session(s) with zero silver_session_result rows:")
        print(gaps[['meeting_name']].to_string(index=False))
    return df


def load_pit_stops(year=None):
    """Loads silver_pit. Warns if stop_duration coverage looks sparse for the requested scope."""
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
    df = pd.read_sql(query, dbset, params=params)
    coverage = df['stop_duration'].notna().mean()
    if coverage < 0.10:
        print(f"WARNING: only {coverage:.1%} of rows have stop_duration populated. "
              f"Consider using lane_duration instead, or scoping to 2024+.")
    return normalize_team_names(df)
