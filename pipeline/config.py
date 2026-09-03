"""Central configuration. Every pipeline step imports paths from here."""
from pathlib import Path

# --- environment guard -----------------------------------------------------------
# Every pipeline step imports this module, so this is the one place that catches a
# wrong interpreter before it can do damage.
#
# The failure this prevents is not a crash — it is worse than a crash. pandas 3.x
# changes groupby, NaN handling and dtype inference in ways that shift results by
# a few percent without erroring, so an accidental run produces numbers that look
# plausible, differ from every recorded figure, and read as a data problem rather
# than an environment one. NOTES_LOG #42 records this happening once already: a
# stray python 3.14 / pandas 3.0.3 install, since re-created, currently sits ahead
# of the pinned Anaconda environment on PATH.
#
# Fail loudly at import instead.
try:
    import pandas as _pd
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "pandas is not installed in this interpreter.\n"
        "The F1 Reality Check pipeline requires the pinned Anaconda environment "
        "(python 3.13.9, pandas 2.3.3, statsmodels 0.14.5).\n"
        "Create it with:  conda env create -f environment-pipeline.yml"
    ) from _exc

_PANDAS_MAJOR = int(_pd.__version__.split(".")[0])
if _PANDAS_MAJOR != 2:
    import sys as _sys
    raise ImportError(
        f"pandas {_pd.__version__} detected — this pipeline requires pandas 2.x.\n"
        f"  interpreter: {_sys.executable}\n"
        f"  python:      {_sys.version.split()[0]}\n\n"
        "pandas 3.x does not crash here, it silently returns different numbers, "
        "which is why this is a hard stop rather than a warning (NOTES_LOG #42).\n"
        "Run with the pinned environment instead:\n"
        "  conda activate f1-reality-check\n"
        "or invoke that interpreter directly, e.g.\n"
        '  & "$env:USERPROFILE\\anaconda3\\python.exe" pipeline\\run_pipeline.py --execute'
    )

# Project root: this file lives in pipeline/, so go up one level.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- databases (medallion layers, one file each) ---
# Bronze is raw API output, read only by the silver build and re-fetchable, so it
# lives outside the working database. Split on 2026-07-28: f1.db went from
# 6.4 GB to 352 MB.
#
# DuckDB since 2026-08-27, replacing SQLite. Columnar rather than row storage,
# which is the shape this pipeline actually has: it never fetches a single row
# and every step scans millions. Measured on this project's own data before
# committing to it, 3.853 GB became 0.788 GB and the s05c position scan went
# from 264.4s to 19.5s returning byte-identical rows.
#
# The silver file is renamed at the same time. It was the only layer not named
# for itself, which was a small inconsistency that a rename makes free to fix.
BRONZE_DB_PATH = PROJECT_ROOT / "DATA INGESTION" / "bronze_f1.duckdb"
DB_PATH        = PROJECT_ROOT / "DATA INGESTION" / "silver_f1.duckdb"   # silver
GOLD_DB_PATH   = PROJECT_ROOT / "DATA INGESTION" / "gold_f1.duckdb"     # built later


# INGESTION_SCRIPT stood here, pointing at DATA INGESTION/openf1_ingestion.py.
# Both the constant and the script are gone: the script was superseded by
# s01_ingest.py long before the DuckDB migration, nothing imported the constant,
# and a path to a file that no longer exists is worse than no path at all.

# Reference only — the executable build is pipeline/s02_build_silver.py
SILVER_SQL = PROJECT_ROOT / "SCHEMA MODELING" / "to_silver.sql"

# --- query libraries ---
PROFILING_SQL_DIR = PROJECT_ROOT / "DATA PROFILING"
DESCRIPTIVE_SQL_DIR = PROJECT_ROOT / "DESCRIPTIVE ANALYTICS"
DIAGNOSTIC_NB_DIR = PROJECT_ROOT / "DIAGNOSTIC ANALYTICS"

# --- pipeline outputs ---
# OUTPUTS_DIR has one user left: serving.ANALYSIS_DIR, for the perfect_* tables
# that only build when named on --tables. It is NOT created on import (see the
# bottom of this file) because the folder would otherwise reappear empty on
# every run of every step. Everything that used to live under it has moved: the
# dashboard bundle to dashboard/data/, the coverage snapshot to pipeline/.
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
MODELS_DIR = PROJECT_ROOT / "models"
LOGS_DIR = PROJECT_ROOT / "logs"

