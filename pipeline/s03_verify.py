"""
s03_verify.py — verification gate for the F1 Reality Check pipeline.

Runs as the first stage of every pipeline run. Re-checks the bronze/silver
invariants established during the IDA and diagnostic phases, so the pipeline
fails loudly instead of quietly producing wrong probabilities.

Design notes:
  - Introspects the live schema via PRAGMA rather than trusting the data
    dictionary (which was wrong at least twice: starting_grid scope, the
    session_result duration split).
  - Three tiers: FAIL (pipeline must stop), WARN (known/accepted quirk worth
    re-seeing), INFO (drift monitoring — log these and diff week over week).
  - Exit code 1 on any FAIL so a scheduler can halt the run.
  - Opens the database read-only; cannot modify anything.

Run:  python pipeline\\s03_verify.py       (from project root)
      python s03_verify.py                (from inside pipeline/)
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `import config` work regardless of the current working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import BRONZE_DB_PATH, DB_PATH  # noqa: E402

# --- expected silver tables (18) -------------------------------------------------
EXPECTED_TABLES = [
    "silver_meetings", "silver_sessions", "silver_drivers",
    "silver_laps", "silver_stints", "silver_pit", "silver_position",
    "silver_intervals", "silver_overtakes",
    "silver_starting_grid", "silver_session_result", "silver_race_control",
    "silver_team_radio", "silver_weather",
    "silver_championship_drivers", "silver_championship_teams",
]
# Derived tables, built by s02b_caution_flags.py rather than the silver build.
DERIVED_TABLES = ["silver_caution_periods", "silver_lap_flags"]
# Columns created by the silver build that the data dictionary does NOT document.
# If these go missing, a rebuild silently regressed to the raw mixed-type columns.
REQUIRED_SPLIT_COLUMNS = {
    "silver_session_result": [
        "duration_race_seconds",
        "gap_to_leader_seconds",
        "gap_to_leader_laps",
    ],
    "silver_intervals": [
        "interval_seconds", "interval_laps",
        "gap_to_leader_seconds", "gap_to_leader_laps",
    ],
}

# --- per-endpoint coverage -------------------------------------------------------
# The gap this closes: every check above asks either "is this table empty overall"
# or "do completed races have laps and results". Neither can see an endpoint that
# vanished for a subset of sessions, which is exactly what a swallowed 404 or a
# throttled backfill produces. The gate once passed clean while races had no pit
# data at all.

RACE_SESSION_NAMES = {"Race", "Sprint"}
QUALI_SESSION_NAMES = {"Qualifying", "Sprint Qualifying", "Sprint Shootout"}

ANY, RACE, QUALI, COMPETITIVE = "any", "race", "quali", "competitive"

# Which session kinds should hold rows for each endpoint-backed table.
ENDPOINT_SCOPE = {
    "silver_laps": ANY,
    "silver_position": ANY,
    "silver_weather": ANY,
    "silver_race_control": ANY,
    "silver_stints": ANY,
    "silver_intervals": RACE,
    "silver_overtakes": RACE,
    "silver_starting_grid": QUALI,
    "silver_session_result": COMPETITIVE,
    "silver_pit": RACE,
    "silver_team_radio": ANY,
}

# Tiers, measured 2026-08-10 across 420 completed non-cancelled sessions.
#
# These come from what the data actually looks like, not from what would be
# tidy. A gate that fails on a gap which has always been there is a gate people
# learn to ignore, and a gate that never fails is decoration. So the absolute
# thresholds are set where they hold today, and check_coverage_snapshot below is
# what catches movement.
#
#   STRICT   complete today, and structurally must be. Any gap is an ingestion
#            failure, so FAIL.
#   RAGGED   legitimately absent upstream. Verified 2026-08-10 by asking the API
#            directly: the races with no pit rows and the sessions with no radio
#            answer 404 {"detail":"No results found."}, so the data does not
#            exist rather than having been missed. INFO only.
#   anything else  near-universal with a small known residue, so WARN with the
#            count and leave the judgement to a human.
STRICT_COVERAGE = {
    "silver_position", "silver_weather", "silver_race_control",
    "silver_stints", "silver_intervals", "silver_overtakes",
}
RAGGED_COVERAGE = {"silver_pit", "silver_team_radio"}

# Beside this file, which is the only thing that reads or writes it, and IN GIT
# ON PURPOSE. This is the gate's memory: the run writes it at the end, the next
# run reads it to tell "this table lost 40% of its rows" from "this table always
# looked like that". A baseline that is not committed is a baseline that resets
# to nothing on a fresh clone, and the check goes quiet without failing.
#
# That rules out dashboard/data/, which the data/ rule in .gitignore ignores
# whole. It lived in outputs/ until that folder was emptied of everything else.
SNAPSHOT_PATH = Path(__file__).resolve().parent / "coverage_snapshot.json"

# A null rate has to move by more than this before it is worth a line of output.
# Below it, ordinary week-to-week churn would bury a real signal in noise.
NULL_DRIFT_THRESHOLD = 0.02


class Report:
    def __init__(self) -> None:
        self.fails: list[str] = []
        self.warns: list[str] = []
        self.infos: list[str] = []
        # Held rather than written immediately: see main(). Baking a regressed
        # state into the baseline would hide the regression on the next run.
        self.snapshot: dict | None = None

    def fail(self, msg: str) -> None:
        self.fails.append(msg)
        print(f"  [FAIL] {msg}")

    def warn(self, msg: str) -> None:
        self.warns.append(msg)
        print(f"  [WARN] {msg}")

    def info(self, msg: str) -> None:
        self.infos.append(msg)
        print(f"  [info] {msg}")

    def ok(self, msg: str) -> None:
        print(f"  [ ok ] {msg}")


def q1(con: sqlite3.Connection, sql: str, params=()):
    """Scalar query helper."""
    row = con.execute(sql, params).fetchone()
    return None if row is None else row[0]


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return q1(
        con,
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ) > 0


def columns_of(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]


# --- checks ---------------------------------------------------------------------

def check_tables_present(con, rep: Report) -> None:
    print("\n[1] Silver tables present")
    missing = [t for t in EXPECTED_TABLES if not table_exists(con, t)]
    for t in missing:
        rep.fail(f"missing table: {t}")
    if not missing:
        rep.ok(f"all {len(EXPECTED_TABLES)} silver tables present")

    # Surface any silver_ table that exists but isn't in the expected list.
    actual = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'silver_%'"
    )]
    extra = sorted(set(actual) - set(EXPECTED_TABLES) - set(DERIVED_TABLES))
    if extra:
        rep.warn(f"undocumented silver tables present: {extra}")


def check_lap_flags_fresh(con, rep: Report) -> None:
    """
    silver_lap_flags is DERIVED from silver_laps via s02b_caution_flags.py.
    A silver rebuild does not update it, so it can silently go stale. Row-count
    parity is a cheap proxy for freshness.
    """
    print("\n[17] silver_lap_flags is present and matches silver_laps")
    if not table_exists(con, "silver_lap_flags"):
        rep.fail("silver_lap_flags missing — run pipeline/s02b_caution_flags.py")
        return

    laps = q1(con, "SELECT COUNT(*) FROM silver_laps")
    flags = q1(con, "SELECT COUNT(*) FROM silver_lap_flags")
    if laps != flags:
        rep.fail(
            f"silver_lap_flags has {flags:,} rows vs silver_laps {laps:,} — "
            "stale; rerun s02b_caution_flags.py"
        )
    else:
        rep.ok(f"{flags:,} rows, matches silver_laps")

    if table_exists(con, "silver_caution_periods"):
        for kind, n in con.execute(
            "SELECT kind, COUNT(*) FROM silver_caution_periods GROUP BY kind ORDER BY 2 DESC"
        ):
            rep.info(f"caution periods — {kind}: {n}")

def check_split_columns(con, rep: Report) -> None:
    print("\n[2] Split columns from the silver build (undocumented in the dictionary)")
    for table, needed in REQUIRED_SPLIT_COLUMNS.items():
        if not table_exists(con, table):
            continue
        have = set(columns_of(con, table))
        missing = [c for c in needed if c not in have]
        if missing:
            rep.fail(f"{table} missing split columns: {missing} — silver build regressed?")
        else:
            rep.ok(f"{table}: split columns intact")
        if table == "silver_session_result" and "duration" in have:
            rep.warn(
                "silver_session_result still has a plain `duration` column — "
                "risk of CAST(... AS REAL) silently corrupting JSON values"
            )


def check_row_counts(con, rep: Report) -> None:
    print("\n[3] Row counts (INFO — log these to spot drift week over week)")
    for t in EXPECTED_TABLES:
        if table_exists(con, t):
            n = q1(con, f'SELECT COUNT(*) FROM "{t}"')
            rep.info(f"{t:32s} {n:>12,}")


def check_empty_tables(con, rep: Report) -> None:
    print("\n[4] No silver table is unexpectedly empty")
    empties = [t for t in EXPECTED_TABLES if table_exists(con, t)
               and q1(con, f'SELECT COUNT(*) FROM "{t}"') == 0]
    if empties:
        rep.fail(f"empty silver tables: {empties}")
    else:
        rep.ok("no empty tables")


def check_overtakes_pk(con, rep: Report) -> None:
    print("\n[5] silver_overtakes PK is the 4-column composite")
    if not table_exists(con, "silver_overtakes"):
        return
    dupes = q1(con, """
        SELECT COUNT(*) FROM (
            SELECT 1 FROM silver_overtakes
            GROUP BY session_key, date, overtaking_driver_number, overtaken_driver_number
            HAVING COUNT(*) > 1
        )
    """)
    if dupes:
        rep.fail(f"{dupes} duplicate rows on the 4-column overtakes key")
    else:
        rep.ok("4-column key is unique")

    # Sanity: the 3-column key SHOULD still be violated (~2,018 rows historically).
    three_col = q1(con, """
        SELECT COUNT(*) FROM (
            SELECT 1 FROM silver_overtakes
            GROUP BY session_key, date, overtaking_driver_number
            HAVING COUNT(*) > 1
        )
    """)
    rep.info(f"3-column key collisions (expected — multi-car passes): {three_col:,}")


def check_composite_pk_uniqueness(con, rep: Report) -> None:
    print("\n[6] Composite PK uniqueness on driver-keyed tables")
    specs = [
        ("silver_drivers", ["session_key", "driver_number"]),
        ("silver_laps", ["session_key", "driver_number", "lap_number"]),
        ("silver_stints", ["session_key", "driver_number", "stint_number"]),
        ("silver_session_result", ["session_key", "driver_number"]),
        ("silver_starting_grid", ["session_key", "driver_number"]),
        ("silver_weather", ["session_key", "date"]),
        ("silver_pit", ["session_key", "driver_number", "lap_number"]),
    ]
    clean = True
    for table, keys in specs:
        if not table_exists(con, table):
            continue
        cols = ", ".join(keys)
        dupes = q1(con, f"""
            SELECT COUNT(*) FROM (
                SELECT 1 FROM "{table}" GROUP BY {cols} HAVING COUNT(*) > 1
            )
        """)
        if dupes:
            rep.fail(f"{table}: {dupes} duplicate groups on ({cols})")
            clean = False
        else:
            rep.ok(f"{table}: ({cols}) unique")
    if clean:
        rep.ok("all composite keys hold")


def check_weather_dupes(con, rep: Report) -> None:
    print("\n[7] silver_weather fully-identical duplicate rows (88 in raw)")
    if not table_exists(con, "silver_weather"):
        return
    cols = ", ".join(f'"{c}"' for c in columns_of(con, "silver_weather"))
    total = q1(con, "SELECT COUNT(*) FROM silver_weather")
    distinct = q1(con, f"SELECT COUNT(*) FROM (SELECT DISTINCT {cols} FROM silver_weather)")
    if total != distinct:
        rep.fail(f"silver_weather has {total - distinct} duplicate rows — SELECT DISTINCT not applied")
    else:
        rep.ok(f"no duplicate rows ({total:,} rows, all distinct)")


def check_starting_grid_scope(con, rep: Report) -> None:
    print("\n[8] silver_starting_grid scope (Qualifying / Sprint Qualifying, NOT Race)")
    if not (table_exists(con, "silver_starting_grid") and table_exists(con, "silver_sessions")):
        return
    rows = con.execute("""
        SELECT s.session_name, COUNT(*) AS n
        FROM silver_starting_grid g
        JOIN silver_sessions s ON s.session_key = g.session_key
        GROUP BY s.session_name
        ORDER BY n DESC
    """).fetchall()
    for name, n in rows:
        rep.info(f"starting_grid session_name = {name!r}: {n:,}")
    unexpected = {r[0] for r in rows} - {"Qualifying", "Sprint Qualifying", "Sprint Shootout"}
    if unexpected:
        rep.warn(f"unexpected session_names in starting_grid: {sorted(unexpected)}")
    else:
        rep.ok("scope matches the empirically confirmed Qualifying-only pattern")


def check_stop_duration_coverage(con, rep: Report) -> None:
    print("\n[9] stop_duration coverage by year (zero in 2023, partial 2024+)")
    if not (table_exists(con, "silver_pit") and table_exists(con, "silver_sessions")):
        return
    rows = con.execute("""
        SELECT s.year,
               COUNT(*) AS stops,
               SUM(CASE WHEN p.stop_duration IS NOT NULL THEN 1 ELSE 0 END) AS with_dur
        FROM silver_pit p
        JOIN silver_sessions s ON s.session_key = p.session_key
        GROUP BY s.year ORDER BY s.year
    """).fetchall()
    for year, stops, with_dur in rows:
        pct = 0.0 if not stops else 100.0 * with_dur / stops
        rep.info(f"{year}: {with_dur:,}/{stops:,} stops have stop_duration ({pct:.1f}%)")
        if year == 2023 and with_dur:
            rep.warn("2023 now has stop_duration values — documented gap may have been backfilled")

def check_completed_races_have_data(con, rep: Report) -> None:
    """
    Forward-looking invariant: every non-cancelled Race or Sprint session that
    finished more than 3 days ago must have both laps and results.

    This replaces the earlier checks that asserted a hardcoded list of known gaps
    still existed. All of those were recovered on 2026-07-27 once the ingestion
    resumability bug was fixed, so the list was stale. A live invariant catches
    the next failure automatically instead of only the ones already known.

    The 3-day grace period avoids flagging a race that ran this weekend but whose
    data OpenF1 has not published yet.
    """
    print("\n[10] Completed races have both laps and results")
    needed = ("silver_sessions", "silver_meetings", "silver_laps", "silver_session_result")
    if not all(table_exists(con, t) for t in needed):
        return

    rows = con.execute("""
        SELECT m.year, m.meeting_name, s.session_name, s.session_key,
               (SELECT COUNT(*) FROM silver_laps l
                 WHERE l.session_key = s.session_key) AS laps,
               (SELECT COUNT(*) FROM silver_session_result r
                 WHERE r.session_key = s.session_key) AS results
        FROM silver_sessions s
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        WHERE s.session_name IN ('Race', 'Sprint')
          AND s.is_cancelled = 0
          AND s.date_start < datetime('now', '-3 days')
        ORDER BY s.date_start
    """).fetchall()

    bad = [r for r in rows if r[4] == 0 or r[5] == 0]
    for year, meeting, sname, sk, laps, results in bad:
        missing = []
        if laps == 0:
            missing.append("laps")
        if results == 0:
            missing.append("results")
        rep.fail(f"{year} {meeting} / {sname} (sk={sk}) missing: {', '.join(missing)}")

    if not bad:
        rep.ok(f"all {len(rows)} completed race/sprint sessions have laps and results")

    # Training-set size, logged for drift monitoring.
    usable = con.execute("""
        SELECT m.year, COUNT(DISTINCT m.meeting_key)
        FROM silver_sessions s
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        WHERE s.session_name = 'Race' AND s.is_cancelled = 0
          AND EXISTS (SELECT 1 FROM silver_laps l WHERE l.session_key = s.session_key)
          AND EXISTS (SELECT 1 FROM silver_session_result r WHERE r.session_key = s.session_key)
        GROUP BY m.year ORDER BY m.year
    """).fetchall()
    for year, n in usable:
        rep.info(f"{year}: {n} usable races (laps + results)")


def check_orphans(con, rep: Report) -> None:
    print("\n[12] Referential integrity — session_key orphans")
    children = [t for t in EXPECTED_TABLES if t not in ("silver_meetings", "silver_sessions")]
    found = False
    for t in children:
        if not table_exists(con, t) or "session_key" not in columns_of(con, t):
            continue
        n = q1(con, f"""
            SELECT COUNT(*) FROM "{t}" c
            LEFT JOIN silver_sessions s ON s.session_key = c.session_key
            WHERE s.session_key IS NULL
        """)
        if n:
            rep.fail(f"{t}: {n:,} rows reference a session_key not in silver_sessions")
            found = True
    if not found:
        rep.ok("no session_key orphans")


def check_team_name_drift(con, rep: Report) -> None:
    print("\n[13] Team name drift")
    if not table_exists(con, "silver_drivers"):
        return
    names = [r[0] for r in con.execute("""
        SELECT DISTINCT team_name FROM silver_drivers
        WHERE team_name IS NOT NULL ORDER BY team_name
    """)]
    rep.info(f"{len(names)} distinct raw team_name values: {names}")
    rep.warn("apply normalize_team_names() before any multi-year team grouping")


def check_null_team_name(con, rep: Report) -> None:
    print("\n[14] NULL team_name (1 known session: 2023 Hungarian GP Practice 1)")
    if not table_exists(con, "silver_drivers"):
        return
    n = q1(con, "SELECT COUNT(*) FROM silver_drivers WHERE team_name IS NULL")
    if not n:
        rep.ok("no NULL team_name rows")
        return

    # Counted per session, not per row. The old message reported 14 rows against
    # "1 session known" and read as though the problem had grown thirteenfold,
    # when all 14 are the same documented session (verified 2026-08-10).
    sessions = con.execute("""
        SELECT s.year, m.meeting_name, s.session_name, COUNT(*)
        FROM silver_drivers d
        JOIN silver_sessions s ON s.session_key = d.session_key
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        WHERE d.team_name IS NULL
        GROUP BY d.session_key ORDER BY s.date_start
    """).fetchall()

    if len(sessions) == 1:
        year, meeting, sname, rows = sessions[0]
        rep.warn(f"{n} rows with NULL team_name, all in the 1 known session "
                 f"({year} {meeting} / {sname})")
    else:
        rep.warn(f"{n} rows with NULL team_name across {len(sessions)} sessions "
                 "(1 known) — investigate the rest")
        for year, meeting, sname, rows in sessions:
            rep.warn(f"    {year} {meeting} / {sname}: {rows} rows")


def check_temporal_coverage(con, rep: Report) -> None:
    print("\n[15] Temporal coverage — latest ingested session")
    if not table_exists(con, "silver_sessions"):
        return
    for year, n in con.execute(
        "SELECT year, COUNT(*) FROM silver_sessions GROUP BY year ORDER BY year"
    ):
        rep.info(f"{year}: {n} sessions")
    latest = con.execute("""
        SELECT s.date_start, m.meeting_name, s.session_name
        FROM silver_sessions s
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        ORDER BY s.date_start DESC LIMIT 1
    """).fetchone()
    if latest:
        rep.info(f"latest session: {latest[0]} — {latest[1]} / {latest[2]}")

def check_cadillac_exclusion(con, rep: Report) -> None:
    """
    Cadillac is excluded from comparative analyses and from the model.

    The original reason was sample size (n was 8-10 early in 2026). That no
    longer holds -- 11 races, 61 sessions with results as of 2026-07-27. The
    exclusion now rests on a structural reason instead: Cadillac is a new-for-2026
    constructor with no presence in the 2023-25 training data, so every trailing
    feature (rolling DNF rate, wet_advantage, prior-season pace) is undefined for
    them, and the model has never observed them.

    This is a modelling constraint, not a data problem, so it reports as INFO.
    """
    print("\n[16] Cadillac scope (excluded — no training-set history)")
    if not table_exists(con, "silver_drivers"):
        return
    races = q1(con, """
        SELECT COUNT(DISTINCT s.meeting_key)
        FROM silver_drivers d
        JOIN silver_sessions s ON s.session_key = d.session_key
        WHERE d.team_name LIKE '%Cadillac%'
          AND s.session_name = 'Race'
          AND EXISTS (SELECT 1 FROM silver_session_result r
                       WHERE r.session_key = d.session_key)
    """)
    rep.info(f"Cadillac has {races} completed races, all in 2026 (test period only)")
    rep.info("excluded from the model: no 2023-25 history, trailing features undefined")

def completed_sessions(con) -> list[tuple]:
    """
    Sessions that ran, were not cancelled, and are old enough to have published.

    The 3-day grace matches check_completed_races_have_data: without it, a race
    that ran this weekend is reported as a coverage gap every Monday.
    """
    return con.execute("""
        SELECT s.session_key, s.year, s.session_name, m.meeting_name
        FROM silver_sessions s
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        WHERE s.is_cancelled = 0
          AND s.date_start < datetime('now', '-3 days')
        ORDER BY s.date_start
    """).fetchall()


def in_scope(scope: str, session_name: str) -> bool:
    if scope == ANY:
        return True
    if scope == RACE:
        return session_name in RACE_SESSION_NAMES
    if scope == QUALI:
        return session_name in QUALI_SESSION_NAMES
    if scope == COMPETITIVE:
        # Pre-season testing days produce no classification.
        return not session_name.startswith("Day ")
    raise ValueError(f"unknown scope {scope!r}")


def null_fractions(con, table: str) -> dict[str, float]:
    """
    Fraction of NULLs per column, in one pass.

    Costs roughly 6 to 8 seconds across all of silver, which is proportionate
    for something that runs once per pipeline run and would otherwise let a
    column quietly empty itself between rebuilds.
    """
    cols = columns_of(con, table)
    if not cols:
        return {}
    expr = ", ".join(f'SUM("{c}" IS NULL)' for c in cols)
    row = con.execute(f'SELECT COUNT(*), {expr} FROM "{table}"').fetchone()
    total = row[0]
    if not total:
        return {c: 1.0 for c in cols}
    # Rounded so float noise cannot manufacture a diff.
    return {c: round(row[i + 1] / total, 4) for i, c in enumerate(cols)}


def check_endpoint_coverage(con, rep: Report) -> None:
    print("\n[18] Per-endpoint coverage — in-scope sessions holding zero rows")
    if not table_exists(con, "silver_sessions"):
        return

    sessions = completed_sessions(con)
    rep.info(f"completed, non-cancelled sessions in scope: {len(sessions)}")

    for table, scope in ENDPOINT_SCOPE.items():
        if not table_exists(con, table):
            rep.fail(f"{table} missing entirely")
            continue

        have = {r[0] for r in con.execute(
            f'SELECT DISTINCT session_key FROM "{table}"')}
        scoped = [s for s in sessions if in_scope(scope, s[2])]
        missing = [s for s in scoped if s[0] not in have]

        if not missing:
            rep.ok(f"{table:24s} {len(scoped):>4} in scope, all present")
            continue

        years: dict[int, int] = {}
        kinds: dict[str, int] = {}
        for _, year, name, _ in missing:
            years[year] = years.get(year, 0) + 1
            kinds[name] = kinds.get(name, 0) + 1
        detail = (f"{len(missing)} of {len(scoped)} {scope} sessions have no rows"
                  f" | by year {dict(sorted(years.items()))}"
                  f" | by kind {dict(sorted(kinds.items(), key=lambda kv: -kv[1]))}")

        if table in STRICT_COVERAGE:
            rep.fail(f"{table}: {detail}")
            for _, year, name, meeting in missing[:8]:
                rep.fail(f"    {year} {meeting} / {name}")
        elif table in RAGGED_COVERAGE:
            rep.info(f"{table}: {detail} (absent upstream, not a fetch failure)")
        else:
            rep.warn(f"{table}: {detail}")
            for _, year, name, meeting in missing[:8]:
                rep.warn(f"    {year} {meeting} / {name}")


def check_silver_matches_bronze(con, rep: Report) -> None:
    """
    Is silver actually built from the bronze that exists now?

    The failure this catches is invisible by every other measure. s01_backfill.py
    writes into bronze and is not a pipeline step, so run_pipeline never sees the
    new rows: it decides whether to rebuild from what s01_ingest reports. Silver
    then sits behind bronze, every table is present, every key is unique, every
    invariant here passes, and the numbers are simply built on less data than the
    project holds.

    It has happened once already. A backfill on 2026-07-27 recovered 324,207 rows
    into bronze; the diagnostic notebooks were run against a silver without them
    and their stored conclusions drifted from the dashboard's.

    Comparing row counts directly would not work, because the silver build types,
    dedupes and filters, so silver is legitimately smaller by a ratio nobody has
    written down. Instead s02_build_silver records the bronze count it read, and
    this compares that against bronze now. Equal means current. Larger means a
    rebuild is owed.
    """
    print("\n[20] Silver is built from the bronze that exists now")

    if not BRONZE_DB_PATH.exists():
        rep.info(f"bronze not found at {BRONZE_DB_PATH.name}; skipping")
        return

    if not table_exists(con, "_silver_build_state"):
        rep.info("no build state recorded yet. Run pipeline\\s02_build_silver.py "
                 "once to establish the baseline; until then a silver lagging "
                 "bronze cannot be detected.")
        return

    try:
        con.execute("ATTACH DATABASE ? AS bronze",
                    (f"file:{BRONZE_DB_PATH.as_posix()}?mode=ro",))
    except sqlite3.Error as exc:
        rep.warn(f"could not attach bronze ({exc}); skipping the staleness check")
        return

    try:
        recorded = con.execute("""
            SELECT table_name, bronze_rows, silver_rows, built_at
            FROM _silver_build_state ORDER BY table_name
        """).fetchall()

        stale = []
        for name, bronze_at_build, silver_rows, built_at in recorded:
            try:
                now = con.execute(
                    f'SELECT COUNT(*) FROM bronze."{name}"').fetchone()[0]
            except sqlite3.Error:
                rep.warn(f"{name}: recorded in build state but not in bronze")
                continue

            if now > bronze_at_build:
                stale.append((name, bronze_at_build, now))
                rep.fail(
                    f"{name}: bronze has {now:,} rows, silver was built from "
                    f"{bronze_at_build:,} ({now - bronze_at_build:+,}), "
                    f"last built {built_at}"
                )
            elif now < bronze_at_build:
                # Bronze does not normally shrink. Worth seeing, but it does not
                # mean silver is missing anything.
                rep.warn(f"{name}: bronze has {now:,} rows but silver was built "
                         f"from {bronze_at_build:,}; did bronze get pruned?")

        if stale:
            rep.fail("silver is behind bronze. Rebuild with: "
                     "python pipeline\\s02_build_silver.py --tables "
                     + " ".join(n for n, _, _ in stale))
        else:
            rep.ok(f"all {len(recorded)} recorded tables match bronze")

        # A table that has never been recorded is a blind spot, not a failure:
        # it simply has not been rebuilt since this check was introduced.
        known = {r[0] for r in recorded}
        missing = sorted(t for t in EXPECTED_TABLES
                         if t.removeprefix("silver_") not in known)
        if missing:
            rep.info(f"{len(missing)} table(s) have no build state yet: "
                     + ", ".join(m.removeprefix('silver_') for m in missing))
    finally:
        con.execute("DETACH DATABASE bronze")


def check_coverage_snapshot(con, rep: Report) -> None:
    """
    Compare this run's coverage and null rates against the previous run's.

    This is the part that survives the calendar growing. An absolute threshold
    rots: every new season shifts it, so it gets raised until it means nothing.
    A diff does not, because the question it asks is "did something that used to
    have data stop having data", and the answer is unambiguous.

    Written by main() only when nothing failed, so a bad run cannot become the
    baseline that hides itself next time.
    """
    print("\n[19] Coverage and null snapshot, diffed against the previous run")

    sessions = completed_sessions(con)
    tables: dict[str, dict] = {}
    for table in sorted(set(EXPECTED_TABLES + DERIVED_TABLES)):
        if not table_exists(con, table):
            continue
        entry: dict = {
            "rows": q1(con, f'SELECT COUNT(*) FROM "{table}"'),
            "nulls": null_fractions(con, table),
        }
        scope = ENDPOINT_SCOPE.get(table)
        if scope:
            have = {r[0] for r in con.execute(
                f'SELECT DISTINCT session_key FROM "{table}"')}
            scoped = [s for s in sessions if in_scope(scope, s[2])]
            entry["scope"] = scope
            entry["in_scope"] = len(scoped)
            entry["missing_sessions"] = sum(
                1 for s in scoped if s[0] not in have)
        tables[table] = entry

    payload = {
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sessions_in_scope": len(sessions),
        "tables": tables,
    }
    rep.snapshot = payload

    if not SNAPSHOT_PATH.exists():
        rep.info(f"no previous snapshot at {SNAPSHOT_PATH.name}; "
                 "this run becomes the baseline")
        return

    try:
        previous = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        rep.warn(f"previous snapshot unreadable ({type(exc).__name__}), "
                 "treating this run as a new baseline")
        return

    rep.info(f"comparing against {previous.get('written_at', 'unknown time')}")
    old_tables = previous.get("tables", {})

    gone = sorted(set(old_tables) - set(tables))
    if gone:
        rep.fail(f"tables present last run and missing now: {gone}")
    added = sorted(set(tables) - set(old_tables))
    if added:
        rep.info(f"new tables since last run: {added}")

    changed = False
    for table in sorted(set(tables) & set(old_tables)):
        now, before = tables[table], old_tables[table]

        # The regression this whole check exists for.
        old_missing = before.get("missing_sessions")
        new_missing = now.get("missing_sessions")
        if old_missing is not None and new_missing is not None:
            if new_missing > old_missing:
                rep.fail(f"{table}: sessions with no rows rose "
                         f"{old_missing} -> {new_missing}")
                changed = True
            elif new_missing < old_missing:
                rep.info(f"{table}: coverage improved, sessions with no rows "
                         f"fell {old_missing} -> {new_missing}")
                changed = True

        # A shrinking table is usually a deliberate dedupe, occasionally a
        # rebuild that lost rows. Worth seeing, not worth stopping the pipeline.
        old_rows, new_rows = before.get("rows"), now.get("rows")
        if isinstance(old_rows, int) and isinstance(new_rows, int):
            if new_rows < old_rows:
                rep.warn(f"{table}: row count fell {old_rows:,} -> {new_rows:,}")
                changed = True
            elif new_rows > old_rows:
                rep.info(f"{table}: {new_rows - old_rows:+,} rows "
                         f"({old_rows:,} -> {new_rows:,})")
                changed = True

        old_nulls = before.get("nulls", {})
        for col, frac in sorted(now.get("nulls", {}).items()):
            if col not in old_nulls:
                rep.info(f"{table}.{col}: new column, {frac:.1%} null")
                changed = True
                continue
            delta = frac - old_nulls[col]
            if abs(delta) > NULL_DRIFT_THRESHOLD:
                rep.warn(f"{table}.{col}: null rate {old_nulls[col]:.1%} -> "
                         f"{frac:.1%} ({delta:+.1%})")
                changed = True
        for col in sorted(set(old_nulls) - set(now.get("nulls", {}))):
            rep.warn(f"{table}.{col}: column has disappeared")
            changed = True

    if not changed:
        rep.ok("nothing moved since the previous run")


# --- main ------------------------------------------------------------------------

def check_unflagged_field_slowdowns(con, rep: Report) -> None:
    """
    Laps where the whole field ran slowly and nothing was flagged.

    WHY THIS EXISTS. Caution periods are built from race control messages, and
    three separate bugs have now been found where the message was there but
    spelled in a way the parser did not match: 'RED FLAG ...' as prose, VSC
    under two names, and a race starting behind the safety car with no
    deployment message at all. Each one left real neutralised laps recorded as
    green-flag racing, and each was invisible until someone went looking.

    A missed caution has a signature that does not depend on knowing the
    vocabulary: the ENTIRE FIELD slows at once. That is what this measures, so
    the next spelling shows up as a number instead of waiting to be stumbled on.

    Deliberately a WARN, not a FAIL. A field-wide slowdown is not proof of a
    caution. Rain slows everyone too, and Zandvoort 2023 laps 1-3 look identical
    on the median while the per-car spread gives it away: under a safety car the
    field is bunched, in the wet it disperses as cars pit at different times.
    Distinguishing them needs a human, so this reports rather than blocks.

    Reference pace is the median of laps that ARE flagged green and are not
    pit-out laps, taken over the whole session. That trusts the flags, but only
    on the laps not in question, and only in aggregate.

    SCOPED TO RACES since 2026-08-13. It covered Sprints too, but nothing in
    this project analyses a Sprint: gold's race scope, the 29 tests and the
    dashboard all filter to session_name = 'Race'. So Sprint findings could not
    affect a published number, and they sat untriaged for weeks while looking
    like real work. Two of them (Austin 2025 laps 18-19 at 1.50x, Miami 2025
    lap 3 at 1.44x) are genuine and still in the data. Widen this back to
    ('Race', 'Sprint') when the Sprint phase starts, and triage them then.

    WHAT THIS LIST SHOULD LOOK LIKE. As of 2026-08-13 it holds 16 lap-events and
    every one is accounted for, so a NEW entry means something changed:

      3   the safety car withdrawal bug, still open (NOTES_LOG #52 family B):
          Jeddah 2023 lap 20, Montreal 2024 laps 29 and 58
      13  confirmed rain, not cautions: Monte Carlo 2023 laps 54-63 and
          Zandvoort 2023 laps 1-3

    The rain verdict was checked rather than assumed, because these two look
    like cautions on the spread test: at Monaco nobody can overtake in the wet,
    so the field queues and appears bunched. What settles it is the tyres. Monte
    Carlo went from 12 cars on HARD and 2 on INTERMEDIATE to 0 and 18, with WET
    appearing after; Zandvoort went from 19 on SOFT and 0 on INTERMEDIATE at lap
    1 to 5 and 15 by lap 5. Cars do not change tyres because a safety car came
    out. Monte Carlo also has zero caution periods in the whole race, and its
    pace decays smoothly rather than stepping and holding.
    """
    print("\n[21] No unexplained field-wide slowdowns left unflagged")
    if not table_exists(con, "silver_lap_flags"):
        rep.warn("silver_lap_flags missing, cannot check field slowdowns")
        return

    # Medians throughout, never means. One car parked at the side of the road
    # drags a mean far enough to invent a field-wide event, which is the exact
    # mistake the rejected RESTART_FACTOR rule made (NOTES_LOG #47). SQLite has
    # no median aggregate, hence the row_number / count window pair.
    rows = con.execute("""
        WITH lap AS (
            SELECT l.session_key, l.lap_number, l.lap_duration,
                   COALESCE(l.is_pit_out_lap, 0) AS pit_out, f.neutralised
            FROM silver_laps l
            JOIN silver_lap_flags f
              ON f.session_key = l.session_key
             AND f.driver_number = l.driver_number
             AND f.lap_number = l.lap_number
            JOIN silver_sessions s ON s.session_key = l.session_key
            WHERE s.session_name = 'Race'
              AND l.lap_duration IS NOT NULL
        ),
        green_ranked AS (
            SELECT session_key, lap_duration,
                   ROW_NUMBER() OVER (PARTITION BY session_key
                                      ORDER BY lap_duration) AS rn,
                   COUNT(*)     OVER (PARTITION BY session_key) AS cnt
            FROM lap WHERE neutralised = 0 AND pit_out = 0 AND lap_number >= 5
        ),
        ref AS (
            SELECT session_key, AVG(lap_duration) AS green
            FROM green_ranked
            WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2)
            GROUP BY session_key
        ),
        lap_ranked AS (
            SELECT session_key, lap_number, lap_duration, neutralised,
                   ROW_NUMBER() OVER (PARTITION BY session_key, lap_number
                                      ORDER BY lap_duration) AS rn,
                   COUNT(*)     OVER (PARTITION BY session_key,
                                      lap_number) AS cnt
            FROM lap
        ),
        per_lap AS (
            SELECT session_key, lap_number, MAX(cnt) AS cars,
                   AVG(CASE WHEN rn IN ((cnt + 1) / 2, (cnt + 2) / 2)
                            THEN lap_duration END) AS med,
                   AVG(neutralised) AS flagged
            FROM lap_ranked GROUP BY session_key, lap_number
        )
        SELECT p.session_key, p.lap_number, p.cars, p.med / ref.green AS ratio,
               p.flagged
        FROM per_lap p JOIN ref USING (session_key)
        WHERE p.cars >= 10 AND p.med / ref.green >= 1.25 AND p.flagged < 0.5
        ORDER BY ratio DESC
    """).fetchall()

    sessions = len({r[0] for r in rows})
    if not rows:
        rep.ok("no field-wide slowdown runs unflagged")
        return

    rep.warn(
        f"{len(rows)} lap-event(s) across {sessions} session(s) where 10+ cars "
        f"ran at 1.25x their own green pace with under half flagged. Check "
        f"for a caution spelling the parser misses"
    )
    for session_key, lap_number, cars, ratio, flagged in rows[:12]:
        rep.info(f"session {session_key} lap {lap_number}: {cars} cars at "
                 f"{ratio:.2f}x, {flagged:.0%} flagged")


def check_overflagged_racing_laps(con, rep: Report) -> None:
    """
    The mirror of [21]: laps flagged as neutralised that ran at racing pace.

    WHY THIS EXISTS. Check [21] looks for cautions the parser MISSED. Nothing
    looked for the opposite, and every caution fix so far has been validated by
    throwaway scripts that no longer exist. On 2026-08-13 a one-line widening of
    the restart-procedure rule extended Melbourne 2023's first red flag period
    across laps 10 to 26 and flagged 697 racing laps as neutralised. The gate
    reported PASS. It was caught by an ad-hoc script, which is not a control.

    Over-flagging is the more dangerous direction. A missed caution leaves a
    conspicuously slow lap in the green population, which check [21] finds and a
    reader might notice. A wrongly flagged lap simply DISAPPEARS from every
    analysis, and nothing downstream can tell it apart from a real one.

    A FAIL, not a WARN, unlike [21]. A field-wide slowdown has innocent
    explanations, so [21] reports. A neutralised lap at full racing pace does
    not: either the flag is wrong or the pace reference is, and both need fixing
    before the numbers are trusted.

    The threshold is deliberately loose. This is not trying to grade borderline
    restart laps, which legitimately sit a few percent off green pace; it is
    trying to catch a rule that has run away. Only laps at or FASTER than the
    session's own green median count, since a genuinely neutralised lap can
    never be quicker than the racing it replaced.
    """
    print("\n[22] No laps flagged as neutralised while running at racing pace")
    if not table_exists(con, "silver_lap_flags"):
        rep.warn("silver_lap_flags missing, cannot check over-flagging")
        return

    # Medians, for the same reason as [21]. Pit-out and pit-in laps are excluded
    # from the reference but a flagged lap is judged on its own duration.
    rows = con.execute("""
        WITH lap AS (
            SELECT l.session_key, l.lap_number, l.driver_number, l.lap_duration,
                   COALESCE(l.is_pit_out_lap, 0) AS pit_out, f.neutralised
            FROM silver_laps l
            JOIN silver_lap_flags f
              ON f.session_key = l.session_key
             AND f.driver_number = l.driver_number
             AND f.lap_number = l.lap_number
            JOIN silver_sessions s ON s.session_key = l.session_key
            WHERE s.session_name IN ('Race', 'Sprint')
              AND l.lap_duration IS NOT NULL
        ),
        green_ranked AS (
            SELECT session_key, lap_duration,
                   ROW_NUMBER() OVER (PARTITION BY session_key
                                      ORDER BY lap_duration) AS rn,
                   COUNT(*)     OVER (PARTITION BY session_key) AS cnt
            FROM lap WHERE neutralised = 0 AND pit_out = 0 AND lap_number >= 5
        ),
        ref AS (
            SELECT session_key, AVG(lap_duration) AS green
            FROM green_ranked
            WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2)
            GROUP BY session_key
        )
        SELECT l.session_key, l.lap_number, COUNT(*) AS cars,
               AVG(l.lap_duration / ref.green) AS ratio
        FROM lap l JOIN ref USING (session_key)
        WHERE l.neutralised = 1 AND l.pit_out = 0
          AND l.lap_duration <= ref.green
        GROUP BY l.session_key, l.lap_number
        HAVING COUNT(*) >= 5
        ORDER BY cars DESC
    """).fetchall()

    if not rows:
        rep.ok("no neutralised lap-event runs at or above green pace")
        return

    total = sum(r[2] for r in rows)
    sessions = len({r[0] for r in rows})
    rep.fail(
        f"{total} neutralised lap(s) across {len(rows)} lap-event(s) in "
        f"{sessions} session(s) ran at or faster than their session's green "
        f"median. A caution rule is over-reaching"
    )
    for session_key, lap_number, cars, ratio in rows[:12]:
        rep.info(f"session {session_key} lap {lap_number}: {cars} cars at "
                 f"{ratio:.2f}x, flagged neutralised")


CHECKS = [
    check_tables_present,
    check_split_columns,
    check_row_counts,
    check_empty_tables,
    check_overtakes_pk,
    check_composite_pk_uniqueness,
    check_weather_dupes,
    check_starting_grid_scope,
    check_stop_duration_coverage,
    check_completed_races_have_data,
    check_orphans,
    check_team_name_drift,
    check_null_team_name,
    check_temporal_coverage,
    check_cadillac_exclusion,
    check_lap_flags_fresh,
    check_unflagged_field_slowdowns,
    check_overflagged_racing_laps,
    check_endpoint_coverage,
    check_silver_matches_bronze,
    # Last, so the snapshot it builds reflects everything the run has seen.
    check_coverage_snapshot,
]


def main() -> int:
    if not DB_PATH.exists():
        print(f"[FAIL] database not found at {DB_PATH}")
        return 1

    print("=" * 74)
    print("F1 REALITY CHECK — LAYER VERIFICATION GATE")
    print(f"database: {DB_PATH}")
    print("=" * 74)

    rep = Report()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        for check in CHECKS:
            try:
                check(con, rep)
            except Exception as exc:  # a broken check shouldn't kill the whole gate
                rep.fail(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    finally:
        con.close()

    print("\n" + "=" * 74)
    print(f"SUMMARY: {len(rep.fails)} FAIL | {len(rep.warns)} WARN | {len(rep.infos)} INFO")
    print("=" * 74)
    if rep.fails:
        print("\nPipeline should NOT proceed. Failures:")
        for f in rep.fails:
            print(f"  - {f}")
        # Deliberately NOT written. Overwriting the baseline with a regressed
        # state would make the next run compare bad against bad and report
        # everything as fine, which is the failure mode this check exists to
        # prevent.
        print(f"\n{SNAPSHOT_PATH.name} left unchanged, so the next run still "
              "compares against the last known-good state.")
        return 1

    if rep.snapshot is not None:
        SNAPSHOT_PATH.write_text(
            json.dumps(rep.snapshot, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\nCoverage snapshot written to "
              f"{SNAPSHOT_PATH.relative_to(SNAPSHOT_PATH.parents[1])}")
    print("\nGate passed — safe to proceed to silver rebuild / feature build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())