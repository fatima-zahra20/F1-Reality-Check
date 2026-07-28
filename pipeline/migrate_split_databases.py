"""
migrate_split_databases.py — one-time migration from a single f1.db into a
medallion layout.

Before
------
    f1.db  (6.4 GB)
        18 bronze tables (raw API responses, all TEXT)
        18 silver tables (typed, PK-enforced)
        _ingestion_progress

After
-----
    bronze_f1.db  (~5 GB)   all raw tables + _ingestion_progress
    f1.db         (~300 MB) 16 silver tables, no telemetry
    gold_f1.db                created later, holds dashboard aggregates

Why
---
Bronze is raw material: it is only read by the silver build, and it is fully
re-fetchable from the API. Keeping it in the working database means every
backup, every VACUUM, and every full-table scan pays for 5 GB that analysis
never touches. Separating it also makes the medallion layers explicit rather
than implied by a table-name prefix.

Silver telemetry (silver_car_data, silver_location — 35M rows) is dropped
outright. It covers 32 of 490 sessions, so it cannot be a model feature or
appear in any season-wide aggregate. If it is ever needed it can be rebuilt from
bronze.

Safety
------
Copies and verifies row counts BEFORE dropping anything. Refuses to drop if any
table fails verification. Your existing f1.db.backup-* is an additional
fallback.

Usage
-----
    python pipeline\\migrate_split_databases.py            # dry run
    python pipeline\\migrate_split_databases.py --execute
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH  # noqa: E402

BRONZE_DB = DB_PATH.parent / "bronze_f1.db"

# Silver tables to discard rather than keep: unusable as features, rebuildable.
DROP_SILVER = ["silver_car_data", "silver_location"]


def human(nbytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:,.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:,.1f} TB"


def list_tables(con: sqlite3.Connection) -> list[str]:
    return [
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def count(con: sqlite3.Connection, table: str, schema: str = "main") -> int:
    return con.execute(f'SELECT COUNT(*) FROM {schema}."{table}"').fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="Split f1.db into bronze and silver.")
    ap.add_argument("--execute", action="store_true", help="apply; otherwise dry run")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[FAIL] database not found at {DB_PATH}")
        return 1

    if BRONZE_DB.exists():
        print(f"[FAIL] {BRONZE_DB.name} already exists — migration appears to have run.")
        print("       Delete it first if you intend to redo the migration.")
        return 1

    size_before = DB_PATH.stat().st_size
    con = sqlite3.connect(str(DB_PATH))

    all_tables = list_tables(con)
    bronze_tables = [t for t in all_tables if not t.startswith("silver_")]
    silver_tables = [t for t in all_tables if t.startswith("silver_")]
    keep_silver = [t for t in silver_tables if t not in DROP_SILVER]
    drop_silver = [t for t in silver_tables if t in DROP_SILVER]

    print("=" * 74)
    print("MIGRATION: SPLIT INTO BRONZE + SILVER")
    print(f"source: {DB_PATH}  ({human(size_before)})")
    print(f"bronze: {BRONZE_DB}")
    print("=" * 74)

    print(f"\nMove to bronze_f1.db ({len(bronze_tables)} tables):")
    bronze_rows = 0
    for t in bronze_tables:
        n = count(con, t)
        bronze_rows += n
        print(f"  {t:26s} {n:>12,}")
    print(f"  {'TOTAL':26s} {bronze_rows:>12,}")

    print(f"\nKeep in f1.db ({len(keep_silver)} silver tables):")
    for t in keep_silver:
        print(f"  {t:26s} {count(con, t):>12,}")

    print(f"\nDrop entirely ({len(drop_silver)} tables — rebuildable from bronze):")
    for t in drop_silver:
        print(f"  {t:26s} {count(con, t):>12,}")

    free = shutil.disk_usage(DB_PATH.parent).free
    print(f"\nfree disk: {human(free)}")
    if free < size_before * 1.2:
        print("[FAIL] need roughly 1.2x the current file size free (copy + VACUUM).")
        con.close()
        return 1

    if not args.execute:
        print("\nDRY RUN — nothing changed. Re-run with --execute.")
        con.close()
        return 0

    # --- 1. copy bronze out ----------------------------------------------------
    print("\n" + "=" * 74)
    print("STEP 1 — copying bronze tables")
    print("=" * 74)

    con.execute(f"ATTACH DATABASE '{BRONZE_DB.as_posix()}' AS bronze")

    for t in bronze_tables:
        started = time.time()
        con.execute(f'CREATE TABLE bronze."{t}" AS SELECT * FROM main."{t}"')
        con.commit()
        n = count(con, t, "bronze")
        print(f"  {t:26s} {n:>12,}  {time.time() - started:.1f}s")

    # --- 2. verify before destroying anything ----------------------------------
    print("\n" + "=" * 74)
    print("STEP 2 — verifying the copy")
    print("=" * 74)

    mismatches = []
    for t in bronze_tables:
        src, dst = count(con, t, "main"), count(con, t, "bronze")
        if src != dst:
            mismatches.append((t, src, dst))
            print(f"  [FAIL] {t}: {src:,} -> {dst:,}")

    if mismatches:
        print("\nRow counts do not match. Nothing dropped; f1.db is unchanged.")
        print(f"Delete {BRONZE_DB.name} and investigate before retrying.")
        con.close()
        return 1

    print(f"  all {len(bronze_tables)} tables verified")

    # --- 3. drop from f1.db ----------------------------------------------------
    print("\n" + "=" * 74)
    print("STEP 3 — dropping from f1.db")
    print("=" * 74)

    con.execute("DETACH DATABASE bronze")

    for t in bronze_tables + drop_silver:
        started = time.time()
        con.execute(f'DROP TABLE main."{t}"')
        con.commit()
        print(f"  dropped {t}  ({time.time() - started:.1f}s)")

    # --- 4. reclaim space ------------------------------------------------------
    print("\n" + "=" * 74)
    print("STEP 4 — VACUUM (rewrites the file; slow)")
    print("=" * 74)

    started = time.time()
    con.execute("VACUUM")
    con.commit()
    print(f"  done in {time.time() - started:.0f}s")

    remaining = list_tables(con)
    con.close()

    size_after = DB_PATH.stat().st_size
    bronze_size = BRONZE_DB.stat().st_size

    print("\n" + "=" * 74)
    print("RESULT")
    print("=" * 74)
    print(f"  f1.db (silver):  {human(size_before)} -> {human(size_after)}")
    print(f"  bronze_f1.db:    {human(bronze_size)}")
    print(f"  tables remaining in f1.db: {len(remaining)}")
    for t in remaining:
        print(f"    {t}")

    print("\nFollow-ups (nothing works until these are done):")
    print("  1. config.py       — add BRONZE_DB_PATH and GOLD_DB_PATH")
    print("  2. s01_ingest.py   — write to bronze, read silver_sessions from f1.db")
    print("  3. s01_backfill.py — same")
    print("  4. s02_build_silver.py — ATTACH bronze, read FROM bronze.<table>")
    print("  5. s03_verify.py   — remove silver_car_data / silver_location from EXPECTED_TABLES")
    print("  6. data_prep.py    — unchanged (silver only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())