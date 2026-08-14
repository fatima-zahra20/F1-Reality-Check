"""
run_pipeline.py — sequences the pipeline steps and logs the run.

Order
-----
    s01_ingest        fetch anything new or previously failed
    s02_build_silver  rebuild silver from bronze   (skipped if nothing new)
    s02b_caution_flags rebuild derived flag tables (skipped if nothing new)
    s03_verify        invariant gate               (always runs)
    s07_build_gold    gold layer, the source for everything below
    s04_descriptive   descriptive serving layer    (only if the gate passed)
    s05_diagnostic    diagnostic serving layer     (only if the gate passed)
    s06_publish       push data to the dashboard   (only with --publish)

Skipping matters: most weeks bring no new data, and rebuilding 2.1M interval
rows to produce a byte-identical result is wasted time. The gate always runs, so
a no-op week still confirms the database is sound.

The serving layers do NOT follow that skip rule. They are cheap and fully
derived, so they rebuild on every successful run rather than only when
ingestion found rows — otherwise a manual run to refresh the dashboard would
silently do nothing, which is the more expensive failure.

They are gated on s03 passing. Publishing a serving layer built on a database
that failed its own invariants would put wrong data in front of someone as
finished output, which is worse than publishing nothing.

s06 is opt-in via --publish rather than automatic. Everything before it writes
to this machine and is undoable; s06 replaces the data behind a public URL. A
step that changes what the outside world sees should be asked for explicitly,
not ride along with a routine refresh.

Exit codes
----------
    0  everything succeeded
    1  a step failed
    2  the verification gate reported FAIL (serving layers skipped)

Each run writes a timestamped log to logs/. Failures are visible there rather
than only on a console nobody was watching.

Usage
-----
    python pipeline\\run_pipeline.py                # dry run — plans only
    python pipeline\\run_pipeline.py --execute
    python pipeline\\run_pipeline.py --execute --force-rebuild
    python pipeline\\run_pipeline.py --execute --skip-ingest
    python pipeline\\run_pipeline.py --execute --publish   # also refresh the dashboard
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import BRONZE_DB_PATH, DB_PATH, LOGS_DIR  # noqa: E402

PIPELINE_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable

# Bronze tables whose silver counterparts must be rebuilt when new data arrives.
# Telemetry is deliberately absent — never fetched by the scheduled run.
REBUILD_TABLES = [
    "meetings", "sessions", "drivers",
    "laps", "stints", "pit", "position", "intervals", "overtakes",
    "race_control", "session_result", "starting_grid", "team_radio", "weather",
    "championship_drivers", "championship_teams",
]


class Runner:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.lines: list[str] = []

    def log(self, text: str = "") -> None:
        print(text)
        self.lines.append(text)

    def flush(self) -> None:
        self.log_path.write_text("\n".join(self.lines), encoding="utf-8")

    def run_step(self, name: str, script: str, extra_args: list[str] | None = None):
        """Runs one step. Returns (returncode, stdout)."""
        cmd = [PYTHON, str(PIPELINE_DIR / script)] + (extra_args or [])

        self.log("")
        self.log("=" * 74)
        self.log(f"STEP: {name}")
        self.log(f"  {' '.join(cmd[1:])}")
        self.log("=" * 74)

        started = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        elapsed = time.time() - started

        if proc.stdout:
            self.log(proc.stdout.rstrip())
        if proc.stderr.strip():
            self.log("--- stderr ---")
            self.log(proc.stderr.rstrip())

        self.log(f"[{name}] exit={proc.returncode} in {elapsed:.1f}s")
        return proc.returncode, proc.stdout


def rows_inserted(stdout: str) -> int:
    """Reads 'rows inserted: N' from the ingest step's summary."""
    m = re.search(r"rows inserted:\s*([\d,]+)", stdout)
    return int(m.group(1).replace(",", "")) if m else 0


