"""
analyse.py - the descriptive layer, told as three stories about one race.

    Story of a Race     the whole field, race-wide
    Story of a Driver   one driver's race, through their eyes
    Story of a Team     both cars compared, the teammate lens

All three share the same Year and Race filter; Driver and Team add one more.
Each story answers the questions in
DESCRIPTIVE ANALYTICS/descriptive_question_bank.md, in the order the bank
asks them, so the page reads chronologically rather than by chart type.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import story_race  # noqa: E402
from app_common import query, render_footer  # noqa: E402

# --- shared filters -----------------------------------------------------------

races = query("""
    SELECT session_key, year, round, race_name, circuit, country, race_date,
           total_laps, entrants, dnf_count, safety_car_periods, vsc_periods,
           red_flag_periods, avg_track_temp, avg_air_temp, pct_samples_wet,
           is_wet_race, circuit_type
    FROM dim_race
    ORDER BY year DESC, round
""")

st.sidebar.title("F1 Reality Check")
st.sidebar.caption(f"{len(races)} races · {races.year.min()}-{races.year.max()}")

season = st.sidebar.selectbox(
    "Season", sorted(races.year.unique(), reverse=True), key="season_choice",
)
season_races = races[races.year == season]

# Options are session_keys, not names: two seasons share race names, and the
# label becomes a lookup instead of a filter over the frame.
labels = {
    int(r.session_key): f"R{int(r.round):02d}  {r.race_name}"
    for r in season_races.itertuples()
}
options = list(labels)

# Changing season leaves the previous season's race in session state, which is
# no longer a valid option. Reset it before the widget renders - Streamlit
# will otherwise try to format a value it cannot find and raise.
if st.session_state.get("race_choice") not in options:
    st.session_state["race_choice"] = options[0]

session_key = st.sidebar.selectbox(
    "Race",
    options,
    format_func=lambda k: labels.get(k, str(k)),
    key="race_choice",
)
race = season_races[season_races.session_key == session_key].iloc[0]

st.sidebar.divider()
st.sidebar.caption(
    "Data from the OpenF1 API, rebuilt weekly. Clean laps exclude safety car, "
    "VSC and red flag periods."
)


# --- story picker -------------------------------------------------------------

STORIES = ["Story of a Race", "Story of a Driver", "Story of a Team"]
# st.radio rather than st.segmented_control: the pill styling is nicer, but
# Streamlit 1.51's own AppTest harness cannot model a segmented_control's
# state, which breaks every automated check of this page after the first
# render. A testable page is worth more than the pills.
story = st.radio(
    "Story", STORIES, horizontal=True, label_visibility="collapsed",
    key="story_choice",
)

st.title(story)
st.caption(
    f"{race.race_name} · {race.circuit}, {race.country} · "
    f"{pd.to_datetime(race.race_date).strftime('%d %B %Y')} · "
    f"{race.circuit_type} circuit"
)

if story == "Story of a Race":
    story_race.render(race)
elif story == "Story of a Driver":
    st.info(
        "Not built yet. This will follow the same question bank through one "
        "driver's race: their grid slot, their lap 1, their stints, their "
        "radio traffic, their result."
    )
else:
    st.info(
        "Not built yet. This will compare a team's two cars: combined grid "
        "position, who out-qualified whom, whether the strategies converged "
        "or split, and the combined points haul."
    )

render_footer()
