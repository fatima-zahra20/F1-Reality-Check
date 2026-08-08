"""
perfect.py - "Find perfect lap".

One page, read by scrolling, in the same three movements as the rest of the
project:

    1. The state of the race line    what was happening, at one instant
    2. Why the lap was what it was   which factors moved it, and by how much
    3. What could have been better   waits for the predictive layer

Unlike Analyse and Diagnose there is no section picker. The three parts are
meant to be read in order, because part 2 only means anything once part 1 has
established what is being explained.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import race_map as rm  # noqa: E402
from app_common import query, render_footer, team_colours  # noqa: E402
from story_common import guide  # noqa: E402

st.title("Find perfect lap")
st.caption(
    "Pick a moment of a race and see where every car was, what its lap was "
    "made of, and which conditions were acting on it."
)

cover = rm.coverage()

# --- pickers ------------------------------------------------------------------
# At the top of the page rather than in the sidebar: this page is one scroll
# and the choices belong with the thing they change.

races = query("""
    SELECT session_key, year, race_name, circuit, country, race_date,
           total_laps, circuit_type
    FROM dim_race ORDER BY year DESC, round
""")
races = races.merge(cover[["session_key", "circuit_key", "has_outline",
                           "has_measured_xy", "map_note"]], on="session_key")

c1, c2 = st.columns([1, 3])
years = sorted(races.year.unique(), reverse=True)
year = c1.selectbox("Season", years, key="perfect_year")

in_year = races[races.year == year].reset_index(drop=True)
if st.session_state.get("perfect_race") not in set(in_year.session_key):
    st.session_state["perfect_race"] = int(in_year.session_key.iloc[0])

session_key = c2.selectbox(
    "Race", in_year.session_key, key="perfect_race",
    format_func=lambda k: in_year.loc[in_year.session_key == k,
                                      "race_name"].iloc[0])

race = in_year[in_year.session_key == session_key].iloc[0]
laps = rm.race_laps(int(session_key))

st.caption(
    f"{race.race_name} · {race.circuit}, {race.country} · "
    f"{pd.to_datetime(race.race_date).strftime('%d %B %Y')} · "
    f"{int(race.total_laps)} laps"
)

st.divider()

# --- 1. the state of the race line -----------------------------------------------

st.header("1. The state of the race line")

if not len(laps):
    st.warning("No lap data for this race.")
    st.stop()

max_lap = int(laps.lap_number.max())
p1, p2, p3 = st.columns([1, 1, 2])
lap_number = p1.number_input("Lap", min_value=1, max_value=max_lap, value=1,
                             step=1, key="perfect_lap_no")
sector_label = p2.selectbox("Sector", rm.SECTOR_CHOICES, index=1,
                            key="perfect_sector")
sector = None if sector_label == rm.SECTOR_CHOICES[0] else int(sector_label[-1])

drivers = (laps[["driver_number", "driver"]].drop_duplicates()
                                            .sort_values("driver"))
options = [None] + drivers.driver_number.tolist()
names = dict(zip(drivers.driver_number, drivers.driver))
focus = p3.selectbox("Driver", options, key="perfect_driver",
                     format_func=lambda d: "All cars" if d is None else names[d])

bounds = rm.sector_bounds(laps)
moment, anchor = rm.moment_for(laps, lap_number,
                               None if focus is None else int(focus),
                               sector, bounds)

if moment is None:
    st.warning(f"Lap {lap_number} has no recorded start time in this race.")
elif not race.has_outline:
    st.info(f"**No track map for this circuit.** {race.map_note}")
else:
    path = rm.outline(int(race.circuit_key))
    cars = rm.place_on_outline(rm.cars_at(laps, moment), path)
    solid = rm.has_elevation(path)

    # Where the race has recorded positions, they replace the derived ones,
    # driver by driver. Coverage is ragged, so this is never all-or-nothing.
    n_measured = 0
    if race.has_measured_xy:
        cars, n_measured = rm.apply_measured(
            cars, rm.measured_positions(int(session_key)), moment)

    view = st.radio("View", ["3D elevation", "Flat"], horizontal=True,
                    key="perfect_view",
                    disabled=not solid,
                    help=None if solid else
                    "This circuit has no elevation data recorded.")
    if not solid and view == "3D elevation":
        view = "Flat"

    if view == "3D elevation":
        # Both ways of moving the camera stay available. The sliders give
        # exact, slow steps; the mouse gives quick, rough movement. The mouse
        # is only usable at all because the drag mode is turntable, which
        # holds the horizon level so a drag can spin the circuit but never
        # tip it over.
        v1, v2 = st.columns(2)
        azimuth = v1.slider(
            "Turn left and right", min_value=0, max_value=355,
            value=int(rm.CAMERA_AZIMUTH_DEFAULT), step=5, format="%d deg",
            key="perfect_azimuth",
            help="Five degrees a step. Dragging the circuit does the same "
                 "thing faster.")
        distance = v2.slider(
            "Zoom", min_value=rm.CAMERA_DISTANCE_MIN,
            max_value=rm.CAMERA_DISTANCE_MAX,
            value=rm.CAMERA_DISTANCE_DEFAULT, step=0.1,
            key="perfect_zoom",
            help="Lower is closer. The mouse wheel does the same thing in "
                 "bigger jumps.")

        fig = rm.draw_3d(path, cars, bounds, sector,
                         None if focus is None else int(focus), team_colours(),
                         azimuth=float(azimuth), distance=float(distance))
    else:
        fig = rm.draw(path, cars, bounds, sector,
                      None if focus is None else int(focus), team_colours())

    st.plotly_chart(
        fig, width="stretch",
        key=f"map_{session_key}_{lap_number}_{focus}_{view}",
        config={"scrollZoom": True, "displaylogo": False})

    if view == "3D elevation":
        relief = rm.relief_metres(path)
        st.caption(
            "Drag the circuit to turn it, scroll to zoom, or use the sliders "
            "for slow exact steps. The horizon is held level either way, so "
            "the track spins but never tips over. Double-click to go back to "
            f"the slider positions. {race.circuit} climbs **{relief:.0f} m** "
            "from its lowest point to its highest, and the height here is "
            "drawn at **true scale**: a metre of climb is the same length on "
            "screen as a metre of track. Most circuits look nearly flat "
            "because they are."
        )
    elif not solid:
        st.caption(
            f"No elevation recorded for {race.circuit}, so only the flat view "
            "is available here."
        )

    running = int(cars.on_track.sum())
    if focus is None:
        st.caption(
            f"Sector {sector} is darkened. Choose a driver to place the cars "
            f"on the track. {running} were running at the start of lap "
            f"{lap_number}."
            + ("" if bounds else
               " Sector lines could not be located for this race.")
        )
    elif anchor == rm.ANCHOR_FALLBACK:
        # No timed lap for this driver at this number, so the sector cannot be
        # anchored to them and the dot will not be in the darkened stretch.
        st.caption(
            f"Showing the instant the leader started lap {lap_number}, "
            f"{running} of {len(cars)} cars on track."
        )
        st.info(
            f"**{names[focus]} has no timed lap {lap_number}.** They had "
            "retired by this point, or the lap was lost to a red flag, so "
            "there are no sector times to place them by. The map falls back "
            "to the leader's moment, which is why their car is not in the "
            "darkened sector."
        )
    else:
        st.caption(
            f"The instant {names[focus]} was {anchor} on lap {lap_number}. "
            f"Every other car is drawn where it was at that same moment, "
            f"{running} of {len(cars)} of them on track."
            + ("" if bounds else
               " Sector lines could not be located for this race.")
        )

    # A driver picked but not drawn needs a reason, or the empty track reads
    # as a broken chart rather than a car that is no longer in the race.
    if focus is not None:
        mine = cars[cars.driver_number == int(focus)]
        if not len(mine) or mine.iloc[0].map_x != mine.iloc[0].map_x:
            st.warning(
                f"**{names[focus]} is not on track at this moment.** They had "
                "either not started the race, were in the pit lane, or had "
                "already retired. Try an earlier lap."
            )

    guide(
        "The map starts empty. Choosing a sector darkens that stretch of "
        "track; choosing a driver places the cars. Your driver is the large "
        "labelled dot, sitting halfway through the sector you picked, and the "
        "rest of the field are the small dots, drawn where each of them was "
        "at that same instant. So the spread around the outline is the real "
        "spread of the race at that moment, and a car just ahead on the "
        "outline was genuinely just ahead on track. Hover any dot for the "
        "driver, team, position and tyre. A car that has retired or is in the "
        "pit lane is not drawn."
    )

    if race.has_measured_xy and n_measured:
        st.success(
            f"**{n_measured} of these dots are real recorded positions**, not "
            "derived ones. This race has a position feed, so where a sample "
            "exists within three seconds of this moment the car is drawn "
            "exactly where it was."
            + ("" if n_measured == int(cars.on_track.sum()) else
               " The rest fall back to lap timing, because the feed does not "
               "cover every car for the whole race.")
        )
    elif race.has_measured_xy:
        st.info(
            "**This race has recorded positions, but none at this moment.** "
            "The position feed does not run the full race distance here, so "
            "the dots are derived from lap timing. Try an earlier lap."
        )
    else:
        st.info(
            "**Positions are derived, not recorded.** No race except one in "
            "2026 has recorded car positions, so each car is placed by how "
            "far through its own lap it was at this instant. It cannot know "
            "the racing line or which side of the track a car was on.\n\n"
            "How accurate that is has been measured rather than assumed. "
            "Checked against the 2026 Monaco Grand Prix, the one race with "
            "real recorded positions, the derived dot sits a median of 24 "
            "metres from where the car actually was, which is 0.7% of a lap. "
            "Checked against the timing feed's own gap to leader across 12 "
            "races, spacing correlates at 0.96 with a median error of 1.7 "
            "seconds. Both are worst on lap 1, when the field is still bunched."
        )

# --- the single car ---------------------------------------------------------------

if focus is not None:
    st.subheader("This car, this lap")
    one = laps[(laps.driver_number == focus) & (laps.lap_number == lap_number)]
    if not len(one):
        st.warning(
            f"{names[focus]} has no recorded lap {lap_number}. They may have "
            "retired earlier, or the lap was not timed."
        )
    else:
        rm.car_panel(one.iloc[0], race)
else:
    st.caption("Choose a driver above to see everything recorded about one car.")

st.divider()

# --- 2 and 3 ----------------------------------------------------------------------

st.header("2. What made the lap what it was")
st.caption("Next section. Not built yet.")

st.divider()

st.header("3. What could have been better")
st.caption("Waits for the predictive layer.")

render_footer()
