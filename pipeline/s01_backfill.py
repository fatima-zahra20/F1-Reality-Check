"""
s01_backfill.py — targeted re-ingestion of sessions whose original fetch failed
silently.

Background
----------
openf1_ingestion.py records a (endpoint, session_key) pair in _ingestion_progress
regardless of whether the fetch succeeded. fetch() returns [] on timeout, on HTTP
error, and on a genuinely empty response — all three are indistinguishable by the
time mark_fetched() is called. So a transient failure, or a fetch issued before a
session had even taken place, is recorded as permanently complete and skipped on
every subsequent run.

That is how 12 races ended up with missing laps and/or results, including three
2026 races fetched on 30 June before they were run.

What this script does
---------------------
1. Finds (endpoint, session_key) pairs where rows_inserted = 0 on a session that
   has actually taken place and was not cancelled, restricted to endpoints that
   should return data for that session type.
2. Deletes those _ingestion_progress rows and re-fetches.
3. Records a real outcome: 'ok', 'empty', or 'failed' — so a future run can tell
   "there is nothing there" from "we never got an answer".

Safe to run repeatedly. Defaults to a dry run.

Usage
-----
    python pipeline\\s01_backfill.py                  # dry run — shows the plan
    python pipeline\\s01_backfill.py --execute        # actually re-fetch
    python pipeline\\s01_backfill.py --execute --sessions 11326 11334 11342
    python pipeline\\s01_backfill.py --execute --include-optional

After running, rebuild the affected silver tables and re-run the gate.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH, BRONZE_DB_PATH  # noqa: E402

BASE_URL = "https://api.openf1.org/v1"
REQUEST_DELAY = 1.0          # seconds between calls, matching the original script
RETRIES = 3

# Endpoints that should return rows for essentially any session that ran.
CORE_ENDPOINTS = ["laps", "position", "weather", "race_control", "stints"]

# Endpoints tied to a particular kind of session.
RACE_ONLY = ["intervals", "overtakes"]          # Race and Sprint
QUALI_ONLY = ["starting_grid"]                  # Qualifying and Sprint Qualifying

# Legitimately empty in many sessions (a practice run with no stops, no radio
# clips captured). Retried only with --include-optional.
OPTIONAL_ENDPOINTS = ["pit", "team_radio"]

# session_result exists for competitive sessions but not for pre-season testing,
# where session_name is 'Day 1' / 'Day 2' / 'Day 3'.
RESULT_ENDPOINT = "session_result"

RACE_SESSION_NAMES = {"Race", "Sprint"}
QUALI_SESSION_NAMES = {"Qualifying", "Sprint Qualifying", "Sprint Shootout"}


# --- progress table -------------------------------------------------------------

def ensure_status_columns(con: sqlite3.Connection) -> None:
    """
    Adds status / http_status / note columns to _ingestion_progress if absent.

    This is the core fix: the original schema could not distinguish a successful
    empty response from a failed fetch, so failures were permanently cached as
    complete.
    """
    existing = {r[1] for r in con.execute("PRAGMA table_info(_ingestion_progress)")}
    for col, decl in [
        ("status", "TEXT"),          # 'ok' | 'empty' | 'failed'
        ("http_status", "INTEGER"),  # last HTTP status seen, NULL on timeout
        ("note", "TEXT"),
    ]:
        if col not in existing:
            con.execute(f"ALTER TABLE _ingestion_progress ADD COLUMN {col} {decl}")
    con.commit()

    ensure_progress_key(con)


def ensure_progress_key(con: sqlite3.Connection) -> None:
    """
    Restores the uniqueness of (endpoint, session_key).

    openf1_ingestion.py declares this table with PRIMARY KEY (endpoint,
    session_key), and both it and this script rely on INSERT OR REPLACE to
    update a row in place. The 2026-07-28 database split rebuilt the table in
    bronze without the key, so OR REPLACE has nothing to replace against and
    silently appends instead. Every re-fetch then leaves its old 'zero rows'
    row sitting beside the new one, and build_plan reads whichever it reaches
    first: it can queue a re-fetch of data already held, or read a stale 'ok'
    over a real failure.

    Deduplicates keeping the most recent row per pair, then adds the unique
    index so it cannot drift again.
    """
    has_index = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' "
        "AND name='ix_progress_pair'").fetchone()
    if has_index:
        return

    con.execute("""
        DELETE FROM _ingestion_progress
        WHERE rowid NOT IN (
            SELECT MAX(rowid) FROM _ingestion_progress
            GROUP BY endpoint, session_key
        )
    """)
    con.execute("CREATE UNIQUE INDEX ix_progress_pair "
                "ON _ingestion_progress (endpoint, session_key)")
    con.commit()


# --- HTTP ------------------------------------------------------------------------

def fetch(endpoint: str, params: dict):
    """
    Returns (rows, status, http_status).

    status is 'ok' when rows came back, 'empty' when the API answered 200 with
    nothing, and 'failed' when we never got a usable answer. The original script
    collapsed all three into [].
    """
    url = f"{BASE_URL}/{endpoint}"
    last_code = None

    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=60)
            last_code = resp.status_code
            resp.raise_for_status()
            data = resp.json()
            time.sleep(REQUEST_DELAY)
            return data, ("ok" if data else "empty"), last_code

        except requests.exceptions.Timeout:
            print(f"      timeout (attempt {attempt + 1}/{RETRIES})")
            time.sleep(2 ** attempt)

        except requests.exceptions.HTTPError as exc:
            code = exc.response.status_code
            last_code = code
            print(f"      HTTP {code} (attempt {attempt + 1}/{RETRIES})")
            # 404 is a definitive answer: this resource does not exist.
            if code == 404:
                return [], "empty", code
            # 422 is also definitive, and it does not mean "no data". OpenF1
            # answers "you're likely asking for too much data at once" when a
            # whole-session telemetry request would be too large. Retrying is
            # pointless; the request has to be split. Returned as its own
            # status so the caller can page instead of giving up.
            if code == 422:
                return [], "too_large", code
            time.sleep(2 ** attempt)

        except requests.exceptions.RequestException as exc:
            print(f"      request error: {exc}")
            time.sleep(2 ** attempt)

    return [], "failed", last_code


def session_drivers(con, session_key: int) -> list[int]:
    """Car numbers entered for a session, from silver."""
    rows = con.execute(
        "SELECT DISTINCT driver_number FROM silver.silver_drivers "
        "WHERE session_key = ? ORDER BY driver_number", (session_key,)
    ).fetchall()
    return [int(r[0]) for r in rows]


def fetch_session(endpoint: str, session_key: int, drivers: list[int]):
    """
    One session's rows for an endpoint, splitting the request if it is refused.

    Asks for the whole session first, because for most endpoints that is one
    call. If the API answers 422, the same data is collected one driver at a
    time and concatenated.

    This is the fix for the gap that hid car_data and location for every race.
    The old fetch treated 422 as "no data": it retried three times, gave up,
    and recorded zero rows. Sakhir and Jeddah were logged as having no position
    data across 45 sessions, and no race anywhere was recorded as having any
    car telemetry. Both were false. Paged per driver, Sakhir 2023 returns
    36,860 location rows per car.

    Returns (rows, status, http_status, calls).
    """
    rows, status, code = fetch(endpoint, {"session_key": session_key})
    if status != "too_large":
        return rows, status, code, 1

    if not drivers:
        return [], "failed", code, 1

    print(f"      422 on the whole session, paging {len(drivers)} drivers")
    collected, calls = [], 1
    for driver_number in drivers:
        part, part_status, part_code = fetch(
            endpoint, {"session_key": session_key,
                       "driver_number": driver_number})
        calls += 1
        if part_status == "failed":
            return collected, "failed", part_code, calls
        collected.extend(part)

    return collected, ("ok" if collected else "empty"), 200, calls


# --- writing ---------------------------------------------------------------------

def ensure_columns(con: sqlite3.Connection, table: str, cols) -> None:
    existing = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    for col in cols:
        if col not in existing:
            con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" TEXT')
    con.commit()


def insert_rows(con: sqlite3.Connection, table: str, rows) -> int:
    """
    Mirrors openf1_ingestion.insert_rows: everything stored as TEXT in bronze,
    typing happens in the silver build.

    A plain INSERT is safe here because every pair this script touches currently
    has zero rows. It would NOT be safe for a general refresh.
    """
    if not rows:
        return 0

    cols = list(rows[0].keys())
    exists = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()

    if not exists:
        col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
        con.execute(f'CREATE TABLE "{table}" ({col_defs})')
        con.commit()
    else:
        ensure_columns(con, table, cols)

    col_names = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)
    data = [
        tuple(str(row[c]) if row.get(c) is not None else None for c in cols)
        for row in rows
    ]
    con.executemany(
        f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders})', data
    )
    con.commit()
    return len(rows)


def clear_session(con, table: str, session_key: int) -> int:
    """
    Removes a session's existing rows before a re-fetch.

    insert_rows uses a plain INSERT, which is only safe when the pair holds
    nothing. Telemetry backfill does not have that guarantee: the 2026 Monaco
    race already has location rows and needs car_data added, and a re-run of
    this script would otherwise double every row it touched.
    """
    exists = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone()
    if not exists:
        return 0
    cur = con.execute(f'DELETE FROM "{table}" WHERE CAST(session_key AS INT) = ?',
                      (int(session_key),))
    con.commit()
    return cur.rowcount


def record(con, endpoint, session_key, n, status, http_status, note=None) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO _ingestion_progress
            (endpoint, session_key, rows_inserted, fetched_at, status, http_status, note)
        VALUES (?, ?, ?, datetime('now'), ?, ?, ?)
        """,
        (endpoint, str(session_key), n, status, http_status, note),
    )
    con.commit()


