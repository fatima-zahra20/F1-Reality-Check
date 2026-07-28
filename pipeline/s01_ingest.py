"""
s01_ingest.py — the scheduled ingestion step.

Two jobs, in order:

1. REFRESH GLOBAL TABLES (meetings, sessions, drivers).
   This is what lets the pipeline see that a new race weekend exists.
   openf1_ingestion.py could not do this: ingest_global() marked these fetched
   under the sentinel key '__global__' exactly once, then on every later run it
   still downloaded them but never inserted. So the calendar froze at whatever
   was present on the first run, and nothing downstream could discover new
   sessions.

   These tables are small (100 / 490 / ~10k rows) and bronze has no primary
   keys, so a plain INSERT would duplicate everything. They are therefore
   truncate-and-reload, guarded by a sanity check: if the API returns fewer rows
   than the database already holds, the swap is refused rather than destroying
   good data on a partial response.

2. FETCH PER-SESSION DATA for anything missing.
   Reuses the planning logic from s01_backfill. New sessions have no rows in
   _ingestion_progress at all, so they are picked up automatically. Newly
   discovered sessions get every endpoint including the optional ones (pit,
   team_radio); previously-seen sessions only get the core retry set, so a
   legitimately empty practice session is not re-queried every week.

Telemetry (car_data, location) is never fetched here — ~35M rows covering only
32 of 490 sessions, unusable as model features. Fetch it manually on demand.

Usage
-----
    python pipeline\\s01_ingest.py                 # dry run — shows the plan
    python pipeline\\s01_ingest.py --execute
    python pipeline\\s01_ingest.py --execute --globals-only
    python pipeline\\s01_ingest.py --execute --skip-globals
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH, BRONZE_DB_PATH  # noqa: E402
from s01_backfill import (  # noqa: E402
    ensure_status_columns,
    expected_endpoints,
    fetch,
    insert_rows,
    record,
)

GLOBAL_ENDPOINTS = ["meetings", "sessions", "drivers"]


# --- global tables ---------------------------------------------------------------

def refresh_global(con: sqlite3.Connection, endpoint: str, execute: bool) -> tuple[int, int]:
    """
    Truncate-and-reload one global table. Returns (rows_before, rows_after).

    Refuses the swap if the API returns materially fewer rows than are already
    stored — that pattern means a partial or failed response, and replacing good
    data with it would be destructive.
    """
    try:
        before = con.execute(f'SELECT COUNT(*) FROM "{endpoint}"').fetchone()[0]
    except sqlite3.OperationalError:
        before = 0

    print(f"\n  /{endpoint}")
    print(f"    currently {before:,} rows")

    rows, status, http_status = fetch(endpoint, None)

    if status == "failed":
        print(f"    FAILED (HTTP {http_status}) — keeping existing data")
        return before, before

    print(f"    API returned {len(rows):,} rows")

    if not rows:
        print("    empty response — keeping existing data")
        return before, before

    # Guard against destroying good data on a partial response.
    if before and len(rows) < before * 0.95:
        print(f"    REFUSED: {len(rows):,} < 95% of {before:,} — looks partial, not replacing")
        return before, before

    if not execute:
        print(f"    would replace ({len(rows) - before:+,})")
        return before, len(rows)

    con.execute("BEGIN")
    try:
        con.execute(f'DELETE FROM "{endpoint}"')
        n = insert_rows(con, endpoint, rows)
        con.commit()
    except Exception as exc:
        con.rollback()
        print(f"    FAILED: {type(exc).__name__}: {exc} — rolled back")
        return before, before

    print(f"    replaced: {before:,} -> {n:,}  ({n - before:+,})")
    return before, n


# --- per-session planning --------------------------------------------------------

def build_session_plan(con: sqlite3.Connection):
    """
    Returns (new_pairs, retry_pairs).

    new_pairs   — sessions never seen before: every endpoint, including optional
    retry_pairs — previously-seen sessions with a zero-row core endpoint
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

    seen = {
        row[0] for row in con.execute(
            "SELECT DISTINCT session_key FROM _ingestion_progress "
            "WHERE session_key != '__global__'"
        )
    }

    new_pairs, retry_pairs = [], []

    for session_key, session_name, meeting_name, _ in sessions:
        is_new = str(session_key) not in seen

        for endpoint in expected_endpoints(session_name, include_optional=is_new):
            if is_new:
                new_pairs.append((session_key, session_name, meeting_name, endpoint))
                continue

            row = con.execute(
                "SELECT rows_inserted, status FROM _ingestion_progress "
                "WHERE endpoint = ? AND session_key = ?",
                (endpoint, str(session_key)),
            ).fetchone()

            # Retry only when we never got a definitive answer:
            #   - no progress row at all
            #   - zero rows with no status (legacy rows from openf1_ingestion.py,
            #     which could not distinguish empty from failed)
            #   - zero rows explicitly marked failed
            # A confirmed 'empty' is a real answer and is not re-queried.
            if row is None:
                retry_pairs.append((session_key, session_name, meeting_name, endpoint))
            elif row[0] == 0 and (row[1] is None or row[1] == "failed"):
                retry_pairs.append((session_key, session_name, meeting_name, endpoint))

    return new_pairs, retry_pairs


