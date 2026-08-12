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

# A restart is a field event, so it takes at least this many cars to call one.
# Used to reject a single car circulating during a stoppage.
MIN_CARS_FOR_RESTART = 3

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


def is_red_flag(row) -> bool:
    """
    Is this race control message a race suspension?

    THREE SPELLINGS, and the third was invisible until 2026-08-11. The usual one
    is category='Flag', flag='RED', scope='Track'. From 2026 OpenF1 also logs
    category='Other' with no flag column at all and the message 'RED FLAG -
    RACE SUSPENDED'. There are 21 of those, and one of them is the Monaco 2026
    race, whose stoppage was therefore never detected: 17 laps of 2,260 seconds
    each sat in the data flagged as green-flag racing.

    This is the same shape as the VSC problem in NOTES_LOG: a category that
    looks unrelated carrying the event under a different name.

    Matched on the message START rather than a substring, because 27 messages
    contain 'RED FLAG INFRINGEMENT', which is a stewards' note about a driver
    and not a suspension. Matching those would invent red flag periods.
    """
    if row["category"] == "Flag" and row["flag"] == "RED":
        return True
    return str(row["message"] or "").strip().upper().startswith("RED FLAG")


def safety_car_starts(con: sqlite3.Connection) -> list[dict]:
    """
    Races that begin behind the safety car, which leave no deployment message.

    THE BUG THIS FIXES, found 2026-08-11 while auditing the 'valid lap' rule.
    Every other caution opens on a 'SAFETY CAR DEPLOYED' message. When the race
    STARTS behind the safety car the car is already on track before the session
    begins, so that message is never sent and no period is ever opened. Spa 2025
    ran its first four laps at 1.57-1.86x its own green pace with all 80 of them
    recorded as green-flag racing.

    This is the third variant of one recurring failure: the event is announced
    in prose under category='Other' rather than as a flag. Compare is_red_flag()
    and the VSC spellings in NOTES_LOG.

    START is unambiguous: the session start, which is where lap 1 begins.

    END is not. Three candidate rules were scored against the four sessions that
    carry the announcement, using the per-car lap tables as ground truth:

      (a) first ROLLING/STANDING START message after the session starts
      (b) the restart_finder statistic, seeded at the session start
      (c) end of the last field-wide slow lap

    (b) fired on all four and was wrong on three, inventing periods for the two
    sessions that need none. (c) needs a slowness threshold, which is the
    mistake RESTART_FACTOR already made once. (a) fires on exactly the two
    sessions that need a period, gets Spa 2025 exactly right, and stays silent
    otherwise, so it is never wrong in the direction of over-flagging.

    Its one known shortfall is Miami 2025 sprint lap 3, where the safety car
    came in mid-lap and the message predates that. Laps 1-2 are flagged, lap 3
    is not. Under-flagging one lap-event is preferred to rule (b)'s two invented
    periods, and check [21] in s03_verify reports the residual rather than
    letting it hide.
    """
    rc = pd.read_sql("""
        SELECT session_key, "date", UPPER(TRIM(message)) AS msg
        FROM silver_race_control
    """, con)
    if rc.empty:
        return []
    rc["date"] = pd.to_datetime(rc["date"], format="ISO8601", utc=True)

    # 'FORMATION LAP WILL BE STARTED BEHIND THE SAFETY CAR' and 'RACE WILL START
    # BEHIND THE SAFETY CAR' are the two spellings present; one carries an
    # 'ON WET-WEATHER TYRES' suffix, so this matches on the phrase, not equality.
    announced = rc[rc.msg.str.contains(r"START(?:ED)?\s+BEHIND THE SAFETY CAR",
                                       regex=True, na=False)]
    if announced.empty:
        return []

    starts = pd.read_sql("""
        SELECT session_key, MIN(date_start) AS session_start
        FROM silver_laps WHERE lap_number = 1 AND date_start IS NOT NULL
        GROUP BY session_key
    """, con)
    starts["session_start"] = pd.to_datetime(starts.session_start,
                                             format="ISO8601", utc=True)
    session_start = dict(zip(starts.session_key, starts.session_start))

    periods = []
    for session_key in sorted(announced.session_key.unique()):
        begin = session_start.get(session_key)
        if begin is None or pd.isna(begin):
            continue
        grp = rc[rc.session_key == session_key]
        # The procedure message ends the neutralisation. Spa 2023's sprint logs
        # it BEFORE the session starts, meaning the car came in during the
        # delay, so requiring 'after the start' correctly yields no period.
        closing = grp[(grp["date"] > begin)
                      & grp.msg.str.contains(r"\b(?:ROLLING|STANDING) START\b",
                                             regex=True, na=False)]
        if closing.empty:
            continue
        periods.append({
            "session_key": int(session_key),
            "kind": "SC",
            "date_start": begin,
            "date_end": closing["date"].min(),
            "start_message": "RACE STARTED BEHIND THE SAFETY CAR (inferred)",
            "closed_by": "start_procedure",
        })

    print(f"  safety-car starts detected: {len(periods)} "
          f"(announced in {announced.session_key.nunique()} sessions)")
    return periods


