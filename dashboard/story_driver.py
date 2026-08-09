"""
story_driver.py - "Story of a Driver": one driver's race, in race order.

Answers the driver-level questions from
DESCRIPTIVE ANALYTICS/descriptive_question_bank.md for a single car in a
single race. Where the bank asks a driver-level question against the teammate
(pace lap by lap, who out-qualified whom) it is answered here; the full
side-by-side comparison belongs to Story of a Team.

Blocks follow the bank's own order:
    Pre-race, grid & setup
    The start, lap 1
    Race pace, lap by lap
    Tyre strategy
    Pit stops
    Position dynamics
    Gaps & race context
    Incidents & external context
    Team radio
    Finish & outcome
    Against the teammate
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app_common import NEUTRAL, fmt_gap, fmt_lap, query, team_colours
from story_common import (
    ACCENT, AXIS_BASE, CLEAN_LAP, FIGHTING_SECONDS, INK, MUTED, PLOT_BASE,
    field, guide, line_layout,
)

# Below this many clean laps a trend line describes noise rather than a race.
MIN_LAPS_FOR_TREND = 8


def _driver_laps(session_key: int, driver_number: int) -> pd.DataFrame:
    return query("""
        SELECT lap_number, lap_duration, duration_sector_1, duration_sector_2,
               duration_sector_3, is_pit_out_lap, neutralised, stint_number,
               compound, tyre_age, position, interval_seconds,
               gap_to_leader_seconds, gap_to_leader_laps, lap_vs_median
        FROM fact_lap
        WHERE session_key = ? AND driver_number = ?
        ORDER BY lap_number
    """, (session_key, driver_number))


def _clean(laps: pd.DataFrame) -> pd.DataFrame:
    return laps[(laps.neutralised == 0) & (laps.is_pit_out_lap == 0)
                & laps.lap_duration.notna()]


# --- 1. Pre-race, grid & setup -------------------------------------------------

def _grid(me: pd.Series, mate: pd.Series | None) -> None:
    st.subheader("Pre-race, grid and setup")
    st.caption("Where this driver started, and the lap time that earned it.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Grid",
              f"P{int(me.grid_position)}" if pd.notna(me.grid_position) else "-")
    c2.metric("Qualifying lap", fmt_lap(me.quali_lap_seconds))

    if mate is not None and pd.notna(me.teammate_grid_delta):
        # Delta is this driver minus the teammate, so a lower grid number, and
        # therefore a negative delta, means qualifying ahead.
        d = int(me.teammate_grid_delta)
        c3.metric("Against teammate",
                  "ahead" if d < 0 else ("behind" if d > 0 else "level"),
                  f"{d:+d} places vs {mate.name_acronym}", delta_color="inverse")
    if pd.notna(me.quali_lap_seconds) and mate is not None \
            and pd.notna(mate.quali_lap_seconds):
        gap = me.quali_lap_seconds - mate.quali_lap_seconds
        c4.metric("Qualifying gap", f"{gap:+.3f}s", "to teammate",
                  delta_color="off")

    if pd.isna(me.quali_lap_seconds) and pd.notna(me.grid_position):
        st.warning(
            "Started from a grid slot with no qualifying lap time recorded"
            + (" (did not start)" if me.dns else
               " (took part, but set no time, or the time was deleted)")
        )


# --- 2. The start, lap 1 -------------------------------------------------------

def _start(session_key: int, me: pd.Series, laps: pd.DataFrame) -> None:
    st.subheader("The start, lap 1")
    st.caption("What the opening lap cost or gained.")

    # fact_lap.position is sampled at each lap's start, so position after
    # lap 1 is carried on lap 2. A few driver-races are missing individual lap
    # rows, so fall back to the earliest lap available and say so rather than
    # presenting a later lap as if it were the end of lap 1.
    after = laps[laps.lap_number >= 2]
    pos_after = after.position.iloc[0] if len(after) else np.nan
    read_at = int(after.lap_number.iloc[0]) if len(after) else None
    exact = read_at == 2

    c1, c2, c3 = st.columns(3)
    c1.metric("Grid",
              f"P{int(me.grid_position)}" if pd.notna(me.grid_position) else "-")
    c2.metric("After lap 1" if exact else f"After lap {read_at - 1}"
              if read_at else "After lap 1",
              f"P{int(pos_after)}" if pd.notna(pos_after) else "-")
    if pd.notna(pos_after) and pd.notna(me.grid_position):
        gained = int(me.grid_position - pos_after)
        c3.metric("Places", f"{gained:+d}",
                  "gained" if gained > 0 else ("lost" if gained < 0 else "held"))

    if read_at is not None and not exact:
        st.caption(
            f"This driver has no lap 2 record, so the position above is read "
            f"at the start of lap {read_at}, covering the first "
            f"{read_at - 1} laps rather than just the opening one."
        )

    opening = query("""
        SELECT lap_number, detail
        FROM fact_event
        WHERE session_key = ? AND driver_number = ? AND lap_number <= 2
          AND event_type IN ('race_control', 'race_control_driver')
        ORDER BY lap_number
    """, (session_key, int(me.driver_number)))

    if len(opening):
        st.caption("Race control, opening laps:")
        for r in opening.itertuples():
            st.caption(f"  Lap {int(r.lap_number)}: {r.detail}")
    else:
        st.caption("No race control message named this driver in the opening laps.")

    st.caption(
        "Lap-1 overtakes cannot be isolated: the overtake feed carries a "
        "timestamp but no lap number."
    )


# --- 3. Race pace, lap by lap --------------------------------------------------

def _pace(session_key: int, me: pd.Series, mate: pd.Series | None,
          laps: pd.DataFrame) -> None:
    st.subheader("Race pace, lap by lap")
    st.caption(
        "Every lap this driver ran, against their teammate where there is one."
    )

    clean = _clean(laps)
    if clean.empty:
        st.info("No green-flag lap times recorded for this driver.")
        return

    # Trend: seconds gained or lost per lap across the race. Fuel burn makes a
    # negative slope normal, so this describes the shape of their race, not
    # tyre degradation on its own.
    trend = np.nan
    if len(clean) >= MIN_LAPS_FOR_TREND:
        trend = np.polyfit(clean.lap_number, clean.lap_duration, 1)[0]

    fastest = clean.loc[clean.lap_duration.idxmin()]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Fastest lap", fmt_lap(fastest.lap_duration),
              f"lap {int(fastest.lap_number)}")
    c2.metric("Mean green-flag lap", fmt_lap(clean.lap_duration.mean()),
              f"{len(clean)} laps")
    if pd.notna(trend):
        c3.metric("Trend", f"{trend:+.3f}s per lap",
                  "improving" if trend < 0 else "degrading", delta_color="off")
    if mate is not None and pd.notna(me.teammate_pace_delta):
        d = me.teammate_pace_delta
        c4.metric("Pace vs teammate", f"{d:+.3f}s",
                  "faster" if d < 0 else "slower", delta_color="inverse")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=clean.lap_number, y=clean.lap_duration, mode="lines+markers",
        name=me.name_acronym or f"#{int(me.driver_number)}",
        line=dict(color=INK, width=2), marker=dict(size=4),
        hovertemplate="Lap %{x}<br>%{y:.3f}s<extra></extra>",
    ))

    if mate is not None:
        mate_laps = _clean(_driver_laps(session_key, int(mate.driver_number)))
        if len(mate_laps):
            fig.add_trace(go.Scatter(
                x=mate_laps.lap_number, y=mate_laps.lap_duration,
                mode="lines", name=mate.name_acronym or "teammate",
                line=dict(color=MUTED, width=1.5, dash="dot"),
                hovertemplate="Lap %{x}<br>%{y:.3f}s<extra></extra>",
            ))

    # Anomalous laps, judged against this driver's own spread rather than a
    # fixed number of seconds (Tukey upper fence).
    q1, q3 = clean.lap_duration.quantile([0.25, 0.75])
    fence = q3 + 1.5 * (q3 - q1)
    odd = clean[clean.lap_duration > fence]
    if len(odd):
        fig.add_trace(go.Scatter(
            x=odd.lap_number, y=odd.lap_duration, mode="markers",
            name="Unusually slow", marker=dict(color=ACCENT, size=9,
                                               symbol="circle-open"),
            hovertemplate="Lap %{x}<br>%{y:.3f}s<br>unusually slow<extra></extra>",
        ))

    st.plotly_chart(line_layout(fig, "Lap", "Lap time (s)"), width="stretch")
    guide(
        "Lower is faster. The solid line is this driver, the dotted line their "
        "teammate. Both generally fall as fuel burns off, and step up sharply "
        "on pit laps. Circled points are laps slower than this driver's own "
        f"typical spread ({fence:.1f}s here), which usually means traffic, a "
        "mistake, or a lap caught by a yellow flag."
    )

    # Sector strengths, relative to the rest of the field on clean laps.
    sectors = query(f"""
        SELECT driver_number,
               AVG(duration_sector_1) s1,
               AVG(duration_sector_2) s2,
               AVG(duration_sector_3) s3
        FROM fact_lap
        WHERE session_key = ? AND {CLEAN_LAP}
          AND duration_sector_1 IS NOT NULL
        GROUP BY driver_number
    """, (session_key,))

    if len(sectors) > 1 and int(me.driver_number) in set(sectors.driver_number):
        mine = sectors[sectors.driver_number == int(me.driver_number)].iloc[0]
        rows = []
        for i, col in enumerate(["s1", "s2", "s3"], start=1):
            best = sectors[col].min()
            rows.append({
                "Sector": f"Sector {i}",
                "Average": mine[col],
                "Best in field": best,
                "Off the best": mine[col] - best,
                "Rank": int(sectors[col].rank().loc[mine.name]),
            })
        sec = pd.DataFrame(rows)
        st.dataframe(
            sec, hide_index=True, width="stretch",
            column_config={
                "Average": st.column_config.NumberColumn(format="%.3f s"),
                "Best in field": st.column_config.NumberColumn(format="%.3f s"),
                "Off the best": st.column_config.NumberColumn(format="+%.3f s"),
                "Rank": st.column_config.NumberColumn(
                    format="%d", width="small",
                    help=f"Out of {len(sectors)} drivers with sector data."),
            },
        )
        guide(
            "Average sector times across green-flag laps, against the best "
            "average any driver managed in that sector. The sector with the "
            "largest 'off the best' is where this car lost the most time, "
            "which points at whether the weakness was traction, high-speed "
            "corners, or the straights."
        )


# --- 4. Tyre strategy ----------------------------------------------------------

def _tyres(me: pd.Series, laps: pd.DataFrame) -> None:
    st.subheader("Tyre strategy")
    st.caption("The stints this driver ran, and how long each one lasted.")

    stints = laps.dropna(subset=["stint_number"])
    c1, c2 = st.columns(2)
    c1.metric("Stints", int(me.stint_count) if pd.notna(me.stint_count) else "-")
    c2.metric("Compound sequence", me.compound_sequence or "not recorded")

    if stints.empty:
        st.info(
            "No per-lap stint data for this driver. Compound sequence above "
            "comes from the race-level record, which is far better covered "
            "than the lap-by-lap tyre data."
        )
        return

    table = (stints.groupby("stint_number")
                   .agg(Compound=("compound", "first"),
                        From=("lap_number", "min"),
                        To=("lap_number", "max"),
                        Laps=("lap_number", "size"),
                        age_start=("tyre_age", "min"))
                   .reset_index()
                   .rename(columns={"stint_number": "Stint",
                                    "age_start": "Tyre age at start"}))
    st.dataframe(
        table, hide_index=True, width="stretch",
        column_config={c: st.column_config.NumberColumn(format="%d",
                                                        width="small")
                       for c in ["Stint", "From", "To", "Laps",
                                 "Tyre age at start"]},
    )
    guide(
        "One row per stint, in order. Tyre age at start is how many laps were "
        "already on that set when it was fitted, so a non-zero value means a "
        "used set rather than a fresh one. Lap-by-lap tyre data is only about "
        "a third populated across the archive, so a stint may cover fewer laps "
        "here than the driver actually ran on it."
    )


# --- 5. Pit stops --------------------------------------------------------------

def _pits(session_key: int, me: pd.Series) -> None:
    st.subheader("Pit stops")
    st.caption("When this driver stopped, and how those stops compared.")

    stops = query("""
        SELECT lap_number, value AS lane_seconds
        FROM fact_event
        WHERE session_key = ? AND driver_number = ? AND event_type = 'pit_stop'
        ORDER BY lap_number
    """, (session_key, int(me.driver_number)))

    field_stops = query("""
        SELECT value AS lane_seconds FROM fact_event
        WHERE session_key = ? AND event_type = 'pit_stop' AND value IS NOT NULL
    """, (session_key,))

    c1, c2 = st.columns(2)
    c1.metric("Stops", int(me.pit_stops) if pd.notna(me.pit_stops) else 0)
    if pd.notna(me.mean_lane_duration):
        delta = None
        if len(field_stops):
            delta = me.mean_lane_duration - field_stops.lane_seconds.median()
        c2.metric("Average time in lane", f"{me.mean_lane_duration:.1f}s",
                  f"{delta:+.1f}s vs the field median" if delta is not None
                  else None, delta_color="inverse")

    if stops.empty:
        st.caption(
            "No individual pit records for this driver. Pit coverage is "
            "incomplete in 2023, where 6 of 22 races have none."
        )
        return

    # Judge a bad stop against the field on the day, not a fixed number: pit
    # lanes differ enormously between circuits.
    fence = None
    if len(field_stops) >= 4:
        q1, q3 = field_stops.lane_seconds.quantile([0.25, 0.75])
        fence = q3 + 1.5 * (q3 - q1)

    show = stops.copy()
    show["Verdict"] = "routine"
    if fence is not None:
        show.loc[show.lane_seconds > fence, "Verdict"] = "unusually long"
    show.loc[show.lane_seconds > 120, "Verdict"] = "red-flag suspension"

    st.dataframe(
        show.rename(columns={"lap_number": "Lap",
                             "lane_seconds": "Time in lane"}),
        hide_index=True, width="stretch",
        column_config={
            "Lap": st.column_config.NumberColumn(format="%d", width="small"),
            "Time in lane": st.column_config.NumberColumn(format="%.1f s"),
        },
    )
    guide(
        "Time in lane is the full pit lane transit, not just the stationary "
        "time, so it includes the pit lane speed limit. "
        + (f"Anything over {fence:.1f}s was unusual for this race. "
           if fence is not None else "")
        + "Times running to minutes are a car held in the lane under a red "
          "flag, not slow pit work."
    )


# --- 6. Position dynamics ------------------------------------------------------

def _positions(me: pd.Series, laps: pd.DataFrame) -> None:
    st.subheader("Position dynamics")
    st.caption("Where this driver ran through the race.")

    trace = laps.dropna(subset=["position"])
    if trace.empty:
        st.info("No lap-by-lap position data for this driver.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Overtakes made",
              int(me.overtakes_made) if pd.notna(me.overtakes_made) else 0)
    c2.metric("Overtakes suffered",
              int(me.overtakes_suffered) if pd.notna(me.overtakes_suffered) else 0)
    c3.metric("Best position held", f"P{int(trace.position.min())}")

    fig = go.Figure(go.Scatter(
        x=trace.lap_number, y=trace.position, mode="lines",
        line=dict(color=INK, width=2),
        hovertemplate="Lap %{x}, P%{y}<extra></extra>",
    ))
    st.plotly_chart(
        line_layout(fig, "Lap", "Position", height=340, reverse_y=True),
        width="stretch",
    )
    guide(
        "First place is at the top, so the line rising means moving forward. "
        "Sharp drops are usually pit stops rather than being passed, because "
        "a car rejoins behind everyone who has not stopped yet, then climbs "
        "back as they do. The overtake counts above come from the API's feed, "
        "which also records pit-cycle gains and penalty swaps, so read them as "
        "position changes rather than strictly on-track passes."
    )


# --- 7. Gaps & race context ----------------------------------------------------

def _gaps(me: pd.Series, laps: pd.DataFrame) -> None:
    st.subheader("Gaps and race context")
    st.caption(
        "Whether this driver spent the race fighting, running alone, or being "
        "lapped."
    )

    clean = _clean(laps)
    have_interval = clean.interval_seconds.notna().any()
    have_leader = clean.gap_to_leader_seconds.notna().any()

    if not (have_interval or have_leader):
        st.info("No gap data recorded for this driver.")
        return

    fighting = int(clean.interval_seconds.between(0.001, FIGHTING_SECONDS).sum())

    c1, c2, c3 = st.columns(3)
    c1.metric("Laps in a fight", fighting,
              f"within {FIGHTING_SECONDS:g}s of the car ahead")
    if have_interval:
        c2.metric("Median gap ahead", f"{clean.interval_seconds.median():.2f}s")
    c3.metric("Lapped", "yes" if me.was_lapped else "no")

    fig = go.Figure()
    if have_leader:
        g = clean.dropna(subset=["gap_to_leader_seconds"])
        fig.add_trace(go.Scatter(
            x=g.lap_number, y=g.gap_to_leader_seconds, mode="lines",
            name="To the leader", line=dict(color=INK, width=2),
            hovertemplate="Lap %{x}<br>%{y:.2f}s behind the leader<extra></extra>",
        ))
    if have_interval:
        g = clean.dropna(subset=["interval_seconds"])
        fig.add_trace(go.Scatter(
            x=g.lap_number, y=g.interval_seconds, mode="lines",
            name="To the car ahead", line=dict(color=MUTED, width=1.5, dash="dot"),
            hovertemplate="Lap %{x}<br>%{y:.2f}s to the car ahead<extra></extra>",
        ))
    st.plotly_chart(line_layout(fig, "Lap", "Gap (s)"), width="stretch")
    guide(
        "The solid line is the gap back to the race leader, which mostly grows "
        "unless this driver was leading. The dotted line is the gap to "
        "whichever car was directly ahead: when it sits near zero the driver "
        "was in a fight, and when it climbs they were in clear air. Both are "
        "shown on green-flag laps only, since a safety car closes every gap."
    )


# --- 8. Incidents & external context -------------------------------------------

def _incidents(session_key: int, me: pd.Series, race) -> None:
    st.subheader("Incidents and conditions")
    st.caption("Race control messages naming this driver, and the conditions.")

    named = query("""
        SELECT lap_number, detail
        FROM fact_event
        WHERE session_key = ? AND driver_number = ?
          AND event_type IN ('race_control', 'race_control_driver')
        ORDER BY lap_number
    """, (session_key, int(me.driver_number)))

    c1, c2, c3 = st.columns(3)
    c1.metric("Messages naming this car", len(named))
    c2.metric("Neutralisations in the race",
              int(race.safety_car_periods + race.vsc_periods
                  + race.red_flag_periods),
              f"{int(race.safety_car_periods)} SC, "
              f"{int(race.vsc_periods)} VSC")
    c3.metric("Track", "Wet" if race.is_wet_race else "Dry")

    if len(named):
        with st.expander(f"All messages naming this car ({len(named)})"):
            st.dataframe(
                named.rename(columns={"lap_number": "Lap", "detail": "Message"}),
                hide_index=True, width="stretch",
                column_config={"Lap": st.column_config.NumberColumn(
                    format="%d", width="small")},
            )
            st.caption(
                "Blue flags dominate this list for slower cars: they are shown "
                "repeatedly as the leaders come through to lap them, so a high "
                "count often means a car being lapped, not one in trouble."
            )
    else:
        st.caption("Race control never named this car.")


# --- 9. Team radio -------------------------------------------------------------

def _radio(session_key: int, me: pd.Series) -> None:
    st.subheader("Team radio")

    n = query("""
        SELECT COUNT(*) AS n FROM fact_event
        WHERE session_key = ? AND driver_number = ? AND event_type = 'team_radio'
    """, (session_key, int(me.driver_number))).n.iloc[0]

    field_median = query("""
        SELECT COUNT(*) AS n FROM fact_event
        WHERE session_key = ? AND event_type = 'team_radio'
        GROUP BY driver_number
    """, (session_key,))

    c1, c2 = st.columns(2)
    c1.metric("Radio messages", int(n))
    if len(field_median):
        c2.metric("Field median", f"{field_median.n.median():.0f}",
                  "messages per driver")

    if n == 0:
        st.caption(
            "No radio recorded for this driver. Coverage falls away sharply in "
            "later seasons: 2,744 messages across 2023, but 217 across 2026."
        )
    else:
        st.caption(
            "The messages are audio only, with no transcription, and they carry "
            "no lap number, so only the count can be read. A count well above "
            "the field median is a signal to look closer, not an explanation."
        )


# --- 10. Finish & outcome ------------------------------------------------------

def _outcome(me: pd.Series, race) -> None:
    st.subheader("Finish and outcome")
    st.caption("How the race ended for this driver.")

    if pd.notna(me.finish_position):
        result = f"P{int(me.finish_position)}"
        note = None
    elif me.dsq:
        result, note = "DSQ", "disqualified"
    elif me.dns:
        result, note = "DNS", "did not start"
    else:
        result = "DNF"
        note = (f"retired on lap {int(me.laps_completed)}"
                if pd.notna(me.laps_completed) else "retired")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Result", result, note)
    c2.metric("Points", int(me.points) if pd.notna(me.points) else 0)
    if pd.notna(me.position_change):
        c3.metric("Places gained", f"{int(me.position_change):+d}",
                  f"from P{int(me.grid_position)}"
                  if pd.notna(me.grid_position) else None)
    c4.metric("Gap to winner",
              fmt_gap(me.gap_to_leader_seconds, me.gap_to_leader_laps))

    st.caption(
        f"Ran {int(me.laps_completed)} of {int(race.total_laps)} laps."
        if pd.notna(me.laps_completed) and pd.notna(race.total_laps) else ""
    )


# --- 11. Against the teammate --------------------------------------------------

def _teammate(me: pd.Series, mate: pd.Series | None) -> None:
    st.subheader("Against the teammate")

    if mate is None:
        st.info(
            "No teammate to compare against in this race: the team did not "
            "field two classified cars."
        )
        return

    st.caption(
        f"{me.full_name} against {mate.full_name}, the only comparison in the "
        "data that holds the car roughly constant."
    )

    def verdict(delta, lower_is_better=True):
        if pd.isna(delta):
            return "-", "no data"
        if delta == 0:
            return "level", ""
        ahead = delta < 0 if lower_is_better else delta > 0
        return ("ahead" if ahead else "behind"), f"{delta:+g}"

    rows = []
    for label, mine, theirs, lower_better, fmt in [
        ("Grid", me.grid_position, mate.grid_position, True, "{:.0f}"),
        ("Finish", me.finish_position, mate.finish_position, True, "{:.0f}"),
        ("Points", me.points, mate.points, False, "{:.0f}"),
        ("Mean green-flag lap", me.mean_clean_lap, mate.mean_clean_lap,
         True, "{:.3f}"),
        ("Pit stops", me.pit_stops, mate.pit_stops, True, "{:.0f}"),
        ("Overtakes made", me.overtakes_made, mate.overtakes_made,
         False, "{:.0f}"),
    ]:
        if pd.isna(mine) and pd.isna(theirs):
            continue
        delta = (mine - theirs) if pd.notna(mine) and pd.notna(theirs) else np.nan
        outcome = "-"
        if pd.notna(delta):
            outcome = "level" if delta == 0 else (
                "this driver" if (delta < 0) == lower_better else mate.name_acronym)
        rows.append({
            "Measure": label,
            me.name_acronym or "This driver": fmt.format(mine)
                if pd.notna(mine) else "-",
            mate.name_acronym or "Teammate": fmt.format(theirs)
                if pd.notna(theirs) else "-",
            "Better": outcome,
        })

    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "Strategy: "
        + (f"both cars ran {me.compound_sequence}."
           if me.compound_sequence and me.compound_sequence == mate.compound_sequence
           else f"strategies split, {me.compound_sequence or 'not recorded'} "
                f"against {mate.compound_sequence or 'not recorded'}.")
    )
    guide(
        "Better names whichever driver came out ahead on each measure, with "
        "the sense of the comparison built in: a lower grid slot, finish "
        "position and lap time are good, while more points and more overtakes "
        "are good. Pit stops are counted as fewer being better, which is a "
        "strategy choice rather than a verdict on the driver."
    )


# --- sections ------------------------------------------------------------------

# The bank's questions verbatim: it is written at driver level, which is
# exactly what this story answers.
SECTIONS = [
    ("grid_setup", "Pre-race, grid and setup", [
        "What grid position did the driver start from, and what lap time earned it?",
        "Did they hold a grid slot with no recorded qualifying lap time, and why?",
    ]),
    ("lap1", "The start, lap 1", [
        "What position did the driver hold after lap 1 against their grid slot?",
        "Did any race control flag or incident fire in the opening laps "
        "involving this driver?",
    ]),
    ("lap_by_lap", "Race pace, lap by lap", [
        "What was the driver's lap time trend across the race, improving, "
        "degrading, or flat?",
        "How does the driver's pace compare to their teammate, lap by lap?",
        "Were there specific laps with anomalous times, and where do they fall?",
        "What were the driver's sector strengths and weaknesses?",
        "What was the driver's fastest lap, and on which lap and compound?",
    ]),
    ("tyres", "Tyre strategy", [
        "How many stints did the driver run, on which compounds, and for how "
        "many laps each?",
        "What was the driver's tyre age at the start of each stint?",
    ]),
    ("pit_stops", "Pit stops", [
        "How many pit stops did the driver make, and on which laps?",
        "What was the lane duration for each stop?",
        "Did any stop go unusually long, a disaster stop?",
    ]),
    ("position", "Position dynamics", [
        "How did the driver's position evolve over the full race distance?",
        "How many overtakes did the driver make, and how many did they suffer?",
    ]),
    ("gaps", "Gaps and race context", [
        "How did the driver's gap to the leader evolve over the race?",
        "How did the gap to the car ahead evolve, fighting, isolated, or lapped?",
        "Was the driver lapped by the leader at any point?",
    ]),
    ("incidents", "Incidents and conditions", [
        "Was the driver specifically named in any race control message?",
        "What were the weather conditions during the race?",
    ]),
    ("radio", "Team radio", [
        "How many radio messages were sent for this driver during the race?",
    ]),
    ("outcome", "Finish and outcome", [
        "What was the final classified position, and how does it compare to "
        "the grid slot?",
        "Did the driver finish, DNF, DNS or get disqualified, and if DNF, at "
        "what lap?",
        "How many points did the driver score, and what was the gap to the winner?",
    ]),
    ("teammate", "Against the teammate", [
        "Who out-qualified whom, and by how much?",
        "Who scored more points, and by how much?",
        "Whose race had more incidents, pit stops or lost time?",
    ]),
]


def intro(race, driver_number: int) -> bool:
    """
    Who this story is about, printed once above the whole scroll.

    This used to live inside render(), which was correct when the page showed a
    single section and wrong the moment it showed eleven: the driver's name and
    team appeared eleven times down the page. Returns False when the driver is
    not in this race, so the caller can skip the sections entirely rather than
    have each one report the same absence.
    """
    everyone = field(int(race.session_key))
    row = everyone[everyone.driver_number == driver_number]
    if row.empty:
        st.info("This driver did not take part in the selected race.")
        return False
    me = row.iloc[0]
    st.markdown(f"### {me.full_name}  ·  {me.team_name}")
    return True


def section_options() -> list[tuple[str, str]]:
    return [(key, title) for key, title, _ in SECTIONS]


# --- entry point ---------------------------------------------------------------

def render(race, driver_number: int, section_key: str) -> None:
    session_key = int(race.session_key)
    everyone = field(session_key)

    row = everyone[everyone.driver_number == driver_number]
    if row.empty:
        # Silent. intro() has already said so, once, above every section.
        return
    me = row.iloc[0]

    # The teammate is the other car under the same normalised team name. Teams
    # occasionally field one classified car, so this can legitimately be absent.
    mates = everyone[(everyone.team_name == me.team_name)
                     & (everyone.driver_number != driver_number)]
    mate = mates.iloc[0] if len(mates) == 1 else None

    laps = _driver_laps(session_key, driver_number)

    blocks = {
        "grid_setup": lambda: _grid(me, mate),
        "lap1": lambda: _start(session_key, me, laps),
        "lap_by_lap": lambda: _pace(session_key, me, mate, laps),
        "tyres": lambda: _tyres(me, laps),
        "pit_stops": lambda: _pits(session_key, me),
        "position": lambda: _positions(me, laps),
        "gaps": lambda: _gaps(me, laps),
        "incidents": lambda: _incidents(session_key, me, race),
        "radio": lambda: _radio(session_key, me),
        "outcome": lambda: _outcome(me, race),
        "teammate": lambda: _teammate(me, mate),
    }
    questions = next((q for k, _, q in SECTIONS if k == section_key), [])
    if questions:
        st.caption("**Questions this section answers**")
        st.markdown("\n".join(f"- {q}" for q in questions))
        st.divider()
    blocks[section_key]()
