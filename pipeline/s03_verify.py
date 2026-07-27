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

import sqlite3
import sys
from pathlib import Path

# Make `import config` work regardless of the current working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH  # noqa: E402

# --- expected silver tables (18) -------------------------------------------------
EXPECTED_TABLES = [
    "silver_meetings", "silver_sessions", "silver_drivers",
    "silver_laps", "silver_stints", "silver_pit", "silver_position",
    "silver_intervals", "silver_overtakes",
    "silver_starting_grid", "silver_session_result", "silver_race_control",
    "silver_team_radio", "silver_weather",
    "silver_championship_drivers", "silver_championship_teams",
    "silver_car_data", "silver_location",
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

class Report:
    def __init__(self) -> None:
        self.fails: list[str] = []
        self.warns: list[str] = []
        self.infos: list[str] = []

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
    else:
        rep.warn(f"{n} rows with NULL team_name (1 session known — investigate if higher)")


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

# --- main ------------------------------------------------------------------------

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
        return 1
    print("\nGate passed — safe to proceed to silver rebuild / feature build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())