def start_procedures(con: sqlite3.Connection) -> dict:
    """
    Per session, the timestamps of every restart-procedure message.

    A red-flagged race does not resume when the track goes green, it resumes
    when the field is released from the grid or from behind the safety car.
    Race control logs that as 'STANDING START' or 'ROLLING START'.
    """
    df = pd.read_sql("""
        SELECT session_key, "date"
        FROM silver_race_control
        WHERE UPPER(message) LIKE '%ROLLING START%'
           OR UPPER(message) LIKE '%STANDING START%'
    """, con)
    if df.empty:
        return {}
    df["date"] = pd.to_datetime(df["date"], format="ISO8601", utc=True)
    return {k: sorted(g["date"].tolist())
            for k, g in df.groupby("session_key", sort=False)}


def effective_session_end(con: sqlite3.Connection) -> dict:
    """
    When did each session actually stop, as opposed to when it was scheduled to?

    THE BUG THIS FIXES. A period with no closing message used to be closed with
    silver_sessions.date_end, which is the SCHEDULED end. Any race that overruns
    ends after that, and a red-flagged race always overruns, so the fallback
    could land before the period's own start.

    Found 2026-08-11: 18 of 422 periods had date_end < date_start, down to
    -2,226 seconds. Melbourne 2023 suspended the race at 07:37:06 and the period
    was closed at 07:00:00. The lap-to-period overlap join then matched nothing,
    so ten cars averaging 1,997 seconds on lap 57 were recorded as green-flag
    racing.

    The 18 negatives were the visible half. Another 36 periods closed the same
    way were merely TRUNCATED, silently leaving the tail of a real caution
    flagged green at times that look perfectly plausible and would never show up
    in an outlier search.

    So the fallback becomes the latest thing actually observed in that session:
    the last race control message, the last lap started, or the scheduled end,
    whichever is furthest along.
    """
    df = pd.read_sql("""
        SELECT s.session_key,
               s.date_end AS scheduled_end,
               (SELECT MAX(rc."date") FROM silver_race_control rc
                 WHERE rc.session_key = s.session_key) AS last_message,
               (SELECT MAX(l.date_start) FROM silver_laps l
                 WHERE l.session_key = s.session_key)  AS last_lap_start
        FROM silver_sessions s
    """, con)

    for col in ("scheduled_end", "last_message", "last_lap_start"):
        df[col] = pd.to_datetime(df[col], format="ISO8601", utc=True,
                                 errors="coerce")

    df["effective_end"] = df[
        ["scheduled_end", "last_message", "last_lap_start"]
    ].max(axis=1)

    later = int((df.effective_end > df.scheduled_end).sum())
    print(f"  sessions running past their scheduled end: {later} "
          f"of {len(df)}")
    return dict(zip(df.session_key, df.effective_end))