# --- planning --------------------------------------------------------------------

def expected_endpoints(session_name: str, include_optional: bool) -> list[str]:
    eps = list(CORE_ENDPOINTS)

    if session_name in RACE_SESSION_NAMES:
        eps += RACE_ONLY
    if session_name in QUALI_SESSION_NAMES:
        eps += QUALI_ONLY

    # Pre-season testing days produce no classification.
    if not session_name.startswith("Day "):
        eps.append(RESULT_ENDPOINT)

    if include_optional:
        eps += OPTIONAL_ENDPOINTS

    return eps


def build_plan(con, only_sessions=None, include_optional=False):
    """
    Returns a list of (session_key, session_name, meeting_name, endpoint) to retry.

    A pair qualifies when it has rows_inserted = 0 for a session that was not
    cancelled and whose start time is in the past — i.e. data should exist.
    """
    now = datetime.now(timezone.utc).isoformat()

    sessions = con.execute(
        """
        SELECT s.session_key, s.session_name, m.meeting_name, s.date_start
        FROM silver.silver_sessions s
        JOIN silver.silver_meetings m ON m.meeting_key = s.meeting_key
        WHERE s.is_cancelled = 0
          AND s.date_start < ?
        ORDER BY s.date_start
        """,
        (now,),
    ).fetchall()

    plan = []
    for session_key, session_name, meeting_name, _ in sessions:
        if only_sessions and session_key not in only_sessions:
            continue

        for endpoint in expected_endpoints(session_name, include_optional):
            row = con.execute(
                """
                SELECT rows_inserted, status FROM _ingestion_progress
                WHERE endpoint = ? AND session_key = ?
                """,
                (endpoint, str(session_key)),
            ).fetchone()

            # Retry only when no definitive answer was ever recorded: never
            # attempted, a legacy row with no status, or an explicit failure.
            # A confirmed 'empty' is a real answer and is not re-queried.
            if row is None:
                plan.append((session_key, session_name, meeting_name, endpoint))
            elif row[0] == 0 and (row[1] is None or row[1] == "failed"):
                plan.append((session_key, session_name, meeting_name, endpoint))

    return plan


