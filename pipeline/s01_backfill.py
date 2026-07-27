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
from config import DB_PATH  # noqa: E402

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
            time.sleep(2 ** attempt)

        except requests.exceptions.RequestException as exc:
            print(f"      request error: {exc}")
            time.sleep(2 ** attempt)

    return [], "failed", last_code


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
        FROM silver_sessions s
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
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
                SELECT rows_inserted FROM _ingestion_progress
                WHERE endpoint = ? AND session_key = ?
                """,
                (endpoint, str(session_key)),
            ).fetchone()

            # Retry when previously recorded as zero rows, or never attempted.
            if row is None or row[0] == 0:
                plan.append((session_key, session_name, meeting_name, endpoint))

    return plan


# --- main ------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill silently-failed OpenF1 fetches.")
    ap.add_argument("--execute", action="store_true",
                    help="actually re-fetch; without this, prints the plan only")
    ap.add_argument("--sessions", nargs="*", type=int, default=None,
                    help="restrict to specific session_keys")
    ap.add_argument("--include-optional", action="store_true",
                    help="also retry pit and team_radio, which are often legitimately empty")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[FAIL] database not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA journal_mode=WAL")
    ensure_status_columns(con)

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