def restart_finder(con: sqlite3.Connection):
    """
    Returns f(session_key, after_ts) -> when racing demonstrably resumed.

    WHY INFERENCE IS NEEDED. Closing a period at the session end is only correct
    when the session really ended under caution. When the closing message is
    simply missing, it swallows the rest of the race.

    Monaco 2024 is the case that forced this. Race control logs a RED FLAG at
    13:04:08 and never logs the restart, yet the race resumed and ran to full
    distance. Extending to the true session end flagged all 1,237 laps of the
    race as neutralised. Extending to the SCHEDULED end, which is what the code
    did before, happened to flag fewer laps, but only by accident.

    HOW, AND WHY NOT WITH A THRESHOLD. The obvious rule is "the first lap after
    the stoppage that runs at a plausible pace". That was tried and rejected on
    evidence: it is decided by whichever single car does something unusual, and
    it is not robust to the threshold it needs.

    Japan 2024 is the case. After the red flag the whole field records lap 2 at
    about 1,711 seconds, 17.5x the session median, because they were parked.
    Car 22 alone records 203.186s, 2.08x. Moving the cutoff from 2.0x to 2.5x
    admits that one lap and drags the inferred restart 25 minutes earlier.
    Requiring several cars to agree did not help either: across cutoffs from
    1.5x to 10x, and corroboration from 1 to 5 cars, worst-case disagreement was
    between 1,530 and 2,824 seconds. A constant that moves the answer by 25
    minutes is a decision in disguise, not a parameter.

    So the estimate is taken from a quantity that needs no cutoff. A car
    stopped by a red flag is still "on" a lap, and that lap does not end until
    the race restarts and the car completes it. Its END time is therefore an
    observation of the restart, give or take the time to finish the lap.

        restart  =  median over cars of (first lap start after the stoppage
                                         + that lap's duration)
                    minus one session-median lap

    The median across cars is what makes it robust: one car doing something
    unusual cannot move it, and no threshold decides who is included.

    Precision is roughly one lap, which is the resolution the flag needs, since
    the result is only used to decide which laps overlap the period.
    """
    laps = pd.read_sql("""
        SELECT session_key, driver_number, date_start, lap_duration
        FROM silver_laps
        WHERE date_start IS NOT NULL AND lap_duration IS NOT NULL
        ORDER BY session_key, date_start
    """, con)
    laps["date_start"] = pd.to_datetime(laps["date_start"], format="ISO8601",
                                        utc=True, errors="coerce")
    laps = laps.dropna(subset=["date_start"])
    laps["date_end"] = laps["date_start"] + pd.to_timedelta(
        laps["lap_duration"], unit="s")

    medians = laps.groupby("session_key").lap_duration.median()
    by_session = {
        key: grp.sort_values("date_start")
        for key, grp in laps.groupby("session_key", sort=False)
    }

    def find(session_key, after_ts):
        grp = by_session.get(session_key)
        med = medians.get(session_key)
        if grp is None or med is None or pd.isna(med):
            return None

        later = grp[grp.date_start > after_ts]
        if later.empty:
            return None

        # One lap per car: the first it began after the stoppage started. That
        # is the lap holding the stoppage, and it cannot end before the restart.
        first_each = later.groupby("driver_number", sort=False).first()
        if len(first_each) < MIN_CARS_FOR_RESTART:
            return None

        # Median across cars, so no single car and no cutoff decides it.
        restart = first_each["date_end"].median() - pd.Timedelta(seconds=med)
        # It cannot precede the stoppage itself.
        return max(restart, after_ts)

    return find