# --- telemetry -------------------------------------------------------------------

TELEMETRY_ENDPOINTS = ["location", "car_data"]


def run_telemetry(con, sessions, execute: bool) -> int:
    """
    Pull car_data and location for named sessions, paging per driver.

    Kept separate from the ordinary backfill plan because the volume is a
    different order of magnitude. One race is about 740,000 location rows and
    900,000 car_data rows across twenty cars, so this is opt-in per session
    rather than something that sweeps the calendar.
    """
    if not sessions:
        print("[FAIL] --telemetry needs --sessions, e.g. --sessions 7953 7779")
        return 1

    rows = con.execute(
        f"""
        SELECT s.session_key, s.year, m.meeting_name, s.session_name,
               s.circuit_short_name
        FROM silver.silver_sessions s
        JOIN silver.silver_meetings m ON m.meeting_key = s.meeting_key
        WHERE s.session_key IN ({','.join('?' * len(sessions))})
        ORDER BY s.date_start
        """, sessions).fetchall()

    found = {r[0] for r in rows}
    missing = [s for s in sessions if s not in found]
    if missing:
        print(f"[FAIL] unknown session_key(s): {missing}")
        return 1

    print("=" * 74)
    print("TELEMETRY BACKFILL")
    print(f"bronze: {BRONZE_DB_PATH}")
    print("=" * 74)

    for session_key, year, meeting, name, circuit in rows:
        drivers = session_drivers(con, session_key)
        print(f"\n{year} {meeting} ({name}) at {circuit}"
              f"  session {session_key}, {len(drivers)} drivers")
        for endpoint in TELEMETRY_ENDPOINTS:
            have = 0
            if con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name=?", (endpoint,)).fetchone():
                have = con.execute(
                    f'SELECT COUNT(*) FROM "{endpoint}" '
                    f'WHERE CAST(session_key AS INT) = ?',
                    (session_key,)).fetchone()[0]
            print(f"  {endpoint:10s} currently {have:>9,} rows", end="")

            if not execute:
                print("   (dry run)")
                continue

            print()
            started = time.time()
            data, status, code, calls = fetch_session(endpoint, session_key,
                                                      drivers)
            if status == "failed":
                print(f"      FAILED after {calls} calls, HTTP {code}")
                record(con, endpoint, session_key, 0, "failed", code,
                       "telemetry paging failed")
                continue

            removed = clear_session(con, endpoint, session_key)
            n = insert_rows(con, endpoint, data)
            record(con, endpoint, session_key, n, status, code,
                   f"paged over {len(drivers)} drivers")
            print(f"      {n:,} rows in {calls} calls, "
                  f"{time.time() - started:.0f}s"
                  + (f", replaced {removed:,}" if removed else ""))

    if not execute:
        print("\nDRY RUN. Re-run with --execute to fetch.")
    print("\n" + "=" * 74)
    print("Then rebuild: s02_build_silver, s04, s05b, s05c, s06.")
    print("=" * 74)
    return 0


