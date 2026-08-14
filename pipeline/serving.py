"""
serving.py — where the dashboard's data lives, and how a table gets into it.

Why this module exists
----------------------
Two duplications, both of a shape that has already produced bugs in this repo.

1. THE PATH WAS WRITTEN OUT SIX TIMES. `OUTPUTS_DIR / "dashboard"` appeared in
   s04, s05, s05b, s05c, s05d and s06. Moving the bundle meant editing six files
   and hoping none was missed. Compare EXCLUDED_TEAMS, which was declared twice
   and LAP_OUTLIER_FACTOR, which was declared four times; both were consolidated
   for the same reason. One definition, imported everywhere.

2. EVERY DATASET WAS WRITTEN TO DISK TWICE. s05, s05b, s05c and s05d each wrote
   CSV files, and s06 then opened those CSVs and copied them into dashboard.db.
   Nothing else ever read them: there is not one `read_csv` in the dashboard, and
   the only one in the whole project was the line in s06 doing the copying. So
   18 files and roughly 36 MB were regenerated every run to be read once by the
   step that deleted the need for them.

   s04 was converted first and kept a `--csv` flag, off by default, because a
   CSV can be opened and read by eye and a database table cannot. The other four
   converge here and keep the same flag.

What this does NOT do
---------------------
It does not decide WHICH tables belong in the bundle. That is s06's job, and it
stays there: s06 owns the list, drops anything stale, and refuses to publish an
incomplete one. This module only knows where the file is and how to put a
dataframe into it.

Writes are drop-and-replace per table, matching what s04 and s06 already did.
Several steps write into the same file in sequence, so a crash midway leaves the
file half-updated. That is deliberate and accepted: the live dashboard reads a
GitHub Release asset, never this file, and s06 validates every table before it
uploads anything. The local file being briefly inconsistent cannot reach a
visitor.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import OUTPUTS_DIR, PROJECT_ROOT  # noqa: E402

# The one definition. Everything that reads or writes the bundle imports these.
#
# The bundle lives BESIDE THE APP, in dashboard/data/, not under outputs/. There
# used to be two folders called "dashboard": the app's source code, and the build
# output. Same name, opposite things, one written by hand and one regenerated
# every run. Putting the database next to the app it serves leaves one folder
# with that name and makes the app's own lookup a sibling path rather than a
# walk up and back down.
#
# The data/ subfolder keeps the 67 MB generated file out of the folder listing
# you read when you are looking for a page's source, so hand-written code and
# build output stay visually separate even though they now share a parent.
#
# It is kept out of git three times over: the *.db and *.gz rules match on the
# filename rather than the folder, so moving it again cannot silently drop a
# 67 MB file into the repository, and the data/ rule ignores this folder whole.
BUNDLE_DIR = PROJECT_ROOT / "dashboard" / "data"
BUNDLE_DB = BUNDLE_DIR / "dashboard.db"
BUNDLE_GZ = BUNDLE_DIR / "dashboard.db.gz"

# Analysis output that is NOT part of the bundle, and so must not sit in the
# bundle folder. Keeping such files beside the shipped ones is how s05b's four
# perfect_* tables came to be packed, gzipped and downloaded by every visitor
# for weeks without anything reading them.
#
# Nothing writes here on a normal run. s05b's perfect_* tables are the only
# users and they now build only when named on --tables, so this folder is
# created on demand and is expected to be absent most of the time. CSV rather
# than a table because nothing serves them, and a CSV is the format you can open
# and look at.
ANALYSIS_DIR = OUTPUTS_DIR / "analysis"


def connect() -> sqlite3.Connection:
    """Open the bundle for writing, creating the folder on first run."""
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(BUNDLE_DB)


def write_table(df: pd.DataFrame, name: str, con: sqlite3.Connection,
                csv: bool = False) -> int:
    """
    Write one table into the bundle, replacing whatever was there.

    Returns the row count, so callers can report what they wrote rather than
    what they intended to write.
    """
    df.to_sql(name, con, index=False, if_exists="replace")
    if csv:
        df.to_csv(BUNDLE_DIR / f"{name}.csv", index=False)
    return len(df)


def write_analysis_csv(df: pd.DataFrame, name: str) -> int:
    """Write analysis output that is not part of the bundle."""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(ANALYSIS_DIR / f"{name}.csv", index=False)
    return len(df)


def table_names(con: sqlite3.Connection) -> list[str]:
    """Every table currently in the bundle."""
    return sorted(r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"))


def row_count(con: sqlite3.Connection, name: str) -> int | None:
    """Rows in one table, or None if it is not there at all.

    s06 uses this to tell 'the step never ran' from 'the step ran and produced
    nothing'. Those need different messages: the first is a missing dependency,
    the second is an empty result that may be legitimate.
    """
    try:
        return con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    except sqlite3.OperationalError:
        return None