def build_periods(con: sqlite3.Connection) -> pd.DataFrame:
    """Pairs deployment messages with their closing messages, per session."""
    ends = effective_session_end(con)
    find_restart = restart_finder(con)
    procedures = start_procedures(con)

    def fallback_end(session_key, start_ts, kind="RED", before=None):
        """
        Close a period whose ending was never logged.

        Prefers evidence of racing resuming over the session end, and never
        returns something earlier than the start.

        THEN TAKES THE LATER OF THAT AND THE RESTART PROCEDURE, added
        2026-08-12 for Monaco 2026. The stoppage there was inferred to end at
        15:11:35, but race control logged STANDING START at 15:14:26, and laps
        69 and 70 fall in between: the field forming up on the grid, lap 70 at
        157s against a 77s racing pace, all sixteen cars, recorded as green.
        Check [21] had it as the loudest unexplained slowdown in the dataset at
        2.02x.

        WHY max() AND NOT "PREFER THE MESSAGE". Both signals are lower bounds on
        when racing actually resumed, so the later one is the safer claim, and
        that single rule handles two opposite cases without a threshold:

          Monaco 2026 logs a bare 'STANDING START', which is the event itself
          and lands 171s AFTER the inference. The message wins.

          Melbourne 2023 logs 'RACE WILL RESUME AT 15:33 - STANDING START
          PROCEDURE', which is an announcement of a future time and lands 208s
          BEFORE the inference. The inference wins.

        Preferring the message outright would have shortened 18 periods.
        Measured across all 53 weakly-closed red periods, max() extends exactly
        one: Monaco 2026, by 171 seconds.

        `before` bounds the search at the next GREEN track flag, because a green
        flag is itself proof racing had resumed, so a procedure message after it
        belongs to a later event. Without that bound, testing-day periods
        extended by up to 31,361 seconds by picking up an unrelated message
        hours later.
        """
        restart = find_restart(session_key, start_ts)
        if restart is not None:
            end, closed_by = restart, "restart_inferred"
        else:
            end = ends.get(session_key)
            if end is None or pd.isna(end) or end < start_ts:
                return start_ts, "unclosed"
            closed_by = "session_end"

        # RED only. A restart procedure is how a SUSPENDED race resumes. A
        # safety car ends with 'SAFETY CAR IN THIS LAP' and a green flag, so
        # letting a procedure message extend an SC period would attach the
        # wrong event to it.
        if kind != "RED":
            return end, closed_by

        later = [p for p in procedures.get(session_key, [])
                 if p > start_ts and (before is None or p <= before)]
        if later and later[0] > end:
            return later[0], "start_procedure"
        return end, closed_by

    rc = pd.read_sql("""
        SELECT rc.session_key, rc."date", rc.category, rc.flag, rc.scope, rc.message
        FROM silver_race_control rc
        WHERE rc.category = 'SafetyCar'
           OR (rc.category = 'Flag' AND rc.flag IN ('RED', 'GREEN') AND rc.scope = 'Track')
           OR UPPER(TRIM(rc.message)) LIKE 'RED FLAG%'
        ORDER BY rc.session_key, rc."date"
    """, con)

    if rc.empty:
        return pd.DataFrame()

    rc["date"] = pd.to_datetime(rc["date"], format="ISO8601", utc=True)

    # Seeded before the message walk because a safety-car start is bounded by
    # the session start, not by any deployment, so it cannot be produced by the
    # open/close state machine below.
    periods = safety_car_starts(con)

    for session_key, grp in rc.groupby("session_key", sort=False):
        grp = grp.sort_values("date")

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
            if is_red_flag(row):
                if open_period is not None:
                    close(open_period, ts, "superseded_by_red")
                    open_period = None
                nxt = [g for g in greens if g > ts]
                if nxt:
                    end_ts, closed_by = nxt[0], "green_flag"
                else:
                    # No green flag follows, so nothing bounds the procedure
                    # search except the period itself.
                    end_ts, closed_by = fallback_end(session_key, ts)
                periods.append({
                    "session_key": session_key,
                    "kind": "RED",
                    "date_start": ts,
                    "date_end": end_ts,
                    "start_message": row["message"],
                    "closed_by": closed_by,
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
            end_ts, closed_by = fallback_end(session_key,
                                             open_period["date_start"],
                                             kind=open_period["kind"])
            close(open_period, end_ts, closed_by)

    df = pd.DataFrame(periods)
    if df.empty:
        return df

    df = df.sort_values(["session_key", "date_start"]).reset_index(drop=True)
    df["period_id"] = range(1, len(df) + 1)

    # A period cannot end before it starts. This is the invariant that was
    # violated for two and a half years without anything noticing, so it is
    # asserted here rather than left to be rediscovered: a negative duration
    # makes the lap overlap join match nothing, which reads as "no caution"
    # rather than as an error.
    backwards = df["date_end"] < df["date_start"]
    if backwards.any():
        print(f"  [WARN] {int(backwards.sum())} period(s) still end before they "
              "start; clamping to zero length")
        for _, row in df[backwards].iterrows():
            print(f"         session {row.session_key} {row.kind} "
                  f"{row.date_start} -> {row.date_end} ({row.closed_by})")
        df.loc[backwards, "date_end"] = df.loc[backwards, "date_start"]

    df["duration_seconds"] = (df["date_end"] - df["date_start"]).dt.total_seconds()

    # A period running to session end can be absurdly long if the closing
    # message was simply never logged; flag rather than silently trust.
    longest = df.nlargest(3, "duration_seconds")
    print(f"  periods: {len(df)}  "
          f"negative: {int((df.duration_seconds < 0).sum())}  "
          f"longest: {longest.duration_seconds.iloc[0]:,.0f}s")
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