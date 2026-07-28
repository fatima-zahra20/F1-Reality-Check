"""Central configuration. Every pipeline step imports paths from here."""
from pathlib import Path

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
EXCLUDED_TEAMS = ["Cadillac"]          # partial 2026 season, n≈8-10
STOP_DURATION_MIN_YEAR = 2024          # zero coverage in 2023

# --- telemetry (excluded from the weekly pipeline) ---
# car_data (9.4M rows) and location (25.8M rows) exist in bronze only. They cover
# 32 of 490 sessions, so they cannot be model features or appear in season-wide
# aggregates. Silver copies were dropped in the 2026-07-28 split; rebuild from
# bronze if ever needed.
TELEMETRY_TABLES = ["car_data", "location"]
INCLUDE_TELEMETRY_IN_WEEKLY = False

for _d in (OUTPUTS_DIR, MODELS_DIR, LOGS_DIR):
    _d.mkdir(exist_ok=True)