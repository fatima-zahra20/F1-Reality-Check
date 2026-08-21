"""
story_race.py - "Story of a Race": the race-wide narrative, in race order.

Answers the race-level questions from
DESCRIPTIVE ANALYTICS/descriptive_question_bank.md. The bank is written per
driver and per team; this view takes the whole field at once, so it keeps the
questions that describe the race and leaves the single-driver and
teammate-comparison ones to the other two stories.

Blocks follow the bank's own order, so the page reads chronologically:
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
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app_common import NEUTRAL, coverage_gaps, fmt_lap, query, team_colours
from story_common import (
    AXIS_BASE, CLEAN_LAP, FIGHTING_SECONDS, PLOT_BASE, ink,
    field as _field, guide as _guide, hbar as _bar, labels as _labels,
)


# --- 1. Pre-race, grid & setup -----------------------------------------------

def _grid_and_setup(session_key: int) -> None:
    st.subheader("Pre-race, grid and setup")
    st.caption(
        "What grid position each driver started from, the lap time that earned "
        "it, and how each team's two cars were split across the field."
    )

    grid = query("""
        SELECT f.driver_number, f.team_name, f.grid_position,
               f.quali_lap_seconds, f.dns, d.full_name, d.name_acronym
        FROM fact_driver_race f
        JOIN dim_race r ON r.session_key = f.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = f.driver_number AND d.year = r.year
        WHERE f.session_key = ?
        ORDER BY f.grid_position
    """, (session_key,))

    if grid.grid_position.notna().sum() == 0:
        st.info("No starting grid recorded for this race.")
        return

    on_grid = grid.dropna(subset=["grid_position"]).copy()
    pole = on_grid.iloc[0]
    pole_time = on_grid.quali_lap_seconds.min()
    # Gap to pole, not raw lap time: the absolute number means nothing without
    # knowing the circuit, the gap is comparable at a glance.
    on_grid["gap_to_pole"] = on_grid.quali_lap_seconds - pole_time

    c1, c2, c3 = st.columns(3)
    c1.metric("Pole", pole.name_acronym or f"#{int(pole.driver_number)}",
              pole.team_name)
    c2.metric("Pole lap", fmt_lap(pole_time))
    front_row = on_grid[on_grid.grid_position <= 2]
    lockout = front_row.team_name.nunique() == 1 and len(front_row) == 2
    c3.metric("Front row", " / ".join(front_row.name_acronym.fillna("?")),
              "lockout" if lockout else "split")

    st.dataframe(
        pd.DataFrame({
            "Grid": on_grid.grid_position.astype("Int64"),
            "Driver": on_grid.full_name,
            "Team": on_grid.team_name,
            "Qualifying lap": on_grid.quali_lap_seconds.map(fmt_lap),
            "Gap to pole": on_grid.gap_to_pole,
        }),
        hide_index=True, width="stretch",
        column_config={
            "Grid": st.column_config.NumberColumn(format="%d", width="small"),
            "Gap to pole": st.column_config.NumberColumn(
                format="+%.3f s",
                help="Against the fastest grid-setting lap in this race."),
        },
    )
    _guide(
        "Gap to pole is how far off the fastest grid-setting lap each driver "
        "was. A tight spread at the top means qualifying was close; a large "
        "jump partway down usually marks where one qualifying segment ended "
        "and the next began."
    )

    # The bank asks specifically which drivers hold a grid slot with no lap
    # time, and whether that is a genuine DNS or a session they took part in
    # without setting a time.
    no_time = on_grid[on_grid.quali_lap_seconds.isna()]
    if len(no_time):
        lines = [
            f"P{int(r.grid_position)} {r.full_name} "
            f"({'did not start' if r.dns else 'started, but no time recorded'})"
            for r in no_time.itertuples()
        ]
        st.warning("Grid slot with no qualifying lap time: " + "; ".join(lines))

    with st.expander("Team-level: how each team's cars were split"):
        split = (on_grid.groupby("team_name")
                        .agg(Best=("grid_position", "min"),
                             Worst=("grid_position", "max"),
                             Cars=("grid_position", "size"))
                        .reset_index().rename(columns={"team_name": "Team"}))
        split["Spread"] = split.Worst - split.Best
        st.dataframe(
            split.sort_values("Best"), hide_index=True, width="stretch",
            column_config={c: st.column_config.NumberColumn(
                format="%d", width="small")
                for c in ["Best", "Worst", "Cars", "Spread"]},
        )
        _guide(
            "Spread is the gap between a team's two cars on the grid. A small "
            "spread means both cars qualified together; a large one means the "
            "team's pace was split across the field."
        )


# --- 2. The start, lap 1 -------------------------------------------------------

def _start_lap1(session_key: int) -> None:
    st.subheader("The start, lap 1")
    st.caption(
        "Where the field ran after one racing lap, against where it started."
    )

    # fact_lap.position is sampled at the lap's start, so position at lap 1 is
    # the grid slot and position after lap 1 is carried on lap 2. A few
    # driver-races are missing individual lap rows, so this takes the earliest
    # lap from 2 onward rather than lap 2 exactly.
    lap1 = query("""
        SELECT l.position AS pos_after_lap1, l.driver_number,
               f.grid_position, f.team_name, d.full_name, d.name_acronym
        FROM fact_lap l
        JOIN fact_driver_race f
             ON f.session_key = l.session_key AND f.driver_number = l.driver_number
        JOIN dim_race r ON r.session_key = l.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = l.driver_number AND d.year = r.year
        WHERE l.session_key = ?
          AND l.lap_number = (
              SELECT MIN(l2.lap_number) FROM fact_lap l2
              WHERE l2.session_key = l.session_key
                AND l2.driver_number = l.driver_number
                AND l2.lap_number >= 2)
          AND f.grid_position IS NOT NULL
    """, (session_key,))

    if lap1.empty:
        st.info("No lap-by-lap position data recorded for this race.")
        return

    lap1["gained"] = (lap1.grid_position - lap1.pos_after_lap1).astype(int)
    lap1["label"] = _labels(lap1)
    lap1 = lap1.sort_values("gained")

    colours = team_colours()
    best, worst = lap1.iloc[-1], lap1.iloc[0]

    c1, c2, c3 = st.columns(3)
    c1.metric("Biggest gain", best.label, f"{best.gained:+d} places")
    c2.metric("Biggest loss", worst.label, f"{worst.gained:+d} places")
    c3.metric("Held position", f"{int((lap1.gained == 0).sum())} of {len(lap1)}")

    st.plotly_chart(
        _bar(lap1, "gained", "label",
             [colours.get(t, NEUTRAL) for t in lap1.team_name],
             lap1[["full_name", "team_name"]],
             xtitle="Places gained on lap 1", zeroline=True),
        width="stretch",
    )
    _guide(
        "Each bar is one driver. Bars right of the centre line gained places "
        "off the line, bars left of it lost places. Bar colour is the team. "
        "A long bar in either direction usually means a strong start, a poor "
        "one, or first-lap contact."
    )

    opening = query("""
        SELECT lap_number, detail, driver_number
        FROM fact_event
        WHERE session_key = ? AND lap_number <= 2
          AND event_type IN ('race_control', 'race_control_driver')
        ORDER BY lap_number
    """, (session_key,))

    if len(opening):
        with st.expander(f"Race control in the opening laps ({len(opening)})"):
            st.dataframe(
                opening.rename(columns={"lap_number": "Lap", "detail": "Message",
                                        "driver_number": "Car"}),
                hide_index=True, width="stretch",
                column_config={
                    "Lap": st.column_config.NumberColumn(format="%d", width="small"),
                    "Car": st.column_config.NumberColumn(format="%d", width="small"),
                },
            )
    else:
        st.caption("No race control messages in the opening laps.")


# --- 3. Race pace, lap by lap --------------------------------------------------

def _race_pace(session_key: int) -> None:
    st.subheader("Race pace, lap by lap")
    st.caption(
        "The field's median lap time on every lap, and the fastest lap of the "
        "race."
    )

    laps = query("""
        SELECT lap_number, lap_duration, neutralised, is_pit_out_lap
        FROM fact_lap
        WHERE session_key = ? AND lap_duration IS NOT NULL
    """, (session_key,))

    if laps.empty:
        st.info("No lap times recorded for this race.")
        return

    clean = laps[(laps.neutralised == 0) & (laps.is_pit_out_lap == 0)]
    per_lap = (laps.groupby("lap_number")
                   .agg(median_lap=("lap_duration", "median"),
                        neutral_share=("neutralised", "mean"))
                   .reset_index())
    # A lap counts as neutralised when most of the field was under it, not
    # when a single car happened to be.
    per_lap["is_neutral"] = per_lap.neutral_share > 0.5

    fastest = query(f"""
        SELECT l.lap_number, l.lap_duration, l.compound, l.tyre_age,
               d.name_acronym
        FROM fact_lap l
        JOIN dim_race r ON r.session_key = l.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = l.driver_number AND d.year = r.year
        WHERE l.session_key = ? AND l.lap_duration IS NOT NULL AND {CLEAN_LAP}
        ORDER BY l.lap_duration
        LIMIT 1
    """, (session_key,))

    c1, c2, c3 = st.columns(3)
    if len(fastest):
        f = fastest.iloc[0]
        c1.metric("Fastest lap", fmt_lap(f.lap_duration),
                  f"{f.name_acronym or ''}, lap {int(f.lap_number)}")
        # Per-lap compound is only about a third populated across the archive,
        # so say it is unrecorded rather than showing a bare dash that reads
        # like the driver was on no tyre at all.
        if pd.notna(f.compound):
            c2.metric("Set on", str(f.compound).title(),
                      f"{int(f.tyre_age)} lap old tyre"
                      if pd.notna(f.tyre_age) else None)
        else:
            c2.metric("Set on", "Not recorded", "tyre data missing for this lap")
    c3.metric("Green-flag laps", f"{len(clean):,}", f"of {len(laps):,} timed")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=per_lap.lap_number, y=per_lap.median_lap, mode="lines",
        line=dict(color=ink(), width=2), name="Field median",
        hovertemplate="Lap %{x}<br>%{y:.3f}s<extra></extra>",
    ))
    neutral = per_lap[per_lap.is_neutral]
    if len(neutral):
        fig.add_trace(go.Scatter(
            x=neutral.lap_number, y=neutral.median_lap, mode="markers",
            marker=dict(color="#E10600", size=7), name="Neutralised",
            hovertemplate="Lap %{x}<br>%{y:.3f}s<br>neutralised<extra></extra>",
        ))
    fig.update_layout(
        height=380,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        xaxis=dict(title="Lap", **AXIS_BASE),
        yaxis=dict(title="Median lap time (s)", **AXIS_BASE),
        **PLOT_BASE,
    )
    st.plotly_chart(fig, width="stretch")
    _guide(
        "Lower is faster. The line generally falls through the race as fuel "
        "burns off, and spikes upward where the field slowed. Red dots are "
        "laps run under a safety car, virtual safety car or red flag, so those "
        "spikes are not a loss of pace."
    )


# --- 4. Tyre strategy ----------------------------------------------------------

def _tyre_strategy(session_key: int) -> None:
    st.subheader("Tyre strategy")
    st.caption(
        "Which compound sequences the field ran, and how many stints each "
        "strategy took."
    )

    field = _field(session_key)
    strat = field.dropna(subset=["compound_sequence"]).copy()
    if strat.empty:
        st.info("No stint data recorded for this race.")
        return

    grouped = (strat.groupby("compound_sequence")
                    .agg(drivers=("driver_number", "size"),
                         stints=("stint_count", "max"),
                         best_finish=("finish_position", "min"),
                         median_finish=("finish_position", "median"))
                    .reset_index()
                    .sort_values(["drivers", "best_finish"],
                                 ascending=[False, True]))

    c1, c2, c3 = st.columns(3)
    c1.metric("Strategies used", f"{len(grouped)}",
              f"across {len(strat)} cars")
    most = grouped.iloc[0]
    c2.metric("Most common", most.compound_sequence,
              f"{int(most.drivers)} cars")
    winner_strat = strat[strat.finish_position == 1]
    c3.metric("Winning strategy",
              winner_strat.compound_sequence.iloc[0] if len(winner_strat) else "-")

    st.dataframe(
        grouped.rename(columns={
            "compound_sequence": "Compound sequence", "drivers": "Cars",
            "stints": "Stints", "best_finish": "Best finish",
            "median_finish": "Median finish"}),
        hide_index=True, width="stretch",
        column_config={
            "Cars": st.column_config.NumberColumn(format="%d", width="small"),
            "Stints": st.column_config.NumberColumn(format="%d", width="small"),
            "Best finish": st.column_config.NumberColumn(
                format="%d", width="small"),
            "Median finish": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    _guide(
        "Each row is one strategy, read left to right in the order the tyres "
        "were fitted. Cars is how many drivers ran it. Comparing best finish "
        "against median finish shows whether a strategy worked for everyone or "
        "only for the car that was already fast."
    )


# --- 5. Pit stops --------------------------------------------------------------

def _pit_stops(session_key: int) -> None:
    st.subheader("Pit stops")
    st.caption(
        "When the field stopped, how long each stop took, and which stops went "
        "wrong."
    )

    stops = query("""
        SELECT e.lap_number, e.value AS lane_seconds, e.driver_number,
               d.full_name, d.name_acronym, f.team_name
        FROM fact_event e
        JOIN fact_driver_race f
             ON f.session_key = e.session_key AND f.driver_number = e.driver_number
        JOIN dim_race r ON r.session_key = e.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = e.driver_number AND d.year = r.year
        WHERE e.session_key = ? AND e.event_type = 'pit_stop'
        ORDER BY e.lap_number
    """, (session_key,))

    if stops.empty:
        st.info(
            "No pit stop data recorded for this race. Coverage is incomplete: "
            f"{coverage_gaps('pit_stop')} have no pit records."
        )
        return

    # "Unusually long" is derived per race from the field's own spread, never a
    # fixed number of seconds: pit lanes differ enormously between circuits.
    # Upper Tukey fence, the same rule the diagnostic layer uses.
    valid = stops.lane_seconds.dropna()
    q1, q3 = valid.quantile([0.25, 0.75])
    fence = q3 + 1.5 * (q3 - q1)
    disasters = stops[stops.lane_seconds > fence].sort_values(
        "lane_seconds", ascending=False)

    # A multi-minute "stop" is a car held in the lane during a red-flag
    # suspension, not slow pit work (NOTES_LOG #18). Those values are real and
    # stay in the data, but reporting one as the slowest stop would be
    # nonsense, so the headline uses the slowest plausible stop instead.
    RED_FLAG_SECONDS = 120
    racing_stops = valid[valid <= RED_FLAG_SECONDS]
    suspended = int((valid > RED_FLAG_SECONDS).sum())

    c1, c2, c3 = st.columns(3)
    c1.metric("Stops made", f"{len(stops)}")
    c2.metric("Median time in lane", f"{valid.median():.1f}s",
              f"slowest {racing_stops.max():.1f}s" if len(racing_stops) else None)
    c3.metric("Unusually long", f"{len(disasters)}", f"over {fence:.1f}s")

    if suspended:
        st.caption(
            f"{suspended} car{'s' if suspended > 1 else ''} spent over "
            f"{RED_FLAG_SECONDS}s in the lane, which is a red-flag suspension "
            "rather than a pit stop. Those are excluded from the slowest-stop "
            "figure above but remain in the counts and the table below."
        )

    by_lap = stops.groupby("lap_number").size().reset_index(name="stops")
    fig = go.Figure(go.Bar(
        x=by_lap.lap_number, y=by_lap.stops, marker_color="#E10600",
        hovertemplate="Lap %{x}<br>%{y} stops<extra></extra>",
    ))
    fig.update_layout(
        height=300,
        xaxis=dict(title="Lap", **AXIS_BASE),
        yaxis=dict(title="Cars stopping", **AXIS_BASE),
        **PLOT_BASE,
    )
    st.plotly_chart(fig, width="stretch")
    _guide(
        "Each bar counts how many cars pitted on that lap. Tall clusters are "
        "the pit windows, where most of the field stopped within a few laps of "
        "each other. A cluster on one lap often follows a safety car, when "
        "stopping costs less time."
    )

    if len(disasters):
        with st.expander(f"Unusually long stops ({len(disasters)})"):
            st.dataframe(
                pd.DataFrame({
                    "Lap": disasters.lap_number.astype("Int64"),
                    "Driver": disasters.full_name,
                    "Team": disasters.team_name,
                    "Time in lane": disasters.lane_seconds,
                }),
                hide_index=True, width="stretch",
                column_config={
                    "Lap": st.column_config.NumberColumn(format="%d", width="small"),
                    "Time in lane": st.column_config.NumberColumn(format="%.1f s"),
                },
            )
            _guide(
                f"These stops exceeded {fence:.1f}s, the point at which this "
                "race's own stop times stop looking routine. The threshold is "
                "recalculated per race, since pit lanes differ in length. "
                "Times running to several minutes are cars held in the lane "
                "under a red flag, not slow pit work."
            )


# --- 6. Position dynamics ------------------------------------------------------

def _position_dynamics(session_key: int) -> None:
    st.subheader("Position dynamics")
    st.caption("How the order changed across the full race distance.")

    trace = query("""
        SELECT l.lap_number, l.position, l.driver_number,
               f.team_name, d.full_name, d.name_acronym
        FROM fact_lap l
        JOIN fact_driver_race f
             ON f.session_key = l.session_key AND f.driver_number = l.driver_number
        JOIN dim_race r ON r.session_key = l.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = l.driver_number AND d.year = r.year
        WHERE l.session_key = ?
        ORDER BY l.driver_number, l.lap_number
    """, (session_key,))

    if trace.empty:
        st.info("No lap-by-lap position data recorded for this race.")
        return

    colours = team_colours()
    field = _field(session_key)

    c1, c2, c3 = st.columns(3)
    if field.overtakes_made.notna().any():
        top = field.loc[field.overtakes_made.idxmax()]
        c1.metric("Most overtakes made", top.name_acronym or "-",
                  f"{int(top.overtakes_made)}")
        c2.metric("Total overtakes", f"{int(field.overtakes_made.sum())}")
    swings = field.dropna(subset=["position_change"])
    if len(swings):
        biggest = swings.loc[swings.position_change.abs().idxmax()]
        c3.metric("Biggest net move", biggest.name_acronym or "-",
                  f"{int(biggest.position_change):+d} places")

    fig = go.Figure()
    for num, g in trace.groupby("driver_number"):
        g = g.sort_values("lap_number")
        name = g.name_acronym.iloc[0] or f"#{num}"
        fig.add_trace(go.Scatter(
            x=g.lap_number, y=g.position, mode="lines", name=name,
            line=dict(color=colours.get(g.team_name.iloc[0], NEUTRAL), width=1.5),
            hovertemplate=f"<b>{g.full_name.iloc[0]}</b><br>"
                          "Lap %{x}, P%{y}<extra></extra>",
        ))
    fig.update_layout(
        height=520, showlegend=False,
        xaxis=dict(title="Lap", **AXIS_BASE),
        yaxis=dict(title="Position", autorange="reversed", dtick=2, **AXIS_BASE),
        **PLOT_BASE,
    )
    st.plotly_chart(fig, width="stretch")
    _guide(
        "One line per driver, coloured by team. First place is at the top, so "
        "a line rising means a driver moving forward. Steep vertical moves are "
        "usually pit stops rather than overtakes, since a car rejoins behind "
        "the cars that have not stopped yet. Hover any line to see who it is. "
        "The overtake counts above come from the API's own overtake feed, "
        "which also records pit-cycle position gains and post-race penalty "
        "swaps, so treat them as position changes rather than strictly "
        "on-track passes."
    )


# --- 7. Gaps & race context ----------------------------------------------------

def _gaps(session_key: int) -> None:
    st.subheader("Gaps and race context")
    st.caption(
        "Whether drivers spent the race fighting the car ahead, running alone, "
        "or being lapped."
    )

    gaps = query(f"""
        SELECT l.driver_number, l.interval_seconds, l.gap_to_leader_seconds,
               f.team_name, d.full_name, d.name_acronym
        FROM fact_lap l
        JOIN fact_driver_race f
             ON f.session_key = l.session_key AND f.driver_number = l.driver_number
        JOIN dim_race r ON r.session_key = l.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = l.driver_number AND d.year = r.year
        WHERE l.session_key = ? AND {CLEAN_LAP}
          AND l.interval_seconds IS NOT NULL
    """, (session_key,))

    if gaps.empty:
        st.info("No interval data recorded for this race.")
        return

    per_driver = (gaps.assign(fighting=gaps.interval_seconds.between(
                        0.001, FIGHTING_SECONDS))
                      .groupby("driver_number")
                      .agg(laps=("interval_seconds", "size"),
                           fighting_laps=("fighting", "sum"),
                           median_gap=("interval_seconds", "median"),
                           team_name=("team_name", "first"),
                           full_name=("full_name", "first"),
                           name_acronym=("name_acronym", "first"))
                      .reset_index())
    per_driver["label"] = _labels(per_driver)
    per_driver = per_driver.sort_values("fighting_laps")

    field = _field(session_key)
    lapped = field[field.was_lapped == 1]

    colours = team_colours()
    c1, c2, c3 = st.columns(3)
    top = per_driver.iloc[-1]
    c1.metric("Most laps in a fight", top.label,
              f"{int(top.fighting_laps)} laps within {FIGHTING_SECONDS:g}s")
    c2.metric("Field median gap", f"{gaps.interval_seconds.median():.2f}s",
              "to the car ahead")
    c3.metric("Lapped drivers", f"{len(lapped)}")

    st.plotly_chart(
        _bar(per_driver, "fighting_laps", "label",
             [colours.get(t, NEUTRAL) for t in per_driver.team_name],
             per_driver[["full_name", "team_name"]],
             xtitle=f"Green-flag laps within {FIGHTING_SECONDS:g}s of the car ahead"),
        width="stretch",
    )
    _guide(
        f"Counts only green-flag laps where a driver ran within "
        f"{FIGHTING_SECONDS:g} second of the car ahead, the point at which "
        "attacking becomes possible. A long bar means a race spent in traffic "
        "or in a battle; a short bar means clear air, which can mean either "
        "leading comfortably or being dropped."
    )

    if len(lapped):
        st.caption(
            "Lapped at least once: "
            + ", ".join(sorted(lapped.name_acronym.dropna().astype(str)))
        )


# --- 8. Incidents & external context -------------------------------------------

def _incidents(session_key: int, race) -> None:
    st.subheader("Incidents and conditions")
    st.caption(
        "Safety car and flag periods, race control messages, and the weather "
        "the race was run in."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Safety cars", int(race.safety_car_periods))
    c2.metric("Virtual safety cars", int(race.vsc_periods))
    c3.metric("Red flags", int(race.red_flag_periods))
    wet_pct = race.pct_samples_wet if pd.notna(race.pct_samples_wet) else 0
    c4.metric("Track", "Wet" if race.is_wet_race else "Dry",
              f"{wet_pct:.0f}% of samples wet")

    st.caption(
        f"Air {race.avg_air_temp:.0f}°C, track {race.avg_track_temp:.0f}°C "
        "on average across the race."
        if pd.notna(race.avg_air_temp) and pd.notna(race.avg_track_temp)
        else "Temperature not recorded for this race."
    )
    st.caption(
        "Weather is stored as a race-level average, so a race that started dry "
        "and finished wet shows only its overall share of wet samples, not the "
        "moment it changed."
    )

    events = query("""
        SELECT lap_number, detail, driver_number, event_type
        FROM fact_event
        WHERE session_key = ?
          AND event_type IN ('race_control', 'race_control_driver')
        ORDER BY lap_number
    """, (session_key,))

    if events.empty:
        st.caption("No race control messages recorded for this race.")
        return

    by_lap = events.groupby("lap_number").size().reset_index(name="messages")
    fig = go.Figure(go.Bar(
        x=by_lap.lap_number, y=by_lap.messages, marker_color="#E10600",
        hovertemplate="Lap %{x}<br>%{y} messages<extra></extra>",
    ))
    fig.update_layout(
        height=260,
        xaxis=dict(title="Lap", **AXIS_BASE),
        yaxis=dict(title="Race control messages", **AXIS_BASE),
        **PLOT_BASE,
    )
    st.plotly_chart(fig, width="stretch")
    _guide(
        "Each bar counts race control messages issued on that lap. Tall bars "
        "mark the moments officials were most active: an incident, a safety "
        "car, or a cluster of blue flags as the leaders lap backmarkers."
    )

    with st.expander(f"All race control messages ({len(events)})"):
        st.dataframe(
            events.rename(columns={"lap_number": "Lap", "detail": "Message",
                                   "driver_number": "Car"})
                  [["Lap", "Message", "Car"]],
            hide_index=True, width="stretch",
            column_config={
                "Lap": st.column_config.NumberColumn(format="%d", width="small"),
                "Car": st.column_config.NumberColumn(format="%d", width="small"),
            },
        )


# --- 9. Team radio -------------------------------------------------------------

def _team_radio(session_key: int) -> None:
    st.subheader("Team radio")
    st.caption(
        "When radio traffic clustered. The messages are audio only, with no "
        "transcription, so volume is all that can be read from them."
    )

    radio = query("""
        SELECT e.driver_number, e.event_time, d.name_acronym, f.team_name
        FROM fact_event e
        JOIN fact_driver_race f
             ON f.session_key = e.session_key AND f.driver_number = e.driver_number
        JOIN dim_race r ON r.session_key = e.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = e.driver_number AND d.year = r.year
        WHERE e.session_key = ? AND e.event_type = 'team_radio'
    """, (session_key,))

    if radio.empty:
        st.info(
            "No team radio recorded for this race. Coverage is incomplete: "
            f"{coverage_gaps('team_radio')} have none."
        )
        return

    # Radio events carry a timestamp but no lap number, so they can only be
    # counted per driver, not placed on the lap chart.
    per_driver = (radio.groupby("driver_number")
                       .agg(messages=("event_time", "size"),
                            team_name=("team_name", "first"),
                            name_acronym=("name_acronym", "first"))
                       .reset_index())
    per_driver["label"] = _labels(per_driver)
    per_driver = per_driver.sort_values("messages")

    colours = team_colours()
    c1, c2 = st.columns(2)
    c1.metric("Messages", f"{len(radio)}")
    busiest = per_driver.iloc[-1]
    c2.metric("Most traffic", busiest.label, f"{int(busiest.messages)} messages")

    st.plotly_chart(
        _bar(per_driver, "messages", "label",
             [colours.get(t, NEUTRAL) for t in per_driver.team_name],
             per_driver[["label", "team_name"]],
             xtitle="Radio messages"),
        width="stretch",
    )
    _guide(
        "Message count per driver, not content. A high count often marks a "
        "driver with a problem, a strategy decision being worked through, or "
        "an incident under investigation, but without transcription this is a "
        "signal to look closer, not an explanation."
    )


# --- 10. Finish & outcome ------------------------------------------------------

def _outcome(session_key: int, race) -> None:
    st.subheader("Finish and outcome")
    st.caption("The classified result, and how it compares to the grid.")

    field = _field(session_key)
    winner = field[field.finish_position == 1]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Winner", winner.name_acronym.iloc[0] if len(winner) else "-",
              winner.team_name.iloc[0] if len(winner) else None)
    c2.metric("Retirements", int(race.dnf_count), f"of {int(race.entrants)} starters")
    classified = field.finish_position.notna().sum()
    c3.metric("Classified", f"{classified}")
    movers = field.dropna(subset=["position_change"])
    if len(movers):
        best = movers.loc[movers.position_change.idxmax()]
        c4.metric("Best recovery", best.name_acronym or "-",
                  f"{int(best.position_change):+d} places")

    def classification(row) -> str:
        """A blank position reads as missing data; say what actually happened."""
        if pd.notna(row.finish_position):
            return str(int(row.finish_position))
        if row.dsq:
            return "DSQ"
        if row.dns:
            return "DNS"
        return "DNF"

    from app_common import fmt_gap  # local: only this block formats gaps

    st.dataframe(
        pd.DataFrame({
            "Pos": field.apply(classification, axis=1),
            "Driver": field.full_name,
            "Team": field.team_name,
            "Grid": field.grid_position,
            "Gained": field.position_change.astype("Int64"),
            "Gap": [fmt_gap(s, l) for s, l in
                    zip(field.gap_to_leader_seconds, field.gap_to_leader_laps)],
            "Best lap": field.fastest_lap.map(fmt_lap),
            "Pace vs median": field.pace_vs_session_median,
            "Stops": field.pit_stops,
            "Tyres": field.compound_sequence,
            "Pts": field.points,
        }),
        hide_index=True, width="stretch",
        column_config={
            "Pos": st.column_config.TextColumn(width="small"),
            "Grid": st.column_config.NumberColumn(format="%d", width="small"),
            "Gained": st.column_config.NumberColumn(
                format="%+d", width="small",
                help="Places gained from the grid. Negative means places lost."),
            "Pace vs median": st.column_config.NumberColumn(
                format="%+.3f s",
                help="Mean clean lap against the session median. "
                     "Negative is faster."),
            "Stops": st.column_config.NumberColumn(format="%d", width="small"),
            "Pts": st.column_config.NumberColumn(format="%d", width="small"),
        },
    )
    _guide(
        "Gained is grid position minus finish position, so positive means "
        "places won. Pace vs median compares a driver's average green-flag lap "
        "to the middle of the field that day, which makes it comparable across "
        "circuits in a way a raw lap time is not. Negative is faster."
    )


# --- sections ------------------------------------------------------------------

# One entry per file in DESCRIPTIVE ANALYTICS, in the same order. The questions
# are the bank's own, adapted from "the driver" to the whole field, which is
# what this story answers.
SECTIONS = [
    ("grid_setup", "Pre-race, grid and setup", [
        "What grid position did each driver start from, and what lap time earned it?",
        "Which drivers have a grid position but no recorded qualifying lap time, "
        "and which of those are genuine DNS cases?",
        "What was each team's combined grid position, a front-row lockout or "
        "split across the field?",
    ]),
    ("lap1", "The start, lap 1", [
        "How did each driver's position change from their grid slot to the end "
        "of lap 1, and how many places did they gain or lose?",
        "Did any race control flag or incident fire in the opening laps?",
    ]),
    ("lap_by_lap", "Race pace, lap by lap", [
        "What was the field's lap time trend across the race?",
        "Were there specific laps with anomalous times, and where do they fall?",
        "What was the fastest lap of the race, and on which lap number and "
        "tyre compound did it occur?",
    ]),
    ("tyres", "Tyre strategy", [
        "How many stints did the field run, on which compounds?",
        "Which compound sequences were used, and how did each finish?",
    ]),
    ("pit_stops", "Pit stops", [
        "How many pit stops were made, and on which laps?",
        "What was the total lane duration for each stop?",
        "Did any stop go unusually long, a disaster stop?",
    ]),
    ("position", "Position dynamics", [
        "How did positions evolve over the full race distance?",
        "How many overtakes were made, and by whom?",
        "At what points did the biggest position swings happen?",
    ]),
    ("gaps", "Gaps and race context", [
        "How did gaps to the car ahead evolve, fighting, isolated, or lapped?",
        "Which drivers were lapped by the leader?",
    ]),
    ("incidents", "Incidents and conditions", [
        "What race control events occurred during the race?",
        "What were the weather conditions, and did they change mid-race?",
    ]),
    ("radio", "Team radio", [
        "How many radio messages were sent, and for which drivers do they cluster?",
    ]),
    ("outcome", "Finish and outcome", [
        "What was each final classified position, and how does it compare to "
        "the grid slot?",
        "Who finished, DNF'd, DNS'd or was disqualified?",
        "How many points did each driver score, and what was the gap to the winner?",
    ]),
]


def section_options() -> list[tuple[str, str]]:
    return [(key, title) for key, title, _ in SECTIONS]


# --- entry point ---------------------------------------------------------------

def render(race, section_key: str) -> None:
    session_key = int(race.session_key)
    blocks = {
        "grid_setup": lambda: _grid_and_setup(session_key),
        "lap1": lambda: _start_lap1(session_key),
        "lap_by_lap": lambda: _race_pace(session_key),
        "tyres": lambda: _tyre_strategy(session_key),
        "pit_stops": lambda: _pit_stops(session_key),
        "position": lambda: _position_dynamics(session_key),
        "gaps": lambda: _gaps(session_key),
        "incidents": lambda: _incidents(session_key, race),
        "radio": lambda: _team_radio(session_key),
        "outcome": lambda: _outcome(session_key, race),
    }
    questions = next((q for k, _, q in SECTIONS if k == section_key), [])
    if questions:
        st.caption("**Questions this section answers**")
        st.markdown("\n".join(f"- {q}" for q in questions))
        st.divider()
    blocks[section_key]()
