"""Central configuration. Every pipeline step imports paths from here."""
from pathlib import Path

# Project root: this file lives in pipeline/, so go up one level.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- data ---
DB_PATH = PROJECT_ROOT / "DATA INGESTION" / "f1.db"
INGESTION_SCRIPT = PROJECT_ROOT / "DATA INGESTION" / "openf1_ingestion.py"
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
# silver_car_data (9.4M rows) + silver_location (25.8M rows) are ~35M of the
# database's 6.5 GB and cover only 32/490 sessions — unusable as model features.
# Refreshed manually on demand, never in the scheduled run.
TELEMETRY_TABLES = ["silver_car_data", "silver_location"]
INCLUDE_TELEMETRY_IN_WEEKLY = False

for _d in (OUTPUTS_DIR, MODELS_DIR, LOGS_DIR):
    _d.mkdir(exist_ok=True)