# --- analysis scoping (from the diagnostic phase) ---
SEASONS = [2023, 2024, 2025, 2026]
TRAIN_SEASONS = [2023, 2024, 2025]
TEST_SEASONS = [2026]
# Partial 2026 season only (n≈8-10 races), so there is no 2023-25 history:
# every trailing feature is undefined and every multi-year comparison is
# unbalanced. A modelling decision, not a data one, which is why gold carries
# the team and lets consumers exclude rather than dropping the rows itself.
# This is the single definition. s05_diagnostic imports it; do not redeclare.
EXCLUDED_TEAMS = ["Cadillac"]
STOP_DURATION_MIN_YEAR = 2024          # zero coverage in 2023

# A clean lap longer than this multiple of its session's median clean lap is
# treated as a car sitting in a red-flag queue rather than a lap. Expressed as a
# ratio, never in seconds, so it travels across circuits.
#
# THE STATED RATIONALE NO LONGER HOLDS and this constant is under review. Red
# flag queues are now caught by silver_lap_flags, so the factor removes 6 laps
# rather than the 12 it was introduced for. It also catches 0 of the 35 laps
# left green by the known safety-car withdrawal bug, whose ratios top out at
# 1.50 (DATA_DICTIONARY, known caution gaps). Consolidated here first so that
# whatever replaces it changes one line: it was previously declared four times,
# in s04, s05, s05b and s05d, with nothing checking the copies agreed.
LAP_OUTLIER_FACTOR = 2.0

# --- telemetry (excluded from the weekly pipeline) ---
# car_data (9.4M rows) and location (25.8M rows) exist in bronze only. They cover
# 32 of 490 sessions, so they cannot be model features or appear in season-wide
# aggregates. Silver copies were dropped in the 2026-07-28 split; rebuild from
# bronze if ever needed.
TELEMETRY_TABLES = ["car_data", "location"]
INCLUDE_TELEMETRY_IN_WEEKLY = False

# OUTPUTS_DIR is deliberately not in this list. Nothing writes there on a normal
# run any more, and creating it here would rebuild an empty folder every time a
# step imports config. Its one remaining consumer, serving.write_analysis_csv,
# creates it on demand with parents=True.
for _d in (MODELS_DIR, LOGS_DIR):
    _d.mkdir(exist_ok=True)


# --- database access -------------------------------------------------------------
# Lives here rather than in a module of its own because every pipeline step
# already imports config, and one shared definition beats the same five lines
# copied into seven files where they can drift apart.

# The pandas nullable dtypes. An integer column containing NULLs comes back from
# DuckDB as one of these; under sqlite3 it came back as plain float64 with NaN.
# See _match_sqlite_dtypes for why that difference has to be undone here.
_MASKED_DTYPES = (
    _pd.BooleanDtype,
    _pd.Int8Dtype, _pd.Int16Dtype, _pd.Int32Dtype, _pd.Int64Dtype,
    _pd.UInt8Dtype, _pd.UInt16Dtype, _pd.UInt32Dtype, _pd.UInt64Dtype,
    _pd.Float32Dtype, _pd.Float64Dtype,
)


def _match_sqlite_dtypes(df: "_pd.DataFrame") -> "_pd.DataFrame":
    """
    Converts a DuckDB result to the dtypes this codebase was written against,
    which are the ones sqlite3 produced. Two separate conversions, each with its
    own failure behind it.

    ONE: NULLABLE COLUMNS BACK TO NUMPY.

    sqlite3 has no column types to report, so pandas inferred every integer
    column that contained a NULL as float64 and wrote the nulls as NaN. DuckDB
    reports a real type, so the same column arrives as the masked dtype Int32
    with the nulls written as pd.NA. The values are identical - verified on
    silver_lap_flags, 57 nulls either way - but the two null objects do not
    behave alike:

        nan == 1  -> False          pd.NA == 1  -> pd.NA
        if nan == 1:  runs          if pd.NA == 1:  TypeError

    s05's T19 raised exactly that, "boolean value of NA is ambiguous", from an
    `if r["red_flag"] == 1` that had been correct for the life of the project.
    Nothing was wrong with the analysis; the null had changed shape underneath
    it. 51 columns across silver and gold arrive masked.

    TWO: INTEGERS WIDENED TO int64.

    sqlite3 returned int64 for every integer. DuckDB returns the declared width,
    so session_key arrives as int32. That looks harmless, and merge() tolerates
    it, but pd.merge_asof does NOT: it requires its `by` keys to match exactly
    and raises "incompatible merge keys dtype('int64') and dtype('int32')". s05c
    failed on precisely that, joining bronze position samples (int64, via
    astype(int)) to silver laps (int32, via this function). s04, s05, s05b and
    s05d all use merge_asof too.

    Widening here rather than at the eight call sites also restores the bundle's
    own column types: the pre-migration bundle was BIGINT throughout, and
    passing int32 through had quietly narrowed 26 of its columns.

    Only the masked and numeric numpy dtypes are touched. Anything else pandas
    calls an extension dtype is left alone, so a timezone-aware timestamp column
    is never mangled by this.

    Normalising once at the boundary is the smaller and more honest change than
    auditing every scalar comparison, .astype(int), truth test and merge_asof in
    the codebase, and then again in the dashboard, which reads the same frames.
    The engine swap is supposed to be invisible above this line.
    """
    for col in df.columns:
        dtype = df[col].dtype

        if isinstance(dtype, _MASKED_DTYPES):
            # A masked column exists precisely because it can hold nulls.
            # float64 is the only numpy dtype that can hold them, and it is what
            # sqlite3 produced. With none actually present, fall through to the
            # widening below by taking the plain numpy dtype first.
            if df[col].isna().any():
                df[col] = df[col].astype("float64")
                continue
            df[col] = df[col].to_numpy(dtype=dtype.numpy_dtype)
            dtype = df[col].dtype

        if isinstance(dtype, _pd.api.extensions.ExtensionDtype):
            continue        # datetimes with a timezone, categoricals: not ours
        if dtype.kind in "iu" and dtype.itemsize < 8:
            df[col] = df[col].astype("int64")
        elif dtype.kind == "f" and dtype.itemsize < 8:
            df[col] = df[col].astype("float64")
    return df


