"""
s06_publish.py — packages the serving layer and publishes it to a GitHub Release.

Streamlit Cloud runs the app from GitHub and has no disk of its own, so the
dashboard needs its data from somewhere the cloud can reach. This step checks
dashboard.duckdb is complete, compacts it, compresses it, and uploads it as a
Release asset.

It no longer BUILDS anything
----------------------------
It used to read 14 CSV files and copy them into the bundle. Every one of those
datasets therefore sat on disk twice, with nothing checking the copies agreed,
and the app never read a CSV at all: the only read_csv in the entire project was
the one in this file doing the copying. Each producing step now writes its tables
straight into the bundle, so this step verifies instead of assembling.

That makes the completeness check in build_db the load-bearing part. Several
steps write into the same file in sequence, so a crash midway leaves it part
old and part new. Nothing else stands between that and the live site.

Why a Release asset and not the repo
------------------------------------
The bundle is rewritten on every run. Committing it would add ~12 MB of new
blobs to git history each time, since a changed binary is stored whole and not
as a diff, so the repo would grow without bound and every clone would carry the
entire history of it. Release assets live outside the git tree: they overwrite
in place, never accumulate, and do not affect clone size.

Why one DuckDB file
-------------------
One asset, one download, and the app can filter with SQL rather than loading 90k
laps into memory to show one race. It also keeps the project's existing idiom:
everything upstream is DuckDB.

THE ASSET IS VERSION-SENSITIVE IN A WAY THE SQLITE ONE WAS NOT. SQLite's file
format is fixed for the life of the format; DuckDB's storage format is tied to
the release that wrote it, and a reader too old for it refuses to open the file
outright. That is why requirements.txt pins duckdb to an exact version rather
than a floor: the pin is what guarantees the copy Streamlit Cloud installs can
read the file this machine writes.

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
import sys
import time
from datetime import datetime, timezone

import duckdb
import requests

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import compact_database  # noqa: E402
import serving  # noqa: E402

# Aliased rather than redefined. serving.py is the single definition of where
# the bundle lives; this file used to compute the same path itself, which is how
# six modules ended up each deciding independently where the bundle was.
DB_FILE = serving.BUNDLE_DB
GZ_FILE = serving.BUNDLE_GZ

REPO = "fatima-zahra20/F1-Reality-Check"
TAG = "data-latest"

# RENAMED WITH THE ENGINE. The asset used to be dashboard.db.gz, and the name
# has to change because the contents did: a DuckDB file called .db invites
# somebody to open it with sqlite3 and get "file is not a database".
#
# dashboard/app_common.py ASSET_URL must name the same file. The two are a
# matched pair and there is no way for either to detect the other is wrong: the
# app would simply get a 404 and report that the data has not been published.
# The old dashboard.db.gz stays on the release until deleted by hand, because
# upload() only removes an asset whose name matches this one.
ASSET_NAME = "dashboard.duckdb.gz"
API = "https://api.github.com"

# Tables to bundle. Anything else in the folder is ignored, so a stray file
# cannot silently end up published.
#
# WRITTEN STRAIGHT INTO dashboard.db by the step named, with no CSV in between.
# The CSV was only ever an intermediate this script read back, which meant every
# value existed twice on disk with nothing checking the copies agreed, and the
# app never read a CSV at all. Mapped to their producer rather than listed,
# because "this table is missing" is only useful with "run this to get it".
DB_TABLES = {
    "dim_race": "s04_descriptive",
    "dim_driver": "s04_descriptive",
    "dim_team": "s04_descriptive",
    "fact_driver_race": "s04_descriptive",
    "fact_lap": "s04_descriptive",
    "fact_event": "s04_descriptive",
    "fact_championship": "s04_descriptive",
    "diag_tests": "s05_diagnostic",
    "diag_coefficients": "s05_diagnostic",
    "diag_groups": "s05_diagnostic",
    "diag_points": "s05_diagnostic",
    "lap_factor_anova": "s05b_prescriptive",
    "lap_factor_model": "s05b_prescriptive",
    "lap_factor_reference": "s05b_prescriptive",
    "lap_counterfactual_model": "s05b_prescriptive",
    "lap_counterfactual_bounds": "s05b_prescriptive",
    "map_circuit_outline": "s05c_racemap",
    "map_measured_xy": "s05c_racemap",
    "map_coverage": "s05c_racemap",
    "telemetry_tow": "s05d_telemetry",
    "telemetry_effect": "s05d_telemetry",
}

# s05b can also produce perfect_lap, perfect_race, perfect_lap_record and
# perfect_lap_model, which are NOT bundled. Checked rather than assumed: none of
# them has a FROM, JOIN, subquery, f-string or quoted reference anywhere in the
# app. 3,070 rows were computed, written, packed, gzipped, uploaded and
# downloaded by every visitor, then never read. The perfect-lap page was built
# on fact_lap plus the geometry in map_measured_xy instead.
#
# They are no longer written at all unless named on --tables, and then only as
# CSV under outputs/analysis/. The list lives in s05b_prescriptive.ON_REQUEST,
# next
# to the code that writes them, and is deliberately NOT repeated here. It used
# to be, and a name in two places is a name that can disagree with itself: the
# same duplication left a dangling entry in the index list below and broke a
# build after every table had already been written.

TABLES = list(DB_TABLES)

# THE BUNDLE CARRIES NO INDEXES, AND THAT IS A MEASUREMENT RATHER THAN AN
# OVERSIGHT.
#
# It used to build eleven, on the columns the app filters by: session_key on
# fact_lap, fact_event and the rest, test_id on the diagnostic tables. Under
# SQLite they were doing real work, because without one a "show me this race"
# click means a full scan of 90k lap rows.
#
# DuckDB does not work that way. It is columnar and keeps min/max zone maps per
# row group, so it already skips the blocks that cannot match, and its index is
# an ART built for single-row lookups and constraint checks. Handing a
# multi-row predicate to that index replaces a vectorised scan with row-by-row
# probing. Timed on this bundle, twenty runs each:
#
#     one race's laps      18.16 ms unindexed    141.00 ms indexed
#     one race's events     5.71 ms               18.21 ms
#     one test's points     2.71 ms               22.05 ms
#     one circuit outline   3.71 ms               17.22 ms
#     one race's drivers    8.46 ms                8.32 ms
#
# Nothing got faster and most got several times slower, while the indexes added
# 4.2 MB to a 14.0 MB file: 30% more for every visitor to download, to make the
# app worse. So they are dropped rather than carried, and dropped explicitly
# below rather than merely not created, because a bundle built before this
# change still has them.
#
# If a genuine point lookup ever appears in the app, measure again before
# concluding an index would help it.
STALE_INDEX_PREFIX = "ix_"


def human(n: float) -> str:
    """Bytes as something readable in a log line."""
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} GB"


def drop_stale_indexes() -> None:
    """
    Remove indexes an older build left on the bundle.

    Dropped BEFORE compact_database rather than after, so the fresh copy never
    carries them into the file that gets uploaded. See STALE_INDEX_PREFIX above
    for why the bundle no longer has any indexes at all.
    """
    with duckdb.connect(str(DB_FILE)) as con:
        stale = [r[0] for r in con.execute(
            "SELECT index_name FROM duckdb_indexes() WHERE index_name LIKE ?",
            [STALE_INDEX_PREFIX + "%"]).fetchall()]
        for name in stale:
            con.execute(f'DROP INDEX IF EXISTS "{name}"')
    if stale:
        print(f"  dropped {len(stale)} index(es) from an earlier build")


def build_db() -> dict[str, int]:
    """
    Checks the bundle is complete, drops anything stale, and compacts it.

    NO LONGER BUILDS ANYTHING. Every table is now written directly by the step
    that computes it, so this verifies rather than assembles. It used to read 14
    CSV files back and copy them in, which meant each dataset sat on disk twice
    with nothing checking the copies agreed, and the app never read a CSV.

    That makes the check below the ONLY thing standing between a half-finished
    build and the live site, since several steps write into the same file in
    sequence and a crash leaves it part-updated. It refuses to publish unless
    every expected table is present, and names the step to run for each one that
    is not. Returns row counts.
    """
    if not DB_FILE.exists():
        raise FileNotFoundError(
            f"{DB_FILE.name} does not exist.\n"
            "Run s04_descriptive.py first: it writes the fact and dimension "
            "tables straight into the bundle."
        )

    # Tables are replaced in place by each step rather than the file being
    # deleted first, which is what lets s04 write its tables before this runs.
    #
    # THE SECOND HALF OF THAT COMMENT USED TO SAY "regardless of who else has it
    # open for reading". That was true of SQLite and is NOT true here. DuckDB
    # allows many readers or one writer, per process, across the whole file, so
    # a local `streamlit run` holding a read-only handle blocks this open
    # entirely. serving.connect() carries the explanation the five serving steps
    # need; this one connect is the only place in the publish path that can hit
    # it, and it fails before anything is written.
    #
    # `with duckdb.connect(...)` CLOSES the connection on exit, where sqlite3's
    # context manager only committed and left it open. That is the behaviour
    # wanted here, because compact_database below opens the file itself.
    counts: dict[str, int] = {}
    with duckdb.connect(str(DB_FILE)) as con:
        present = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main'").fetchall()}
        absent = [t for t in DB_TABLES if t not in present]
        if absent:
            by_step: dict[str, list[str]] = {}
            for t in absent:
                by_step.setdefault(DB_TABLES[t], []).append(t)
            raise FileNotFoundError(
                "these tables are missing from the bundle:\n"
                + "\n".join(f"  run {step}.py  ->  {', '.join(sorted(ts))}"
                            for step, ts in sorted(by_step.items()))
            )

        for table, producer in DB_TABLES.items():
            n = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            counts[table] = n
            print(f"  {table:26s} {n:>8,} rows  ({producer})")

        # Anything left over from a previous bundle would still be uploaded, so
        # it is dropped rather than carried. This is what removes the four
        # perfect_* tables nothing reads on the first run after that change.
        # sqlite_sequence is no longer excluded here because DuckDB has no such
        # table: it has no AUTOINCREMENT to keep a counter for.
        stale = present - set(TABLES)
        for table in sorted(stale):
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
            print(f"  dropped stale table: {table}")

    drop_stale_indexes()
    before, after = compact_database(DB_FILE)
    if before > after:
        print(f"  compacted {human(before)} -> {human(after)}")
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
    print(f"\n  {DB_FILE.name:16s} {human(db_size)}  ({sum(counts.values()):,} rows)")

    compress()
    gz_size = GZ_FILE.stat().st_size
    print(f"  {GZ_FILE.name:16s} {human(gz_size)}  "
          f"({db_size / gz_size:.1f}x smaller)")

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
