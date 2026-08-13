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
BRONZE_DB_PATH = PROJECT_ROOT / "DATA INGESTION" / "bronze_f1.db"
DB_PATH        = PROJECT_ROOT / "DATA INGESTION" / "f1.db"           # silver
GOLD_DB_PATH   = PROJECT_ROOT / "DATA INGESTION" / "gold_f1.db"      # built later

INGESTION_SCRIPT = PROJECT_ROOT / "DATA INGESTION" / "openf1_ingestion.py"

# Reference only — the executable build is pipeline/s02_build_silver.py
SILVER_SQL = PROJECT_ROOT / "SCHEMA MODELING" / "to_silver.sql"

# --- query libraries ---
PROFILING_SQL_DIR = PROJECT_ROOT / "DATA PROFILING"
DESCRIPTIVE_SQL_DIR = PROJECT_ROOT / "DESCRIPTIVE ANALYTICS"
DIAGNOSTIC_NB_DIR = PROJECT_ROOT / "DIAGNOSTIC ANALYTICS"

# --- pipeline outputs ---
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

for _d in (OUTPUTS_DIR, MODELS_DIR, LOGS_DIR):
    _d.mkdir(exist_ok=True)