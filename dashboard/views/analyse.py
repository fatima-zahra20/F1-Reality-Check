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
import story_driver  # noqa: E402
import story_race  # noqa: E402
import story_team  # noqa: E402
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


# --- section picker -----------------------------------------------------------
# One section at a time rather than one long scroll: each maps to a file in
# DESCRIPTIVE ANALYTICS and answers that file's questions. Sections differ by
# story, since a team has no "gaps" questions and only a driver has a teammate.

MODULES = {
    "Story of a Race": story_race,
    "Story of a Driver": story_driver,
    "Story of a Team": story_team,
}
section_pairs = MODULES[story].section_options()
section_titles = {key: title for key, title in section_pairs}
section_keys = list(section_titles)

# Switching story changes which sections exist, and Diagnose can hand over a
# section directly. Fall back to the first section rather than raising when the
# stored key is not valid here.
if st.session_state.get("section_choice") not in section_keys:
    st.session_state["section_choice"] = section_keys[0]

st.sidebar.divider()
section = st.sidebar.radio(
    "Section", section_keys,
    format_func=lambda k: section_titles.get(k, k),
    key="section_choice",
)

if story == "Story of a Race":
    story_race.render(race, section)

elif story == "Story of a Driver":
    # The driver list depends on the race, so it is built here rather than
    # alongside the season and race pickers above.
    entrants = query("""
        SELECT f.driver_number, d.full_name, d.name_acronym, f.team_name,
               f.finish_position
        FROM fact_driver_race f
        JOIN dim_race r ON r.session_key = f.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = f.driver_number AND d.year = r.year
        WHERE f.session_key = ?
        ORDER BY f.finish_position IS NULL, f.finish_position
    """, (int(race.session_key),))

    if entrants.empty:
        st.info("No entrants recorded for this race.")
    else:
        driver_labels = {
            int(r.driver_number):
                f"{r.full_name or ('#' + str(int(r.driver_number)))}"
                f"  ·  {r.team_name}"
            for r in entrants.itertuples()
        }
        driver_options = list(driver_labels)

        # Changing race leaves the previous race's driver selected, who may
        # not have entered this one. Reset before the widget renders.
        if st.session_state.get("driver_choice") not in driver_options:
            st.session_state["driver_choice"] = driver_options[0]

        driver_number = st.sidebar.selectbox(
            "Driver", driver_options,
            format_func=lambda k: driver_labels.get(k, str(k)),
            key="driver_choice",
        )
        story_driver.render(race, driver_number, section)

else:
    teams = query("""
        SELECT DISTINCT team_name FROM fact_driver_race
        WHERE session_key = ? ORDER BY team_name
    """, (int(race.session_key),))

    if teams.empty:
        st.info("No teams recorded for this race.")
    else:
        team_options = teams.team_name.tolist()

        # Changing race can leave a team selected that did not enter this one
        # (Cadillac joined in 2026). Reset before the widget renders.
        if st.session_state.get("team_choice") not in team_options:
            st.session_state["team_choice"] = team_options[0]

        team = st.sidebar.selectbox("Team", team_options, key="team_choice")
        story_team.render(race, team, section)

st.sidebar.divider()
st.sidebar.caption(
    "Data from the OpenF1 API, rebuilt weekly. Clean laps exclude safety car, "
    "VSC and red flag periods."
)

render_footer()