# --- main ------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill silently-failed OpenF1 fetches.")
    ap.add_argument("--execute", action="store_true",
                    help="actually re-fetch; without this, prints the plan only")
    ap.add_argument("--sessions", nargs="*", type=int, default=None,
                    help="restrict to specific session_keys")
    ap.add_argument("--include-optional", action="store_true",
                    help="also retry pit and team_radio, which are often legitimately empty")
    ap.add_argument("--telemetry", action="store_true",
                    help="fetch car_data and location for --sessions, paging "
                         "per driver. These are large: roughly 37k location "
                         "and 45k car_data rows per driver per race.")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[FAIL] database not found at {DB_PATH}")
        return 1

    if not BRONZE_DB_PATH.exists():
        print(f"[FAIL] bronze database not found at {BRONZE_DB_PATH}")
        return 1

    # Writes go to bronze; planning reads silver_sessions from the silver file,
    # attached read-only.
    con = sqlite3.connect(str(BRONZE_DB_PATH))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"ATTACH DATABASE '{DB_PATH.as_posix()}' AS silver")
    ensure_status_columns(con)

    if args.telemetry:
        return run_telemetry(con, args.sessions, args.execute)

    plan = build_plan(con, args.sessions, args.include_optional)

    if not plan:
        print("Nothing to backfill.")
        con.close()
        return 0

    # --- summary ---------------------------------------------------------------
    print("=" * 74)
    print(f"BACKFILL PLAN — {len(plan)} (session, endpoint) pairs")
    print("=" * 74)

    by_endpoint: dict[str, int] = {}
    for _, _, _, ep in plan:
        by_endpoint[ep] = by_endpoint.get(ep, 0) + 1
    for ep, n in sorted(by_endpoint.items(), key=lambda kv: -kv[1]):
        print(f"  {ep:20s} {n:>4}")

    est_min = len(plan) * REQUEST_DELAY / 60
    print(f"\nEstimated time at {REQUEST_DELAY}s/request: ~{est_min:.0f} min")

    if not args.execute:
        print("\nAffected sessions:")
        seen = set()
        for sk, sname, mname, _ in plan:
            if sk not in seen:
                seen.add(sk)
                eps = [e for k, _, _, e in plan if k == sk]
                print(f"  {sk}  {mname} / {sname}  ->  {', '.join(eps)}")
        print("\nDRY RUN — nothing fetched. Re-run with --execute to apply.")
        con.close()
        return 0

    # --- execute ---------------------------------------------------------------
    print("\n" + "=" * 74)
    print("EXECUTING")
    print("=" * 74)

    counts = {"ok": 0, "empty": 0, "failed": 0}
    total_rows = 0

    for i, (session_key, session_name, meeting_name, endpoint) in enumerate(plan, 1):
        print(f"[{i}/{len(plan)}] {meeting_name} / {session_name} "
              f"(sk={session_key}) -> {endpoint}")

        rows, status, http_status = fetch(endpoint, {"session_key": session_key})

        n = 0
        if status == "ok":
            n = insert_rows(con, endpoint, rows)
            total_rows += n
            print(f"      inserted {n:,} rows")
        elif status == "empty":
            print("      empty response (API has no data for this pair)")
        else:
            print("      FAILED — left unmarked so a later run retries it")

        counts[status] += 1

        # Only record a terminal outcome for ok/empty. A failure is deliberately
        # left as-is so the next run picks it up again — this is the bug fix.
        if status in ("ok", "empty"):
            record(con, endpoint, session_key, n, status, http_status)

    con.close()

    print("\n" + "=" * 74)
    print(f"ok: {counts['ok']}  |  empty: {counts['empty']}  |  failed: {counts['failed']}")
    print(f"rows inserted: {total_rows:,}")
    print("=" * 74)
    print("\nNext: rebuild the affected silver tables, then run pipeline\\s03_verify.py")

    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())