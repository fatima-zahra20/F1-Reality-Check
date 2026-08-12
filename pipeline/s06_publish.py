"""
s06_publish.py — packages the serving layer and publishes it to a GitHub Release.

Streamlit Cloud runs the app from GitHub and has no disk of its own, so the
dashboard needs its data from somewhere the cloud can reach. This step bundles
the ten serving CSVs into one SQLite file, compresses it, and uploads it as a
Release asset.

Why a Release asset and not the repo
------------------------------------
s04 and s05 rewrite all ten CSVs on every run. Committing them would add ~21 MB
of new blobs to git history every time — a changed data file is stored whole,
not as a diff, so the repo would grow without bound and every clone would carry
the entire history of it. Release assets live outside the git tree: they
overwrite in place, never accumulate, and do not affect clone size.

Why one SQLite file and not ten CSVs
------------------------------------
One asset instead of ten, one download instead of ten round trips, and the app
can filter with SQL rather than loading 90k laps into memory to show one race.
It also keeps the project's existing idiom — everything upstream is SQLite.

The tag is fixed at data-latest and the asset is replaced each run, so the
download URL never changes and the app can hardcode it. Deliberately not a
versioned release: this is a snapshot of current truth, not an artifact history.

Authentication
--------------
Needs a GitHub token with contents:write on this repo, read from GITHUB_TOKEN
(or GH_TOKEN). A fine-grained PAT scoped to this one repo is enough:

    https://github.com/settings/tokens?type=beta

    $env:GITHUB_TOKEN = "github_pat_..."        # this shell only
    setx GITHUB_TOKEN "github_pat_..."          # persist for future shells

Usage
-----
    python pipeline\\s06_publish.py                  # build + report, no upload
    python pipeline\\s06_publish.py --execute        # build + upload
    python pipeline\\s06_publish.py --build-only     # never touches the network
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import OUTPUTS_DIR  # noqa: E402

DASHBOARD_DIR = OUTPUTS_DIR / "dashboard"
DB_FILE = DASHBOARD_DIR / "dashboard.db"
GZ_FILE = DASHBOARD_DIR / "dashboard.db.gz"

REPO = "fatima-zahra20/F1-Reality-Check"
TAG = "data-latest"
ASSET_NAME = "dashboard.db.gz"
API = "https://api.github.com"

# Tables to bundle. Anything else in the directory is ignored, so a stray CSV
# cannot silently end up published.
# Written directly into dashboard.db by s04_descriptive, which reads gold.
# They have no CSV: the CSV was only ever an intermediate this script read back,
# which meant every value existed twice with nothing checking the copies agreed.
DB_TABLES = [
    "dim_race", "dim_driver", "dim_team",
    "fact_driver_race", "fact_lap", "fact_event", "fact_championship",
]

# Statistical and model output. These are conclusions, not data, so they are
# genuinely produced by the analysis steps and arrive as CSV.
CSV_TABLES = [
    "diag_tests", "diag_coefficients", "diag_groups", "diag_points",
    "map_circuit_outline", "map_measured_xy", "map_coverage",
    "lap_factor_anova", "lap_factor_model", "lap_factor_reference",
    "telemetry_tow", "telemetry_effect",
    "lap_counterfactual_model", "lap_counterfactual_bounds",
]

# NOT BUNDLED, and checked rather than assumed: perfect_lap, perfect_race,
# perfect_lap_record and perfect_lap_model have no FROM, JOIN, subquery,
# f-string or quoted reference anywhere in the app. 3,070 rows and 754 KB were
# computed, written, packed, gzipped, uploaded and downloaded by every visitor,
# then never read. The perfect-lap page was built on fact_lap plus the telemetry
# geometry in map_measured_xy instead.
#
# s05b_perfect.py still computes them, deliberately. They are ahead of the
# dashboard rather than useless: the planned application wants a choose-a-lap
# feature. Add the names back here when something reads them.
NOT_BUNDLED = [
    "perfect_lap", "perfect_lap_model", "perfect_lap_record", "perfect_race",
]

TABLES = DB_TABLES + CSV_TABLES

# The app filters by race and by test far more than anything else. Without
# these, every "show me this race" click scans 90k lap rows.
#
# Filtered against TABLES rather than listed freely, because the two lists drifted
# apart the moment the perfect_* tables stopped being bundled: the entries for
# them survived here and the build failed with "no such table: main.perfect_lap"
# AFTER it had already written every table. Deriving the list means removing a
# table from the bundle cannot leave a dangling index behind.
_INDEX_WISHLIST = [
    ("fact_lap", "session_key"),
    ("fact_driver_race", "session_key"),
    ("fact_event", "session_key"),
    ("fact_championship", "session_key"),
    ("diag_coefficients", "test_id"),
    ("diag_groups", "test_id"),
    ("diag_points", "test_id"),
    ("perfect_lap", "session_key"),
    ("perfect_race", "session_key"),
    ("map_circuit_outline", "circuit_key"),
    ("map_coverage", "session_key"),
    ("lap_factor_reference", "session_key"),
    ("lap_counterfactual_bounds", "session_key"),
]
INDEXES = [(t, c) for t, c in _INDEX_WISHLIST if t in TABLES]


def human(n: float) -> str:
    """Bytes as something readable in a log line."""
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} GB"


def build_db() -> dict[str, int]:
    """
    Completes dashboard.db: the fact and dimension tables are already in it,
    written by s04 from gold, so this loads the analysis-output CSVs around them.
    Returns row counts.
    """
    missing_csv = [t for t in CSV_TABLES
                   if not (DASHBOARD_DIR / f"{t}.csv").exists()]
    if missing_csv:
        raise FileNotFoundError(
            "missing analysis CSVs: " + ", ".join(missing_csv) + "\n"
            "Run s05_diagnostic.py, s05b_perfect.py, s05c_racemap.py and "
            "s05d_telemetry.py first."
        )
    if not DB_FILE.exists():
        raise FileNotFoundError(
            f"{DB_FILE.name} does not exist.\n"
            "Run s04_descriptive.py first: it writes the fact and dimension "
            "tables straight into the bundle."
        )

    # Drop and rewrite per table via to_sql(if_exists="replace"), matching
    # s04/s05, rather than deleting the file first. On Windows the file delete
    # fails outright if a local `streamlit run` still has dashboard.db open —
    # replacing tables in place works regardless of who else has it open for
    # reading. It is also what lets s04 write its tables before this runs.
    counts: dict[str, int] = {}
    with sqlite3.connect(DB_FILE) as con:
        present = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        absent = [t for t in DB_TABLES if t not in present]
        if absent:
            raise FileNotFoundError(
                "these tables are missing from the bundle: " + ", ".join(absent)
                + "\nRun s04_descriptive.py, which writes them from gold."
            )

        for table in DB_TABLES:
            n = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            counts[table] = n
            print(f"  {table:26s} {n:>8,} rows  (from gold via s04)")

        for table in CSV_TABLES:
            df = pd.read_csv(DASHBOARD_DIR / f"{table}.csv")
            df.to_sql(table, con, index=False, if_exists="replace")
            counts[table] = len(df)
            print(f"  {table:26s} {len(df):>8,} rows  (csv)")

        # Anything left over from a previous bundle would still be uploaded, so
        # it is dropped rather than carried. This is what removes the four
        # perfect_* tables nothing reads on the first run after that change.
        stale = present - set(TABLES) - {"sqlite_sequence"}
        for table in sorted(stale):
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
            print(f"  dropped stale table: {table}")

        # DROP TABLE (inside to_sql's replace) already drops that table's
        # indexes, so recreating them here never collides with a stale one.
        # The DB_TABLES indexes are not dropped by anything above, hence
        # IF NOT EXISTS.
        for table, col in INDEXES:
            con.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_{col} ON {table}({col})")

    # VACUUM cannot run inside the transaction the context manager holds open,
    # and needs exclusive access it may not get with another reader attached.
    # It only reclaims space, so skip it rather than fail the whole publish.
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("VACUUM")
        con.close()
    except sqlite3.OperationalError as exc:
        print(f"  (skipped VACUUM: {exc} — file is still valid, just not compacted)")

    return counts


def compress() -> None:
    """gzip the database — roughly 4x on this data, worth the second of CPU."""
    with open(DB_FILE, "rb") as src, gzip.open(GZ_FILE, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)


def get_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not tok:
        raise RuntimeError(
            "No GITHUB_TOKEN in the environment.\n"
            f"Create a fine-grained PAT with contents:write on {REPO} at\n"
            "  https://github.com/settings/tokens?type=beta\n"
            "then set it:\n"
            '  $env:GITHUB_TOKEN = "github_pat_..."'
        )
    return tok


def headers(tok: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_or_create_release(tok: str) -> dict:
    """Finds the data-latest release, creating it on the first run."""
    h = headers(tok)
    r = requests.get(f"{API}/repos/{REPO}/releases/tags/{TAG}", headers=h, timeout=30)

    if r.status_code == 200:
        return r.json()

    if r.status_code == 404:
        print(f"  release '{TAG}' does not exist yet — creating it")
        r = requests.post(
            f"{API}/repos/{REPO}/releases",
            headers=h,
            json={
                "tag_name": TAG,
                "name": "Dashboard data (latest)",
                "body": (
                    "Serving layer for the Streamlit dashboard, rebuilt by "
                    "`pipeline/s06_publish.py`. The asset is replaced in place, "
                    "so this is always the current snapshot rather than a "
                    "version history."
                ),
                "prerelease": True,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    r.raise_for_status()
    return {}


def upload(tok: str, release: dict) -> str:
    """Replaces the asset. Delete then upload — GitHub rejects a duplicate name."""
    h = headers(tok)

    for asset in release.get("assets", []):
        if asset["name"] == ASSET_NAME:
            print(f"  removing previous asset (id {asset['id']})")
            requests.delete(
                f"{API}/repos/{REPO}/releases/assets/{asset['id']}",
                headers=h, timeout=30,
            ).raise_for_status()

    upload_url = release["upload_url"].split("{")[0]
    with open(GZ_FILE, "rb") as fh:
        r = requests.post(
            upload_url,
            headers={**h, "Content-Type": "application/gzip"},
            params={"name": ASSET_NAME},
            data=fh,
            timeout=600,
        )
    r.raise_for_status()
    return r.json()["browser_download_url"]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bundle the serving layer and publish it to a GitHub Release."
    )
    ap.add_argument("--execute", action="store_true",
                    help="upload; without this the bundle is built but not published")
    ap.add_argument("--build-only", action="store_true",
                    help="build the bundle and stop, without contacting GitHub")
    args = ap.parse_args()

    started = time.time()
    print("=" * 70)
    print("s06_publish — bundling the serving layer")
    print("=" * 70)

    counts = build_db()
    db_size = DB_FILE.stat().st_size
    print(f"\n  dashboard.db     {human(db_size)}  ({sum(counts.values()):,} rows)")

    compress()
    gz_size = GZ_FILE.stat().st_size
    print(f"  dashboard.db.gz  {human(gz_size)}  ({db_size / gz_size:.1f}x smaller)")

    if args.build_only:
        print(f"\nBuilt in {time.time() - started:.1f}s. Not published (--build-only).")
        return 0

    if not args.execute:
        print("\nDRY RUN — nothing uploaded. Re-run with --execute to publish.")
        print(f"Would replace '{ASSET_NAME}' on release '{TAG}' of {REPO}.")
        return 0

    print(f"\nPublishing to {REPO} @ {TAG}")
    tok = get_token()
    release = get_or_create_release(tok)
    url = upload(tok, release)

    print(f"\n  published: {url}")
    print(f"  generated: {datetime.now(timezone.utc).isoformat()}")
    print(f"\nDone in {time.time() - started:.1f}s.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        sys.exit(1)
