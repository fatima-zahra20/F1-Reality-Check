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

import sys
from pathlib import Path
import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import OUTPUTS_DIR, PROJECT_ROOT  # noqa: E402

BUNDLE_DIR = PROJECT_ROOT / "dashboard" / "data"
BUNDLE_DB = BUNDLE_DIR / "dashboard.duckdb"
BUNDLE_GZ = BUNDLE_DIR / "dashboard.duckdb.gz"

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


def connect() -> duckdb.DuckDBPyConnection:
    """
    Open the bundle for writing, creating the folder on first run.

    THE ERROR HANDLING IS THE POINT OF THIS BEING A FUNCTION NOW.

    DuckDB allows many readers or one writer, and the lock is held per PROCESS
    across the whole file. SQLite let a writer in while readers were attached,
    so `streamlit run` against the local bundle cost nothing. It is no longer
    free: a local dashboard holds a read-only handle, and that is enough to stop
    every serving step from writing. Measured, across two processes.

    Raw, the failure is an IOException from inside DuckDB naming a temp path and
    a PID, which says nothing about what to do. Five steps hit this same line, so
    the explanation belongs here rather than in each of them.

    The same lock is why a notebook holding data_prep's `dbset` blocks a silver
    rebuild. It has bitten this project once already.

    RAISES SystemExit, NOT AN EXCEPTION TO CATCH. A locked file is an
    operational condition with an obvious remedy, not a defect, and a traceback
    for it is noise that buries the one line worth reading. Every caller of this
    is a script whose main() would immediately print the message and exit 1
    anyway, so doing it here avoids the same try/except in five files. Nothing
    has been written when this fires.
    """
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return duckdb.connect(str(BUNDLE_DB))
    except duckdb.IOException as exc:
        raise SystemExit(
            f"\n[FAIL] Cannot open {BUNDLE_DB.name} for writing: "
            "another process has it.\n"
            "\n"
            "  DuckDB allows many readers or one writer, so anything holding\n"
            "  this file open stops the pipeline writing to it. Almost always\n"
            "  one of:\n"
            "\n"
            "    - a local `streamlit run` serving the dashboard\n"
            "    - a notebook that imported data_prep and touched `dbset`\n"
            "    - an earlier pipeline run that has not exited\n"
            "\n"
            "  Close it and run this step again. Nothing has been written, so\n"
            "  the bundle is exactly as it was.\n"
            f"\n  DuckDB said: {str(exc).splitlines()[0]}\n"
        ) from exc


def write_table(df: pd.DataFrame, name: str, con: duckdb.DuckDBPyConnection,
                csv: bool = False) -> int:
    """
    Write one table into the bundle, replacing whatever was there.

    Returns the row count, so callers can report what they wrote rather than
    what they intended to write.

    Registered under a fixed private name rather than left to DuckDB's habit of
    finding DataFrames by inspecting the caller's variables. That habit works,
    but it would silently pick up a local called `df` in whichever function
    happened to call this, and it would lose to a real table of the same name.
    Registering and unregistering says exactly what is being written.
    """
    con.register("_incoming", df)
    try:
        con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _incoming')
    finally:
        con.unregister("_incoming")
    if csv:
        df.to_csv(BUNDLE_DIR / f"{name}.csv", index=False)
    return len(df)



def write_analysis_csv(df: pd.DataFrame, name: str) -> int:
    """Write analysis output that is not part of the bundle."""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(ANALYSIS_DIR / f"{name}.csv", index=False)
    return len(df)


def table_names(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Every table currently in the bundle."""
    return sorted(r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main'").fetchall())


def row_count(con: duckdb.DuckDBPyConnection, name: str) -> int | None:
    """Rows in one table, or None if it is not there at all.

    s06 uses this to tell 'the step never ran' from 'the step ran and produced
    nothing'. Those need different messages: the first is a missing dependency,
    the second is an empty result that may be legitimate.
    """
    try:
        return con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    except duckdb.Error:
        return None
