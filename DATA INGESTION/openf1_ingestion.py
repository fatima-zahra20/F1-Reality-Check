#!/usr/bin/env python3
"""
OpenF1 API -> f1.db ingestion script.
Fetches all 18 endpoints and stores them in a SQLite database.
Resumable: already-fetched (session, endpoint) pairs are skipped on re-run.
"""

import sqlite3
import requests
import time
import logging
import sys
from pathlib import Path

BASE_URL = "https://api.openf1.org/v1"
DB_PATH = Path(__file__).parent / "f1.db"
REQUEST_DELAY = 1.0  # seconds between API calls

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# Endpoints that require iterating per session_key (too large to fetch globally)
PER_SESSION_ENDPOINTS = [
    "laps",
    "pit",
    "position",
    "race_control",
    "stints",
    "team_radio",
    "weather",
    "intervals",
    "overtakes",
    "session_result",
    "starting_grid",
]

# High-frequency telemetry (~3.7 Hz per driver) — fetched per session last
TELEMETRY_ENDPOINTS = [
    "car_data",
    "location",
]

# Endpoints that return all data without needing session filtering
GLOBAL_ENDPOINTS = [
    "meetings",
    "sessions",
    "drivers",
    "championship_drivers",
    "championship_teams",
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch(endpoint, params=None, retries=3):
    url = f"{BASE_URL}/{endpoint}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            time.sleep(REQUEST_DELAY)
            return resp.json()
        except requests.exceptions.Timeout:
            log.warning("Timeout on %s (attempt %d/%d)", url, attempt + 1, retries)
            time.sleep(2 ** attempt)
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code
            log.warning("HTTP %s on %s", code, url)
            if code in (400, 404):
                return []
            time.sleep(2 ** attempt)
        except requests.exceptions.RequestException as e:
            log.warning("Request error on %s: %s", url, e)
            time.sleep(2 ** attempt)
    log.error("Giving up on %s after %d attempts", url, retries)
    return []


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def init_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")  # 64 MB cache
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _ingestion_progress (
            endpoint TEXT NOT NULL,
            session_key TEXT NOT NULL,
            rows_inserted INTEGER,
            fetched_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (endpoint, session_key)
        )
    """)
    conn.commit()
    return conn


def already_fetched(conn, endpoint, session_key):
    cur = conn.execute(
        "SELECT 1 FROM _ingestion_progress WHERE endpoint=? AND session_key=?",
        (endpoint, str(session_key)),
    )
    return cur.fetchone() is not None


def mark_fetched(conn, endpoint, session_key, rows_inserted):
    conn.execute(
        """INSERT OR REPLACE INTO _ingestion_progress
           (endpoint, session_key, rows_inserted) VALUES (?, ?, ?)""",
        (endpoint, str(session_key), rows_inserted),
    )
    conn.commit()


def ensure_columns(conn, table, col_names):
    existing = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    for col in col_names:
        if col not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" TEXT')
    conn.commit()


def insert_rows(conn, table, rows):
    if not rows:
        return 0
    cols = list(rows[0].keys())
    # Create table on first insert
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone():
        col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({col_defs})')
        conn.commit()
    else:
        ensure_columns(conn, table, cols)

    col_names = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)
    sql = f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders})'
    data = [
        tuple(str(row[c]) if row.get(c) is not None else None for c in cols)
        for row in rows
    ]
    conn.executemany(sql, data)
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Ingestion logic
# ---------------------------------------------------------------------------

def ingest_global(conn, endpoint):
    if already_fetched(conn, endpoint, "__global__"):
        log.info("SKIP /%s (already ingested)", endpoint)
        return fetch(endpoint)  # still need sessions/meetings for later steps

    log.info("Fetching /%s ...", endpoint)
    rows = fetch(endpoint)
    n = insert_rows(conn, endpoint, rows)
    mark_fetched(conn, endpoint, "__global__", n)
    log.info("  -> %d rows into '%s'", n, endpoint)
    return rows


def ingest_per_session(conn, endpoint, session_key, label=""):
    if already_fetched(conn, endpoint, session_key):
        return
    rows = fetch(endpoint, {"session_key": session_key})
    n = insert_rows(conn, endpoint, rows)
    mark_fetched(conn, endpoint, session_key, n)
    if n:
        log.info("    %s%s: %d rows", label, endpoint, n)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("Database: %s", DB_PATH)
    conn = init_db(DB_PATH)

    # 1. Global endpoints
    log.info("=== Global endpoints ===")
    sessions_data = []
    for ep in GLOBAL_ENDPOINTS:
        result = ingest_global(conn, ep)
        if ep == "sessions":
            sessions_data = result

    # If sessions were already ingested, read them from DB
    if not sessions_data:
        cur = conn.execute("SELECT session_key FROM sessions")
        sessions_data = [{"session_key": row[0]} for row in cur.fetchall()]

    session_keys = sorted(
        set(str(s["session_key"]) for s in sessions_data if s.get("session_key")),
        key=lambda x: int(x) if x.isdigit() else 0,
    )
    log.info("Found %d sessions", len(session_keys))

    # 2. Per-session endpoints
    log.info("=== Per-session endpoints (%d sessions) ===", len(session_keys))
    for i, sk in enumerate(session_keys, 1):
        label = f"[{i}/{len(session_keys)}] session={sk}  "
        needs_work = any(
            not already_fetched(conn, ep, sk) for ep in PER_SESSION_ENDPOINTS
        )
        if needs_work:
            log.info("[%d/%d] Session %s", i, len(session_keys), sk)
        for ep in PER_SESSION_ENDPOINTS:
            ingest_per_session(conn, ep, sk, label="  ")

    # 3. High-frequency telemetry (largest datasets — done last)
    log.info("=== Telemetry endpoints (car_data & location) ===")
    log.info("Note: these are sampled at ~3.7 Hz per driver — expect large volume.")
    for i, sk in enumerate(session_keys, 1):
        needs_work = any(
            not already_fetched(conn, ep, sk) for ep in TELEMETRY_ENDPOINTS
        )
        if needs_work:
            log.info("[%d/%d] Telemetry session %s", i, len(session_keys), sk)
        for ep in TELEMETRY_ENDPOINTS:
            ingest_per_session(conn, ep, sk, label="  ")

    # Summary
    log.info("=== Ingestion complete ===")
    for table in (
        GLOBAL_ENDPOINTS + PER_SESSION_ENDPOINTS + TELEMETRY_ENDPOINTS
    ):
        try:
            cur = conn.execute(f'SELECT COUNT(*) FROM "{table}"')
            count = cur.fetchone()[0]
            log.info("  %-25s %d rows", table, count)
        except sqlite3.OperationalError:
            log.info("  %-25s (no data)", table)

    conn.close()
    log.info("Saved to %s", DB_PATH)


if __name__ == "__main__":
    main()