def run_pairs(con: sqlite3.Connection, pairs, label: str) -> dict:
    counts = {"ok": 0, "empty": 0, "failed": 0, "rows": 0}

    for i, (session_key, session_name, meeting_name, endpoint) in enumerate(pairs, 1):
        print(f"  [{i}/{len(pairs)}] {meeting_name} / {session_name} "
              f"(sk={session_key}) -> {endpoint}")

        rows, status, http_status = fetch(endpoint, {"session_key": session_key})

        n = 0
        if status == "ok":
            n = insert_rows(con, endpoint, rows)
            counts["rows"] += n
            print(f"        {n:,} rows")
        elif status == "empty":
            print("        empty")
        else:
            print("        FAILED — left unmarked for the next run")

        counts[status] += 1

        # Never mark a failure terminal. This is the fix for the bug that froze
        # transient failures as permanent gaps.
        if status in ("ok", "empty"):
            record(con, endpoint, session_key, n, status, http_status)

    return counts


# --- main ------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest new OpenF1 data.")
    ap.add_argument("--execute", action="store_true", help="apply; otherwise dry run")
    ap.add_argument("--globals-only", action="store_true",
                    help="refresh meetings/sessions/drivers and stop")
    ap.add_argument("--skip-globals", action="store_true",
                    help="skip the global refresh, go straight to per-session")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[FAIL] database not found at {DB_PATH}")
        return 1

    if not BRONZE_DB_PATH.exists():
        print(f"[FAIL] bronze database not found at {BRONZE_DB_PATH}")
        return 1

    con = sqlite3.connect(str(BRONZE_DB_PATH))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"ATTACH DATABASE '{DB_PATH.as_posix()}' AS silver")
    ensure_status_columns(con)

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print("=" * 74)
    print(f"INGEST — {mode}")
    print(f"bronze: {BRONZE_DB_PATH}")
    print(f"silver: {DB_PATH}")
    print("=" * 74)

    # --- 1. globals ------------------------------------------------------------
    globals_changed = False
    if not args.skip_globals:
        print("\nRefreshing global tables (this is how new sessions are discovered)")
        for endpoint in GLOBAL_ENDPOINTS:
            before, after = refresh_global(con, endpoint, args.execute)
            if after != before:
                globals_changed = True

        if globals_changed and args.execute:
            print("\n  NOTE: bronze sessions/meetings changed. silver_sessions and")
            print("        silver_meetings must be rebuilt before the per-session")
            print("        plan can see new sessions. Run:")
            print("          python pipeline\\s02_build_silver.py --tables meetings sessions drivers")
            print("        then re-run this script with --skip-globals.")

    if args.globals_only:
        con.close()
        return 0

    # --- 2. per-session --------------------------------------------------------
    print("\n" + "=" * 74)
    print("PER-SESSION PLAN")
    print("=" * 74)

    new_pairs, retry_pairs = build_session_plan(con)

    new_sessions = sorted({p[0] for p in new_pairs})
    print(f"\n  newly discovered sessions: {len(new_sessions)}")
    for sk in new_sessions[:20]:
        meeting = next(p[2] for p in new_pairs if p[0] == sk)
        sname = next(p[1] for p in new_pairs if p[0] == sk)
        print(f"    {sk}  {meeting} / {sname}")
    if len(new_sessions) > 20:
        print(f"    ... and {len(new_sessions) - 20} more")

    print(f"\n  pairs to fetch for new sessions: {len(new_pairs)}")
    print(f"  pairs to retry on known sessions: {len(retry_pairs)}")

    total = len(new_pairs) + len(retry_pairs)
    if not total:
        print("\nNothing to fetch — database is current.")
        con.close()
        return 0

    print(f"  estimated time: ~{total / 60:.0f} min")

    if not args.execute:
        print("\nDRY RUN — nothing fetched. Re-run with --execute.")
        con.close()
        return 0

    summary = {"ok": 0, "empty": 0, "failed": 0, "rows": 0}

    if new_pairs:
        print("\n--- new sessions ---")
        for k, v in run_pairs(con, new_pairs, "new").items():
            summary[k] += v

    if retry_pairs:
        print("\n--- retries ---")
        for k, v in run_pairs(con, retry_pairs, "retry").items():
            summary[k] += v

    con.close()

    print("\n" + "=" * 74)
    print(f"ok: {summary['ok']}  |  empty: {summary['empty']}  |  failed: {summary['failed']}")
    print(f"rows inserted: {summary['rows']:,}")
    print("=" * 74)

    if summary["rows"]:
        print("\nNext:")
        print("  python pipeline\\s02_build_silver.py")
        print("  python pipeline\\s02b_caution_flags.py")
        print("  python pipeline\\s03_verify.py")

    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())