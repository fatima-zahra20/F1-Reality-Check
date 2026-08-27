"""
Measurement two: the s05c position scan, SQLite vs DuckDB.

Builds the real query s05c issues, by calling s05c's own planning functions so
the pairs and sessions are exactly what the pipeline would ask for, then runs
that identical SQL against both stores and compares time AND content.

Content matters more than time. If the two disagree by a single row the speed
is irrelevant, so both results are hashed and the hashes must match.

Reads only. Nothing is written and no pipeline file is modified.
"""
import hashlib
import sqlite3
import sys
import time

import duckdb
import pandas as pd

ROOT = r"c:\Users\Fatima zahra\Projects\F1-Reality-Check"
sys.path.insert(0, f"{ROOT}\\pipeline")
import s05c_racemap as s05c  # noqa: E402

silver = sqlite3.connect(f"file:{ROOT}\\DATA INGESTION\\f1.db?mode=ro", uri=True)
lite = sqlite3.connect(f"file:{ROOT}\\DATA INGESTION\\bronze_f1.db?mode=ro", uri=True)
duck = duckdb.connect(f"{ROOT}\\DATA INGESTION\\bronze_f1.duckdb", read_only=True)

# Exactly what s05c's main() works out before it scans.
races = pd.read_sql("""
    SELECT DISTINCT circuit_key, circuit_short_name FROM silver_sessions
    WHERE session_name = 'Race' AND is_cancelled = 0
""", silver)
loc_sessions = s05c.load_location_sessions(lite, silver)
candidates = s05c.pick_trace_candidates(silver, loc_sessions, races)
pairs = sorted(set(zip(candidates.session_key, candidates.driver_number)))
gp = pd.read_sql("""
    SELECT session_key FROM silver_sessions
    WHERE session_name = 'Race' AND is_cancelled = 0
""", silver).session_key
measured = sorted(set(gp) & set(loc_sessions.session_key))
print(f"pairs: {len(pairs)}, whole sessions: {len(measured)}")

clauses = []
if pairs:
    clauses.append("(" + " OR ".join(
        f"(session_key = '{s}' AND driver_number = '{d}')" for s, d in pairs) + ")")
if measured:
    clauses.append("session_key IN (" + ", ".join(f"'{k}'" for k in measured) + ")")
SQL = f"""
    SELECT session_key, driver_number, date, x, y, z
    FROM location
    WHERE ({' OR '.join(clauses)})
      AND NOT (x = '0' AND y = '0')
"""


def digest(df: pd.DataFrame) -> str:
    d = df.sort_values(["session_key", "driver_number", "date"],
                       kind="mergesort").reset_index(drop=True)
    return hashlib.sha256(
        pd.util.hash_pandas_object(d, index=False).values.tobytes()).hexdigest()[:16]


print("\nscanning SQLite ...")
t0 = time.perf_counter()
a = pd.read_sql(SQL, lite)
ta = time.perf_counter() - t0
print(f"  {len(a):,} rows in {ta:.1f}s")

print("scanning DuckDB ...")
t0 = time.perf_counter()
b = duck.execute(SQL).df()
tb = time.perf_counter() - t0
print(f"  {len(b):,} rows in {tb:.1f}s")

print(f"\nspeedup: {ta / tb:.1f}x   ({ta:.1f}s -> {tb:.1f}s)")
print(f"rows equal: {len(a) == len(b)}")
ha, hb = digest(a), digest(b)
print(f"content hash sqlite : {ha}")
print(f"content hash duckdb : {hb}")
print("IDENTICAL" if ha == hb else "*** DIFFERENT, do not proceed ***")
