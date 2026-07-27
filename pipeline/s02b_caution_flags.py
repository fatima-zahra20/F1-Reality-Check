"""
s02b_caution_flags.py — derives race neutralisation periods and per-lap flags.

Why this exists
---------------
data_prep.load_laps() built caution_flag by joining silver_race_control on an
exact (session_key, lap_number) match, treating SafetyCar rows and
YELLOW/DOUBLE YELLOW/RED flags identically. Two problems:

  1. OVER-FLAGGING. Every yellow in the database is sector-scoped (1,607 YELLOW
     + 1,684 DOUBLE YELLOW, zero track-scoped). Those are localised marshal
     warnings costing a fraction of a second in one sector, not race-wide
     neutralisations. They were ~85% of everything the old flag caught.

  2. UNDER-FLAGGING. Safety Car periods span several laps but are typically
     announced once, and lap_number is NULL on 22% of SC and 56% of VSC
     deployment messages. So most neutralised laps were passing through
     unflagged — while being 30-50% slower than normal.

Since session-normalized pace is the strongest feature in the prediction model,
contamination here degrades the thing the model most depends on.

Also: VSC was never missing from the data. It lives inside category='SafetyCar'
under two spellings ('VIRTUAL SAFETY CAR ...' and 'VSC ...'), so previous
analyses counted VSC as a full Safety Car. A VSC is far less disruptive, so the
two are separated here.

What it builds
--------------
silver_caution_periods — one row per neutralisation, with kind (SC / VSC / RED)
and a start/end timestamp window.

silver_lap_flags — one row per lap, with independent boolean flags. Filtering
becomes an explicit modelling choice rather than one blunt indicator.

Both are DERIVED tables: they depend on silver_race_control, silver_sessions and
silver_laps, so they must be rebuilt after s02_build_silver.py.

Usage
-----
    python pipeline\\s02b_caution_flags.py
    python pipeline\\s02b_caution_flags.py --validate    # extra evidence output
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH  # noqa: E402

# A GREEN track flag within this window after an "ending" message is treated as
# the real restart. "SAFETY CAR IN THIS LAP" is announced during the lap BEFORE
# the restart, so the message timestamp alone would end the period too early.
GREEN_LOOKAHEAD_SECONDS = 300

# Fallback lap length when lap_duration is NULL and there is no following lap.
DEFAULT_LAP_SECONDS = 120


def classify(message: str) -> tuple[str | None, str | None]:
    """Returns (kind, phase) for a SafetyCar-category message."""
    m = message.upper()
    kind = "VSC" if ("VIRTUAL" in m or "VSC" in m) else "SC"

    if "DEPLOYED" in m:
        return kind, "start"
    if "ENDING" in m or "IN THIS LAP" in m:
        return kind, "end"
    if "THROUGH THE PIT LANE" in m:
        # Occurs during an already-open period. Treated as a start only if
        # nothing is currently open (handled by the caller).
        return kind, "start"
    return None, None


def build_periods(con: sqlite3.Connection) -> pd.DataFrame:
    """Pairs deployment messages with their closing messages, per session."""
    rc = pd.read_sql("""
        SELECT rc.session_key, rc."date", rc.category, rc.flag, rc.scope, rc.message,
               s.date_end AS session_end
        FROM silver_race_control rc
        JOIN silver_sessions s ON s.session_key = rc.session_key
        WHERE rc.category = 'SafetyCar'
           OR (rc.category = 'Flag' AND rc.flag IN ('RED', 'GREEN') AND rc.scope = 'Track')
        ORDER BY rc.session_key, rc."date"
    """, con)

    if rc.empty:
        return pd.DataFrame()

    rc["date"] = pd.to_datetime(rc["date"], format="ISO8601", utc=True)
    rc["session_end"] = pd.to_datetime(rc["session_end"], format="ISO8601", utc=True)

    periods = []

    for session_key, grp in rc.groupby("session_key", sort=False):
        grp = grp.sort_values("date")
        session_end = grp["session_end"].iloc[0]

        greens = grp.loc[
            (grp["category"] == "Flag") & (grp["flag"] == "GREEN"), "date"
        ].tolist()

        open_period = None

        def close(period, end_ts, closed_by):
            period["date_end"] = end_ts
            period["closed_by"] = closed_by
            periods.append(period)

        for _, row in grp.iterrows():
            ts = row["date"]

            # --- red flag: session suspended -------------------------------
            if row["category"] == "Flag" and row["flag"] == "RED":
                if open_period is not None:
                    close(open_period, ts, "superseded_by_red")
                    open_period = None
                nxt = [g for g in greens if g > ts]
                periods.append({
                    "session_key": session_key,
                    "kind": "RED",
                    "date_start": ts,
                    "date_end": nxt[0] if nxt else session_end,
                    "start_message": row["message"],
                    "closed_by": "green_flag" if nxt else "session_end",
                })
                continue

            if row["category"] != "SafetyCar":
                continue

            kind, phase = classify(row["message"])
            if kind is None:
                continue

            if phase == "start":
                # "THROUGH THE PIT LANE" during an open period is not a new
                # deployment.
                if open_period is not None and "THROUGH THE PIT LANE" in row["message"].upper():
                    continue
                # An escalation (VSC -> SC) closes the previous period.
                if open_period is not None:
                    close(open_period, ts, f"escalated_to_{kind}")
                open_period = {
                    "session_key": session_key,
                    "kind": kind,
                    "date_start": ts,
                    "start_message": row["message"],
                }

            elif phase == "end" and open_period is not None:
                # The announcement precedes the actual restart; prefer the next
                # GREEN track flag if one follows soon after.
                nxt = [
                    g for g in greens
                    if ts <= g <= ts + pd.Timedelta(seconds=GREEN_LOOKAHEAD_SECONDS)
                ]
                if nxt:
                    close(open_period, nxt[0], "green_flag")
                else:
                    close(open_period, ts, "end_message")
                open_period = None

        # Deployments with no closing message — a race finishing under Safety
        # Car, or one superseded by session end.
        if open_period is not None:
            close(open_period, session_end, "session_end")

    df = pd.DataFrame(periods)
    if df.empty:
        return df

    df = df.sort_values(["session_key", "date_start"]).reset_index(drop=True)
    df["period_id"] = range(1, len(df) + 1)
    df["duration_seconds"] = (df["date_end"] - df["date_start"]).dt.total_seconds()

    # A period running to session end can be absurdly long if the closing
    # message was simply never logged; flag rather than silently trust.
    return df


def build_lap_flags(con: sqlite3.Connection, periods: pd.DataFrame) -> pd.DataFrame:
    """Flags each lap whose time window overlaps a caution period."""
    laps = pd.read_sql("""
        SELECT session_key, driver_number, lap_number, date_start, lap_duration
        FROM silver_laps
        ORDER BY session_key, driver_number, lap_number
    """, con)

    laps["date_start"] = pd.to_datetime(laps["date_start"], format="ISO8601", utc=True)

    # Lap end: prefer date_start + lap_duration; else the next lap's start;
    # else a default length.
    laps["next_start"] = laps.groupby(
        ["session_key", "driver_number"], sort=False
    )["date_start"].shift(-1)

    laps["lap_end"] = laps["date_start"] + pd.to_timedelta(laps["lap_duration"], unit="s")
    laps["lap_end"] = laps["lap_end"].fillna(laps["next_start"])
    laps["lap_end"] = laps["lap_end"].fillna(
        laps["date_start"] + pd.Timedelta(seconds=DEFAULT_LAP_SECONDS)
    )

    for col in ("sc_flag", "vsc_flag", "red_flag"):
        laps[col] = 0

    # Laps with no date_start cannot be placed in time — mark unknown rather
    # than falsely clean.
    unknown = laps["date_start"].isna()

    if not periods.empty:
        for session_key, per in periods.groupby("session_key", sort=False):
            mask_session = laps["session_key"] == session_key
            if not mask_session.any():
                continue
            sub = laps.loc[mask_session]

            for _, p in per.iterrows():
                overlap = (sub["date_start"] < p["date_end"]) & (sub["lap_end"] > p["date_start"])
                idx = sub.index[overlap.fillna(False)]
                col = {"SC": "sc_flag", "VSC": "vsc_flag", "RED": "red_flag"}[p["kind"]]
                laps.loc[idx, col] = 1

    # --- sector yellows, kept separate ---------------------------------------
    yellows = pd.read_sql("""
        SELECT session_key, "date"
        FROM silver_race_control
        WHERE category = 'Flag' AND flag IN ('YELLOW', 'DOUBLE YELLOW')
    """, con)
    yellows["date"] = pd.to_datetime(yellows["date"], format="ISO8601", utc=True)

    laps["yellow_sector_flag"] = 0
    for session_key, ys in yellows.groupby("session_key", sort=False):
        mask_session = laps["session_key"] == session_key
        if not mask_session.any():
            continue
        sub = laps.loc[mask_session]
        for ts in ys["date"]:
            hit = (sub["date_start"] <= ts) & (sub["lap_end"] >= ts)
            laps.loc[sub.index[hit.fillna(False)], "yellow_sector_flag"] = 1

    # Race-wide neutralisation only — sector yellows deliberately excluded.
    laps["neutralised"] = (
        (laps["sc_flag"] == 1) | (laps["vsc_flag"] == 1) | (laps["red_flag"] == 1)
    ).astype(int)

    for col in ("sc_flag", "vsc_flag", "red_flag", "yellow_sector_flag", "neutralised"):
        laps.loc[unknown, col] = None

    return laps[[
        "session_key", "driver_number", "lap_number",
        "sc_flag", "vsc_flag", "red_flag", "yellow_sector_flag", "neutralised",
    ]]


def write_tables(con: sqlite3.Connection, periods: pd.DataFrame, flags: pd.DataFrame) -> None:
    con.execute("DROP TABLE IF EXISTS silver_caution_periods")
    con.execute("""
        CREATE TABLE silver_caution_periods (
            period_id        INTEGER PRIMARY KEY,
            session_key      INTEGER NOT NULL,
            kind             TEXT    NOT NULL CHECK (kind IN ('SC', 'VSC', 'RED')),
            date_start       TEXT    NOT NULL,
            date_end         TEXT    NOT NULL,
            duration_seconds REAL,
            start_message    TEXT,
            closed_by        TEXT
        )
    """)
    out = periods.copy()
    out["date_start"] = out["date_start"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    out["date_end"] = out["date_end"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    out[[
        "period_id", "session_key", "kind", "date_start", "date_end",
        "duration_seconds", "start_message", "closed_by",
    ]].to_sql("silver_caution_periods", con, if_exists="append", index=False)

    con.execute("DROP TABLE IF EXISTS silver_lap_flags")
    con.execute("""
        CREATE TABLE silver_lap_flags (
            session_key        INTEGER NOT NULL,
            driver_number      INTEGER NOT NULL,
            lap_number         INTEGER NOT NULL,
            sc_flag            INTEGER,
            vsc_flag           INTEGER,
            red_flag           INTEGER,
            yellow_sector_flag INTEGER,
            neutralised        INTEGER,
            PRIMARY KEY (session_key, driver_number, lap_number)
        )
    """)
    flags.to_sql("silver_lap_flags", con, if_exists="append", index=False)
    con.execute("""
        CREATE INDEX idx_lap_flags_session ON silver_lap_flags (session_key)
    """)
    con.commit()


def validate(con: sqlite3.Connection) -> None:
    """Evidence that the flags separate genuinely different pace regimes."""
    print("\n" + "=" * 74)
    print("VALIDATION")
    print("=" * 74)

    print("\nMedian lap time by flag (Race sessions, 2024+):")
    q = """
        SELECT
            CASE
                WHEN f.red_flag = 1 THEN 'red flag'
                WHEN f.sc_flag  = 1 THEN 'safety car'
                WHEN f.vsc_flag = 1 THEN 'virtual SC'
                WHEN f.yellow_sector_flag = 1 THEN 'sector yellow only'
                ELSE 'clean'
            END AS regime,
            COUNT(*) AS laps,
            ROUND(AVG(l.lap_duration), 2) AS mean_seconds
        FROM silver_laps l
        JOIN silver_lap_flags f
          ON  f.session_key   = l.session_key
          AND f.driver_number = l.driver_number
          AND f.lap_number    = l.lap_number
        JOIN silver_sessions s ON s.session_key = l.session_key
        WHERE s.session_name = 'Race' AND s.year >= 2024
          AND l.lap_duration IS NOT NULL
          AND l.lap_duration BETWEEN 50 AND 400
        GROUP BY regime
        ORDER BY mean_seconds
    """
    print(pd.read_sql(q, con).to_string(index=False))

    print("\nCaution periods by kind:")
    print(pd.read_sql("""
        SELECT kind, COUNT(*) AS periods,
               ROUND(AVG(duration_seconds), 1) AS mean_seconds,
               ROUND(MAX(duration_seconds), 1) AS max_seconds
        FROM silver_caution_periods GROUP BY kind ORDER BY periods DESC
    """, con).to_string(index=False))

    print("\nHow periods were closed:")
    print(pd.read_sql("""
        SELECT closed_by, COUNT(*) AS n
        FROM silver_caution_periods GROUP BY closed_by ORDER BY n DESC
    """, con).to_string(index=False))

    print("\nSuspiciously long periods (>1800s — likely an unlogged closing message):")
    long = pd.read_sql("""
        SELECT p.session_key, m.meeting_name, s.session_name, p.kind,
               ROUND(p.duration_seconds) AS seconds, p.closed_by
        FROM silver_caution_periods p
        JOIN silver_sessions s ON s.session_key = p.session_key
        JOIN silver_meetings m ON m.meeting_key = s.meeting_key
        WHERE p.duration_seconds > 1800
        ORDER BY p.duration_seconds DESC LIMIT 10
    """, con)
    print("none" if long.empty else long.to_string(index=False))


def main() -> int:
    ap = argparse.ArgumentParser(description="Build caution periods and lap flags.")
    ap.add_argument("--validate", action="store_true", help="print validation evidence")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[FAIL] database not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))

    print("=" * 74)
    print("CAUTION PERIODS AND LAP FLAGS")
    print("=" * 74)

    print("\nPairing race control messages into periods...")
    periods = build_periods(con)
    if periods.empty:
        print("[FAIL] no caution periods found")
        con.close()
        return 1
    print(f"  {len(periods):,} periods")

    print("\nFlagging laps by timestamp overlap...")
    flags = build_lap_flags(con, periods)
    print(f"  {len(flags):,} laps processed")

    print("\nWriting tables...")
    write_tables(con, periods, flags)

    summary = flags[["sc_flag", "vsc_flag", "red_flag", "yellow_sector_flag", "neutralised"]].sum()
    print("\nFlagged laps:")
    for name, n in summary.items():
        print(f"  {name:20s} {int(n):>8,}")
    unknown = int(flags["neutralised"].isna().sum())
    print(f"  {'unknown (no date)':20s} {unknown:>8,}")

    if args.validate:
        validate(con)

    con.close()
    print("\nDone. Rerun this after any silver rebuild — these are derived tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())