def read_sql(sql: str, con, params=None) -> "_pd.DataFrame":
    """
    Run a query and get a DataFrame back.

    Not pd.read_sql. That accepts a DuckDB connection and works, but it goes
    through the DB-API and builds Python objects a row at a time, which throws
    away most of the reason for using DuckDB: the s05c position scan measured
    264.4s that way against 19.5s through this one.

    Argument order matches pd.read_sql so the call sites it replaces read the
    same as they did — including the dtypes they get back, which is what
    _match_sqlite_dtypes is for.
    """
    if params is not None:
        df = con.execute(sql, params).df()
    else:
        df = con.execute(sql).df()
    return _match_sqlite_dtypes(df)


def compact_database(path, label: str = "") -> tuple[int, int]:
    """
    Rewrite a DuckDB file into fresh storage, reclaiming what a rebuild left.

    THIS EXISTS BECAUSE VACUUM DOES NOT DO IT. DuckDB accepts VACUUM and returns
    success without reclaiming a byte, which is the worst way for a thing to not
    work: it looks exactly like it is working.

    Every build step here rewrites its tables whole, and DuckDB appends the new
    version rather than reusing the blocks the old one held. So the layers grow
    on every run even when the data is identical. Measured 2026-08-31, on files
    holding exactly the data they hold now:

        silver_f1.duckdb    259.3 MB -> 219.3 MB
        gold_f1.duckdb      107.0 MB ->  64.0 MB
        bronze_f1.duckdb    651.3 MB -> 550.5 MB

    That is 184 MB of nothing, and it accumulates weekly. COPY FROM DATABASE is
    the operation that actually rebuilds the storage.

    The copy is written beside the original and swapped in only once it is
    complete, so an interrupted compaction leaves the file it started from
    rather than half of one. Verified across all three layers: every table
    carried over with no row count differing.

    Returns (bytes before, bytes after). The caller must hold NO open connection
    to the file: DuckDB takes an exclusive lock to write, and this opens it for
    writing.
    """
    import duckdb as _duckdb

    path = Path(path)
    if not path.exists():
        return 0, 0

    before = path.stat().st_size
    fresh = path.with_suffix(".compact")
    fresh.unlink(missing_ok=True)

    con = _duckdb.connect(str(path))
    try:
        source = con.execute(
            "SELECT database_name FROM duckdb_databases() WHERE NOT internal"
        ).fetchone()[0]
        con.execute(f"ATTACH '{fresh.as_posix()}' AS _compacted")
        con.execute(f'COPY FROM DATABASE "{source}" TO _compacted')
        con.execute("DETACH _compacted")
    except _duckdb.Error:
        # Never fail a build over housekeeping. A large file is a nuisance; a
        # pipeline that stops because it could not tidy up is worse.
        fresh.unlink(missing_ok=True)
        con.close()
        return before, before
    con.close()

    after = fresh.stat().st_size
    fresh.replace(path)
    if label and before > after:
        print(f"  compacted {label}: {before / 1024**2:,.1f} MB -> "
              f"{after / 1024**2:,.1f} MB")
    return before, after