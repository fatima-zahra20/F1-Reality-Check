"""
story_team.py - "Story of a Team": both cars in one race, side by side.

Answers the team-level questions from
DESCRIPTIVE ANALYTICS/descriptive_question_bank.md, plus the whole "Driver vs
teammate" section at full depth. Story of a Driver already answers the
teammate questions the bank asks inside its driver-level sections; this view
is the complete side-by-side.

Blocks follow the bank's own order:
    Pre-race, grid & setup
    The start, lap 1
    Race pace, lap by lap
    Tyre strategy
    Pit stops
    Position dynamics
    Incidents
    Finish & outcome
    Constructor standings
    Head to head
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app_common import fmt_gap, fmt_lap, query, team_colours
from story_common import (
    ACCENT, AXIS_BASE, CLEAN_LAP, MUTED, PLOT_BASE, ink,
    field, guide, line_layout,
)

# The two cars need to be told apart on a shared chart. The team's own colour
# would make them identical, so one takes it and the other is muted.
#
# A function, not a constant: the first colour follows the active theme, and a
# module-level tuple would be built once at import and keep the light value for
# the life of the server process.
def car_colours() -> tuple[str, str]:
    return (ink(), MUTED)


def _cars(session_key: int, team: str) -> pd.DataFrame:
    """Both cars, ordered by finish so the better result reads first."""
    everyone = field(session_key)
    return everyone[everyone.team_name == team].reset_index(drop=True)


def _laps(session_key: int, driver_number: int) -> pd.DataFrame:
    return query("""
        SELECT lap_number, lap_duration, is_pit_out_lap, neutralised,
               stint_number, compound, tyre_age, position
        FROM fact_lap
        WHERE session_key = ? AND driver_number = ?
        ORDER BY lap_number
    """, (session_key, driver_number))


def _label(car: pd.Series) -> str:
    return car.name_acronym or f"#{int(car.driver_number)}"


def _pair_metrics(cars: pd.DataFrame, col: str, fmt: str = "{:.0f}",
                  label: str = "") -> None:
    """Two columns, one per car, for a single measure."""
    cols = st.columns(len(cars))
    for c, (_, car) in zip(cols, cars.iterrows()):
        v = car[col]
        c.metric(f"{_label(car)} {label}".strip(),
                 fmt.format(v) if pd.notna(v) else "-")


# --- 1. Pre-race, grid & setup -------------------------------------------------

def _grid(cars: pd.DataFrame) -> None:
    st.subheader("Pre-race, grid and setup")
    st.caption("Where both cars started, and how far apart they qualified.")

    on_grid = cars.dropna(subset=["grid_position"])
    if on_grid.empty:
        st.info("No grid positions recorded for this team.")
        return

    best, worst = on_grid.grid_position.min(), on_grid.grid_position.max()
    lockout = len(on_grid) == 2 and worst <= 2

    c1, c2, c3 = st.columns(3)
    c1.metric("Best grid slot", f"P{int(best)}")
    c2.metric("Grid spread", f"{int(worst - best)} places",
              "front row lockout" if lockout else None)
    if len(on_grid) == 2 and on_grid.quali_lap_seconds.notna().all():
        gap = abs(on_grid.quali_lap_seconds.diff().iloc[-1])
        c3.metric("Qualifying gap between cars", f"{gap:.3f}s")

    st.dataframe(
        pd.DataFrame({
            "Driver": cars.full_name,
            "Grid": cars.grid_position.astype("Int64"),
            "Qualifying lap": cars.quali_lap_seconds.map(fmt_lap),
        }),
        hide_index=True, width="stretch",
        column_config={"Grid": st.column_config.NumberColumn(
            format="%d", width="small")},
    )
    guide(
        "Grid spread is the gap between the team's two cars on the grid. A "
        "spread of 1 with both cars at the front is a lockout; a large spread "
        "means one car found pace the other did not, which is usually setup, "
        "traffic, or a session ended early by a red flag."
    )


# --- 2. The start, lap 1 -------------------------------------------------------

def _start(session_key: int, cars: pd.DataFrame) -> None:
    st.subheader("The start, lap 1")
    st.caption("What the opening lap did to each car.")

    rows = []
    for _, car in cars.iterrows():
        laps = _laps(session_key, int(car.driver_number))
        after = laps[laps.lap_number >= 2]
        pos = after.position.iloc[0] if len(after) else np.nan
        rows.append({
            "Driver": car.full_name,
            "Grid": car.grid_position,
            "After lap 1": pos,
            "Places": (car.grid_position - pos)
                      if pd.notna(pos) and pd.notna(car.grid_position) else np.nan,
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df.astype({"Grid": "Int64", "After lap 1": "Int64", "Places": "Int64"}),
        hide_index=True, width="stretch",
        column_config={
            "Grid": st.column_config.NumberColumn(format="%d", width="small"),
            "After lap 1": st.column_config.NumberColumn(
                format="%d", width="small"),
            "Places": st.column_config.NumberColumn(format="%+d", width="small"),
        },
    )
    guide(
        "Places is grid position minus position after the opening lap, so "
        "positive means the car moved forward off the line. Both cars losing "
        "places usually points at a poor getaway from the whole team rather "
        "than one driver."
    )


# --- 3. Race pace, lap by lap --------------------------------------------------

def _pace(session_key: int, cars: pd.DataFrame) -> None:
    st.subheader("Race pace, lap by lap")
    st.caption("Both cars' green-flag lap times on one chart.")

    fig = go.Figure()
    any_data = False
    for colour, (_, car) in zip(car_colours(), cars.iterrows()):
        laps = _laps(session_key, int(car.driver_number))
        clean = laps[(laps.neutralised == 0) & (laps.is_pit_out_lap == 0)
                     & laps.lap_duration.notna()]
        if clean.empty:
            continue
        any_data = True
        fig.add_trace(go.Scatter(
            x=clean.lap_number, y=clean.lap_duration, mode="lines",
            name=_label(car), line=dict(color=colour, width=2),
            hovertemplate=f"<b>{_label(car)}</b><br>Lap %{{x}}"
                          "<br>%{y:.3f}s<extra></extra>",
        ))

    if not any_data:
        st.info("No green-flag lap times recorded for either car.")
        return

    _pair_metrics(cars, "mean_clean_lap", "{:.3f}s", "mean lap")
    if len(cars) == 2 and cars.mean_clean_lap.notna().all():
        delta = cars.mean_clean_lap.iloc[0] - cars.mean_clean_lap.iloc[1]
        faster = cars.iloc[0] if delta < 0 else cars.iloc[1]
        st.caption(
            f"{_label(faster)} was {abs(delta):.3f}s per lap faster on average "
            "across green-flag laps."
        )

    st.plotly_chart(line_layout(fig, "Lap", "Lap time (s)"), width="stretch")
    guide(
        "Lower is faster. Where the two lines run together the cars had the "
        "same pace; where one sits consistently below the other, that car was "
        "quicker. A line jumping up for a lap or two and then settling is "
        "usually a pit stop or traffic, not a change in the car."
    )


# --- 4. Tyre strategy ----------------------------------------------------------

def _tyres(cars: pd.DataFrame) -> None:
    st.subheader("Tyre strategy")
    st.caption("Whether the team ran both cars the same way, or split them.")

    seqs = cars.compound_sequence.dropna().unique()
    converged = len(seqs) == 1 and len(cars) == 2

    c1, c2 = st.columns(2)
    c1.metric("Strategy", "converged" if converged else "split")
    c2.metric("Stints",
              " / ".join(str(int(s)) for s in cars.stint_count.dropna())
              or "-")

    st.dataframe(
        pd.DataFrame({
            "Driver": cars.full_name,
            "Compound sequence": cars.compound_sequence.fillna("not recorded"),
            "Stints": cars.stint_count.astype("Int64"),
            "Finish": cars.finish_position.astype("Int64"),
        }),
        hide_index=True, width="stretch",
        column_config={
            "Stints": st.column_config.NumberColumn(format="%d", width="small"),
            "Finish": st.column_config.NumberColumn(format="%d", width="small"),
        },
    )
    guide(
        "Read each sequence left to right in the order the tyres were fitted. "
        "Converged means the team ran both cars on the same plan; split means "
        "they hedged, and comparing the two finish positions is the closest "
        "thing to a controlled test of which plan worked, though track "
        "position and traffic still confound it."
    )


# --- 5. Pit stops --------------------------------------------------------------

def _pits(session_key: int, cars: pd.DataFrame) -> None:
    st.subheader("Pit stops")
    st.caption("How the two cars' stops compared, and against the field.")

    field_stops = query("""
        SELECT value AS lane_seconds FROM fact_event
        WHERE session_key = ? AND event_type = 'pit_stop' AND value IS NOT NULL
    """, (session_key,))

    _pair_metrics(cars, "mean_lane_duration", "{:.1f}s", "average in lane")
    if len(field_stops):
        st.caption(
            f"Field median time in lane this race: "
            f"{field_stops.lane_seconds.median():.1f}s."
        )

    stops = query("""
        SELECT e.driver_number, e.lap_number, e.value AS lane_seconds,
               d.full_name
        FROM fact_event e
        JOIN dim_race r ON r.session_key = e.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = e.driver_number AND d.year = r.year
        WHERE e.session_key = ? AND e.event_type = 'pit_stop'
          AND e.driver_number IN ({})
        ORDER BY e.lap_number
    """.format(",".join(str(int(n)) for n in cars.driver_number)),
        (session_key,))

    if stops.empty:
        st.caption(
            "No individual pit records for either car. Pit coverage is "
            "incomplete in 2023, where 6 of 22 races have none."
        )
        return

    st.dataframe(
        stops.rename(columns={"full_name": "Driver", "lap_number": "Lap",
                              "lane_seconds": "Time in lane"})
             [["Driver", "Lap", "Time in lane"]],
        hide_index=True, width="stretch",
        column_config={
            "Lap": st.column_config.NumberColumn(format="%d", width="small"),
            "Time in lane": st.column_config.NumberColumn(format="%.1f s"),
        },
    )
    guide(
        "Time in lane is the full pit lane transit, including the speed limit, "
        "not just the stationary time. Two cars stopping on the same lap means "
        "a double stack, which necessarily costs the second car time. Values "
        "running to minutes are a red-flag suspension, not slow pit work."
    )


# --- 6. Position dynamics ------------------------------------------------------

def _positions(session_key: int, cars: pd.DataFrame) -> None:
    st.subheader("Position dynamics")
    st.caption("Where both cars ran through the race.")

    fig = go.Figure()
    any_data = False
    for colour, (_, car) in zip(car_colours(), cars.iterrows()):
        laps = _laps(session_key, int(car.driver_number)).dropna(subset=["position"])
        if laps.empty:
            continue
        any_data = True
        fig.add_trace(go.Scatter(
            x=laps.lap_number, y=laps.position, mode="lines",
            name=_label(car), line=dict(color=colour, width=2),
            hovertemplate=f"<b>{_label(car)}</b><br>Lap %{{x}}, "
                          "P%{y}<extra></extra>",
        ))

    if not any_data:
        st.info("No lap-by-lap position data for either car.")
        return

    st.plotly_chart(
        line_layout(fig, "Lap", "Position", height=420, reverse_y=True),
        width="stretch",
    )
    guide(
        "First place is at the top, so a line rising means moving forward. "
        "Where the two lines cross, the cars swapped places. Sharp drops are "
        "usually pit stops rather than being passed, since a car rejoins "
        "behind everyone who has not stopped yet."
    )


# --- 7. Incidents --------------------------------------------------------------

def _incidents(session_key: int, cars: pd.DataFrame) -> None:
    st.subheader("Incidents")
    st.caption("Race control messages naming each car.")

    counts = query("""
        SELECT driver_number, COUNT(*) AS n
        FROM fact_event
        WHERE session_key = ?
          AND event_type IN ('race_control', 'race_control_driver')
        GROUP BY driver_number
    """, (session_key,))

    cols = st.columns(len(cars))
    for c, (_, car) in zip(cols, cars.iterrows()):
        row = counts[counts.driver_number == car.driver_number]
        c.metric(f"{_label(car)} messages",
                 int(row.n.iloc[0]) if len(row) else 0)

    st.caption(
        "Blue flags dominate these counts for cars being lapped, so a high "
        "number often means a slow race rather than a troubled one."
    )


# --- 8. Finish & outcome -------------------------------------------------------

def _outcome(cars: pd.DataFrame, race) -> None:
    st.subheader("Finish and outcome")
    st.caption("Both results, and the combined points haul for the race.")

    def classification(row) -> str:
        if pd.notna(row.finish_position):
            return f"P{int(row.finish_position)}"
        if row.dsq:
            return "DSQ"
        if row.dns:
            return "DNS"
        return "DNF"

    total = cars.points.fillna(0).sum()
    both_finished = cars.finish_position.notna().all()

    c1, c2, c3 = st.columns(3)
    c1.metric("Combined points", f"{total:.0f}")
    c2.metric("Both cars classified", "yes" if both_finished else "no")
    best = cars.finish_position.min()
    c3.metric("Best finish", f"P{int(best)}" if pd.notna(best) else "-")

    st.dataframe(
        pd.DataFrame({
            "Driver": cars.full_name,
            "Result": cars.apply(classification, axis=1),
            "Grid": cars.grid_position.astype("Int64"),
            "Gained": cars.position_change.astype("Int64"),
            "Gap": [fmt_gap(s, l) for s, l in
                    zip(cars.gap_to_leader_seconds, cars.gap_to_leader_laps)],
            "Points": cars.points,
        }),
        hide_index=True, width="stretch",
        column_config={
            "Grid": st.column_config.NumberColumn(format="%d", width="small"),
            "Gained": st.column_config.NumberColumn(format="%+d", width="small"),
            "Points": st.column_config.NumberColumn(format="%d", width="small"),
        },
    )


# --- 9. Constructor standings --------------------------------------------------

def _standings(session_key: int, team: str) -> None:
    st.subheader("Constructor standings")
    st.caption("Where the team sat in the championship around this race.")

    row = query("""
        SELECT position_start, position_current, points_start, points_current,
               points_gained, positions_gained
        FROM fact_championship
        WHERE session_key = ? AND team_name = ?
    """, (session_key, team))

    if row.empty:
        st.info(
            "No championship standings recorded for this race. Coverage is "
            "complete for 2023 to 2025 but reaches only 8 of 11 races in 2026."
        )
        return

    r = row.iloc[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Championship position",
              f"P{int(r.position_current)}" if pd.notna(r.position_current) else "-",
              f"was P{int(r.position_start)}" if pd.notna(r.position_start)
              else "season opener, no prior standing")
    c2.metric("Championship points",
              f"{r.points_current:.0f}" if pd.notna(r.points_current) else "-",
              f"was {r.points_start:.0f}" if pd.notna(r.points_start) else None)
    c3.metric("Movement",
              f"{r.points_gained:+.0f} points" if pd.notna(r.points_gained) else "-",
              f"{int(r.positions_gained):+d} places"
              if pd.notna(r.positions_gained) else None)

    st.caption(
        "These standings are recorded as the session ends, before stewards "
        "apply any post-race penalty, and on a sprint weekend they cover the "
        "whole weekend. So this movement can differ from the combined points "
        "above, which is the final classified result. Both are correct, they "
        "answer different questions."
    )


# --- 10. Head to head ----------------------------------------------------------

def _head_to_head(cars: pd.DataFrame) -> None:
    st.subheader("Head to head")

    if len(cars) < 2:
        st.info(
            "Only one car from this team was classified in this race, so there "
            "is nothing to compare."
        )
        return

    a, b = cars.iloc[0], cars.iloc[1]
    name_a, name_b = _label(a), _label(b)

    rows = []
    for label, col, lower_better, fmt in [
        ("Grid", "grid_position", True, "{:.0f}"),
        ("Finish", "finish_position", True, "{:.0f}"),
        ("Points", "points", False, "{:.0f}"),
        ("Mean green-flag lap", "mean_clean_lap", True, "{:.3f}"),
        ("Fastest lap", "fastest_lap", True, "{:.3f}"),
        ("Pit stops", "pit_stops", True, "{:.0f}"),
        ("Overtakes made", "overtakes_made", False, "{:.0f}"),
        ("Overtakes suffered", "overtakes_suffered", True, "{:.0f}"),
    ]:
        va, vb = a[col], b[col]
        if pd.isna(va) and pd.isna(vb):
            continue
        if pd.isna(va) or pd.isna(vb):
            better = "-"
        elif va == vb:
            better = "level"
        else:
            better = name_a if (va < vb) == lower_better else name_b
        rows.append({
            "Measure": label,
            name_a: fmt.format(va) if pd.notna(va) else "-",
            name_b: fmt.format(vb) if pd.notna(vb) else "-",
            "Ahead": better,
        })

    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    guide(
        "Ahead names whichever car came out better on each measure, with the "
        "sense built in: lower grid, finish and lap times are good, more "
        "points and overtakes made are good, while fewer overtakes suffered "
        "and fewer stops are good. Pit stop count is a strategy choice rather "
        "than a verdict on the driver. This is the only comparison in the data "
        "that holds the car roughly constant, which is what makes it the "
        "fairest read on the two drivers available."
    )


# --- sections ------------------------------------------------------------------

# The bank's team-level questions, plus its whole "Driver vs teammate" set.
SECTIONS = [
    ("grid_setup", "Pre-race, grid and setup", [
        "What was the combined grid position of both cars, a front-row lockout "
        "or split across the field?",
        "Who out-qualified whom, and by how much?",
    ]),
    ("lap1", "The start, lap 1", [
        "How did each car's position change from its grid slot to the end of "
        "lap 1?",
    ]),
    ("lap_by_lap", "Race pace, lap by lap", [
        "How do the two cars' paces compare, lap by lap?",
    ]),
    ("tyres", "Tyre strategy", [
        "Did both cars run the same strategy, or did they diverge?",
        "If the strategies split, which one paid off?",
    ]),
    ("pit_stops", "Pit stops", [
        "How does average pit stop duration compare between the two cars?",
        "When did each car stop, and did either lose time in the lane?",
    ]),
    ("position", "Position dynamics", [
        "How did both cars' positions evolve over the race distance?",
    ]),
    ("incidents", "Incidents", [
        "Whose race had more incidents?",
    ]),
    ("outcome", "Finish and outcome", [
        "What was the combined points haul for the race?",
        "Did both cars reach the finish?",
    ]),
    ("standings", "Constructor standings", [
        "How did this race move the constructor standings, from points_start "
        "to points_current?",
    ]),
    ("teammate", "Head to head", [
        "Who scored more points, and by how much?",
        "Whose race had more pit stops or lost time?",
        "Which car came out ahead on pace, qualifying and racecraft?",
    ]),
]


def intro(race, team: str) -> bool:
    """
    Which team this story is about, printed once above the whole scroll.

    Same reason as story_driver.intro: this was inside render(), so a page that
    shows ten sections showed the team name ten times.
    """
    cars = _cars(int(race.session_key), team)
    if cars.empty:
        st.info("This team did not enter the selected race.")
        return False

    known = query("SELECT known_as FROM dim_team WHERE team_name = ?", (team,))
    subtitle = ""
    if len(known) and known.known_as.iloc[0] and known.known_as.iloc[0] != team:
        subtitle = f"  ·  raced as {known.known_as.iloc[0]}"

    st.markdown(f"### {team}{subtitle}")
    if len(cars) == 1:
        st.caption("Only one car from this team is recorded in this race.")
    return True


def section_options() -> list[tuple[str, str]]:
    return [(key, title) for key, title, _ in SECTIONS]


# --- entry point ---------------------------------------------------------------

def render(race, team: str, section_key: str) -> None:
    session_key = int(race.session_key)
    cars = _cars(session_key, team)

    if cars.empty:
        # Silent. intro() has already said so, once, above every section.
        return

    blocks = {
        "grid_setup": lambda: _grid(cars),
        "lap1": lambda: _start(session_key, cars),
        "lap_by_lap": lambda: _pace(session_key, cars),
        "tyres": lambda: _tyres(cars),
        "pit_stops": lambda: _pits(session_key, cars),
        "position": lambda: _positions(session_key, cars),
        "incidents": lambda: _incidents(session_key, cars),
        "outcome": lambda: _outcome(cars, race),
        "standings": lambda: _standings(session_key, team),
        "teammate": lambda: _head_to_head(cars),
    }
    questions = next((q for k, _, q in SECTIONS if k == section_key), [])
    if questions:
        st.caption("**Questions this section answers**")
        st.markdown("\n".join(f"- {q}" for q in questions))
        st.divider()
    blocks[section_key]()