def stale_tables() -> list[str]:
    """
    Tables where bronze has grown since silver was last built from it.

    Why this is not just `rows_inserted() > 0`. That number counts per-session
    endpoint fetches only. The global tables (meetings, sessions, drivers) are
    refreshed by full replace and never appear in it, so a change to the F1
    calendar grows bronze while ingest truthfully reports zero new rows, and the
    rebuild is skipped.

    Found 2026-08-10, on the first real run after the gate learned to detect
    this: OpenF1 published the 2026 Bahrain Grand Prix and its five sessions,
    bronze took them, ingest reported 'new rows: 0', silver was left behind, and
    the gate stopped the pipeline. The same blind spot also covers anything
    s01_backfill.py writes, since it is not a pipeline step at all.

    Reads the state s02_build_silver records rather than parsing stdout, so it
    cannot be fooled by a step choosing not to mention something.
    """
    if not (DB_PATH.exists() and BRONZE_DB_PATH.exists()):
        return []

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='_silver_build_state'").fetchone()
        if not exists:
            # Never recorded, so nothing can be compared. The gate says so too.
            return []

        con.execute("ATTACH DATABASE ? AS bronze",
                    (f"file:{BRONZE_DB_PATH.as_posix()}?mode=ro",))
        behind = []
        for name, at_build in con.execute(
                "SELECT table_name, bronze_rows FROM _silver_build_state"):
            try:
                now = con.execute(
                    f'SELECT COUNT(*) FROM bronze."{name}"').fetchone()[0]
            except sqlite3.Error:
                continue
            if now > at_build:
                behind.append(name)
        return sorted(behind)
    except sqlite3.Error:
        # A rebuild decision is not worth crashing the run over. The gate checks
        # the same thing and will fail loudly if this returned the wrong answer.
        return []
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the F1 Reality Check pipeline.")
    ap.add_argument("--execute", action="store_true",
                    help="apply changes; without this, ingest plans only and nothing is rebuilt")
    ap.add_argument("--force-rebuild", action="store_true",
                    help="rebuild silver even if ingestion found nothing new")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="skip ingestion (useful when rebuilding after a manual fetch)")
    ap.add_argument("--publish", action="store_true",
                    help="publish the serving layer to the GitHub Release the "
                         "dashboard reads from (needs GITHUB_TOKEN)")
    args = ap.parse_args()

    LOGS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runner = Runner(LOGS_DIR / f"pipeline_{stamp}.log")

    mode = "EXECUTE" if args.execute else "DRY RUN"
    runner.log("=" * 74)
    runner.log(f"F1 REALITY CHECK PIPELINE — {mode}")
    runner.log(f"started: {datetime.now(timezone.utc).isoformat()}")
    runner.log("=" * 74)

    overall_started = time.time()
    new_rows = 0

    # --- 1. ingest -------------------------------------------------------------
    if args.skip_ingest:
        runner.log("\nIngestion skipped (--skip-ingest)")
    else:
        code, out = runner.run_step(
            "ingest", "s01_ingest.py", ["--execute"] if args.execute else []
        )
        if code != 0:
            runner.log("\nIngestion reported failures. Continuing — partial data is still")
            runner.log("worth rebuilding, and failed pairs are retried next run.")
        new_rows = rows_inserted(out) if args.execute else 0
        runner.log(f"\nnew rows: {new_rows:,}")

    # --- 2 & 3. rebuild --------------------------------------------------------
    # Two independent triggers. `new_rows` catches per-session fetches; `stale`
    # catches everything that never passes through that counter, which is the
    # global tables and anything s01_backfill.py wrote straight into bronze.
    stale = stale_tables() if args.execute else []
    if stale:
        runner.log(f"\nSilver is behind bronze for: {', '.join(stale)}")
        runner.log("Rebuilding, even though ingest reported no new rows.")

    should_rebuild = args.execute and (new_rows > 0 or args.force_rebuild or stale)

    if not should_rebuild:
        reason = "dry run" if not args.execute else "no new data"
        runner.log(f"\nSkipping silver rebuild and caution flags ({reason}).")
        runner.log("Use --force-rebuild to rebuild anyway.")
    else:
        code, _ = runner.run_step("build_silver", "s02_build_silver.py",
                                  ["--tables"] + REBUILD_TABLES)
        if code != 0:
            runner.log("\nSilver build FAILED — stopping. Downstream steps would run")
            runner.log("against an inconsistent layer.")
            runner.flush()
            return 1

        # Derived from silver_laps and silver_race_control, so it must follow
        # the rebuild or the flags silently go stale.
        code, _ = runner.run_step("caution_flags", "s02b_caution_flags.py")
        if code != 0:
            runner.log("\nCaution flag build FAILED — stopping.")
            runner.flush()
            return 1

    # --- 4. verify -------------------------------------------------------------
    code, out = runner.run_step("verify", "s03_verify.py")
    gate_passed = code == 0

    # --- 5 & 6. serving layers -------------------------------------------------
    # Rebuilt whenever the gate passes, regardless of whether ingestion found
    # anything. They are derived views over silver and take well under a minute,
    # so the cost of an unnecessary rebuild is far lower than the cost of a
    # dashboard that quietly kept showing last week's numbers.
    serving_status = "skipped"
    serving_failed: list[str] = []

    if not args.execute:
        runner.log("\nServing layers skipped (dry run).")
    elif not gate_passed:
        runner.log("\nServing layers SKIPPED — the verification gate reported FAIL.")
        runner.log("Building them now would publish data the gate has already")
        runner.log("rejected, presented as a finished dashboard.")
    else:
        serving_status = "built"
        # GOLD RUNS FIRST, AND THAT ORDER IS LOAD-BEARING.
        #
        # s05_diagnostic reads gold, so a run that rebuilt silver and skipped
        # gold would analyse the previous week's data while reporting success.
        # That is the exact failure this pipeline already has one guard against:
        # silver_lap_flags going stale behind silver (check [17]) and the
        # scheduled task failing silently for four months (NOTES_LOG #43).
        # Gold is fully derived, so rebuilding it every run costs a minute and
        # removes the whole class of problem.
        #
        # It also runs before s04, which now writes its seven tables straight
        # into dashboard.db rather than to CSV.
        for name, script in (("gold", "s07_build_gold.py"),
                             ("descriptive", "s04_descriptive.py"),
                             ("diagnostic", "s05_diagnostic.py")):
            extra = ["--execute"] if name == "gold" else None
            rc, _ = runner.run_step(name, script, extra)
            if rc != 0:
                runner.log(f"\n{name} serving layer FAILED.")
                serving_failed.append(name)
                if name == "gold":
                    runner.log("Gold is the source for the diagnostic layer, so "
                               "the rest would read a stale copy. Stopping here.")
                    break
        if serving_failed:
            serving_status = f"FAILED: {', '.join(serving_failed)}"

    # --- 7. publish ------------------------------------------------------------
    # Opt-in. Requires the serving layers to have been built this run, since
    # publishing means replacing the live dashboard's data — doing that from a
    # stale bundle would push whatever happened to be lying around in
    # dashboard/data/dashboard.db.
    publish_status = "not requested"

    if args.publish:
        if not args.execute:
            publish_status = "skipped (dry run)"
            runner.log("\nPublish skipped — --publish needs --execute.")
        elif serving_status != "built":
            publish_status = "skipped (no fresh serving layer)"
            runner.log("\nPublish SKIPPED — the serving layers were not rebuilt")
            runner.log("this run, so there is nothing verified to publish.")
        else:
            rc, _ = runner.run_step("publish", "s06_publish.py", ["--execute"])
            publish_status = "published" if rc == 0 else "FAILED"
            if rc != 0:
                runner.log("\nPublish FAILED — the dashboard still serves the")
                runner.log("previous data, which is the safe outcome.")
                serving_failed.append("publish")

    elapsed = time.time() - overall_started
    runner.log("")
    runner.log("=" * 74)
    runner.log(f"PIPELINE FINISHED in {elapsed:.1f}s")
    runner.log(f"new rows: {new_rows:,}")
    runner.log(f"rebuild:  {'yes' if should_rebuild else 'skipped'}")
    runner.log(f"gate:     {'PASS' if gate_passed else 'FAIL'}")
    runner.log(f"serving:  {serving_status}")
    runner.log(f"publish:  {publish_status}")
    runner.log(f"log:      {runner.log_path}")
    runner.log("=" * 74)

    runner.flush()

    # Gate failure outranks a serving-layer failure: it says the database itself
    # is not trustworthy, which is the more important thing to surface.
    if not gate_passed:
        return 2
    if serving_failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())