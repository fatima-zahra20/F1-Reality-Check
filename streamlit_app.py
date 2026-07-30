"""
F1 Reality Check — dashboard.

Reads the serving layer published by pipeline/s06_publish.py as a GitHub Release
asset. Streamlit Cloud has no disk of its own and only sees this repo, so the
data arrives over HTTPS at runtime rather than living in git — which keeps ~21 MB
of weekly-rewritten CSVs out of the history.

Run locally with:  streamlit run streamlit_app.py

Locally it prefers outputs/dashboard/dashboard.db if that file exists, so you can
preview changes before publishing them.
"""

from __future__ import annotations

import gzip
import io
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

REPO = "fatima-zahra20/F1-Reality-Check"
ASSET_URL = f"https://github.com/{REPO}/releases/download/data-latest/dashboard.db.gz"
LOCAL_DB = Path("outputs/dashboard/dashboard.db")

# Fallback for teams whose colour is missing in the source data.
NEUTRAL = "#8A8A94"

st.set_page_config(
    page_title="F1 Reality Check",
    page_icon="🏁",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --- data ---------------------------------------------------------------------

@st.cache_resource(show_spinner="Loading race data…")
def get_connection() -> sqlite3.Connection:
    """
    One connection per server process, not per session — the download and the
    23 MB file are shared by every visitor rather than repeated for each.
    """
    if LOCAL_DB.exists():
        path = LOCAL_DB
    else:
        tmp = Path(tempfile.gettempdir()) / "f1_reality_check_dashboard.db"
        if not tmp.exists():
            r = requests.get(ASSET_URL, timeout=120)
            if r.status_code == 404:
                st.error(
                    "The data has not been published yet.\n\n"
                    "Run `python pipeline/s06_publish.py --execute` to upload it "
                    f"to the `data-latest` release of `{REPO}`."
                )
                st.stop()
            r.raise_for_status()
            with gzip.open(io.BytesIO(r.content), "rb") as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        path = tmp

    # check_same_thread=False: Streamlit serves reruns from a worker pool, so
    # the connection is touched by threads other than the one that opened it.
    return sqlite3.connect(path, check_same_thread=False)


@st.cache_data(show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql(sql, get_connection(), params=params)


@st.cache_data(show_spinner=False)
def team_colours() -> dict[str, str]:
    df = query("SELECT team_name, team_colour FROM dim_team")
    return {
        r.team_name: (f"#{r.team_colour}" if pd.notna(r.team_colour) else NEUTRAL)
        for r in df.itertuples()
    }


# --- helpers ------------------------------------------------------------------

def fmt_lap(seconds) -> str:
    """Lap times read as m:ss.sss, never as a raw float."""
    if seconds is None or pd.isna(seconds):
        return "—"
    m, s = divmod(float(seconds), 60)
    return f"{int(m)}:{s:06.3f}"


def fmt_gap(seconds, laps) -> str:
    if pd.notna(laps) and laps:
        return f"+{int(laps)} lap" + ("s" if laps > 1 else "")
    if pd.isna(seconds):
        return "—"
    return "—" if seconds == 0 else f"+{seconds:.3f}s"


# --- sidebar ------------------------------------------------------------------

races = query("""
    SELECT session_key, year, round, race_name, circuit, country, race_date,
           total_laps, entrants, dnf_count, safety_car_periods, vsc_periods,
           red_flag_periods, avg_track_temp, avg_air_temp, is_wet_race,
           circuit_type
    FROM dim_race
    ORDER BY year DESC, round
""")

st.sidebar.title("F1 Reality Check")
st.sidebar.caption(f"{len(races)} races · {races.year.min()}–{races.year.max()}")

season = st.sidebar.selectbox("Season", sorted(races.year.unique(), reverse=True))
season_races = races[races.year == season]

# Options are session_keys, not names: two seasons share race names, and the
# label becomes a lookup instead of a filter over the frame.
labels = {
    int(r.session_key): f"R{int(r.round):02d}  {r.race_name}"
    for r in season_races.itertuples()
}
options = list(labels)

# Changing season leaves the previous season's race in session state, which is
# no longer a valid option. Reset it before the widget renders — Streamlit will
# otherwise try to format a value it cannot find and raise.
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


# --- header -------------------------------------------------------------------

st.title(race.race_name)
st.caption(
    f"{race.circuit}, {race.country} · "
    f"{pd.to_datetime(race.race_date).strftime('%d %B %Y')} · "
    f"{race.circuit_type} circuit"
)

results = query("""
    SELECT f.*, d.full_name, d.name_acronym
    FROM fact_driver_race f
    LEFT JOIN dim_race r ON r.session_key = f.session_key
    LEFT JOIN dim_driver d
           ON d.driver_number = f.driver_number AND d.year = r.year
    WHERE f.session_key = ?
    ORDER BY f.finish_position IS NULL, f.finish_position
""", (int(race.session_key),))

winner = results[results.finish_position == 1]
pole = results[results.grid_position == 1]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Winner", winner.name_acronym.iloc[0] if len(winner) else "—",
          winner.team_name.iloc[0] if len(winner) else None)
c2.metric("Pole", pole.name_acronym.iloc[0] if len(pole) else "—",
          pole.team_name.iloc[0] if len(pole) else None)
c3.metric("Retirements", int(race.dnf_count), f"of {int(race.entrants)} starters")
c4.metric("Neutralisations",
          int(race.safety_car_periods + race.vsc_periods + race.red_flag_periods),
          f"{int(race.safety_car_periods)} SC · {int(race.vsc_periods)} VSC")
c5.metric("Track temp",
          f"{race.avg_track_temp:.0f}°C" if pd.notna(race.avg_track_temp) else "—",
          "wet race" if race.is_wet_race else "dry")


# --- grid vs finish -----------------------------------------------------------

st.subheader("Positions gained")
st.caption(
    "Grid position minus finish position. Bars to the right are drivers who "
    "moved forward. Retirements aren't shown here — see Pos in the table below."
)

colours = team_colours()
plot = (results.dropna(subset=["position_change"])
               .sort_values("position_change")
               .copy())
plot["label"] = plot.name_acronym.fillna(plot.driver_number.astype(str))

fig = go.Figure(go.Bar(
    x=plot.position_change,
    y=plot.label,
    orientation="h",
    marker_color=[colours.get(t, NEUTRAL) for t in plot.team_name],
    hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>%{x:+d} places<extra></extra>",
    customdata=plot[["full_name", "team_name"]],
))

fig.update_layout(
    height=max(320, 24 * len(plot)),
    margin=dict(l=10, r=10, t=10, b=10),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    xaxis=dict(title="Places gained", zeroline=True, zerolinecolor="rgba(0,0,0,0.3)",
               gridcolor="rgba(0,0,0,0.08)", fixedrange=True),
    yaxis=dict(title=None, fixedrange=True),
)
st.plotly_chart(fig, width="stretch")


# --- classification -----------------------------------------------------------

st.subheader("Classification")

def classification(row) -> str:
    """A blank position reads as missing data; say what actually happened."""
    if pd.notna(row.finish_position):
        return str(int(row.finish_position))
    if row.dsq:
        return "DSQ"
    if row.dns:
        return "DNS"
    return "DNF"


table = pd.DataFrame({
    "Pos": results.apply(classification, axis=1),
    "Driver": results.full_name,
    "Team": results.team_name,
    "Grid": results.grid_position,
    # Already signed positive-for-gain upstream (grid minus finish).
    "Gained": results.position_change.astype("Int64"),
    "Gap": [fmt_gap(s, l) for s, l in
            zip(results.gap_to_leader_seconds, results.gap_to_leader_laps)],
    "Best lap": results.fastest_lap.map(fmt_lap),
    "Pace vs median": results.pace_vs_session_median,
    "Stops": results.pit_stops,
    "Tyres": results.compound_sequence,
    "Pts": results.points,
})

st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "Pos": st.column_config.TextColumn(width="small"),
        "Grid": st.column_config.NumberColumn(format="%d", width="small"),
        "Gained": st.column_config.NumberColumn(
            format="%+d", width="small",
            help="Places gained from the grid. Negative means places lost."),
        "Pace vs median": st.column_config.NumberColumn(
            format="%+.3f s",
            help="Mean clean lap against the session median. Negative is faster."),
        "Stops": st.column_config.NumberColumn(format="%d", width="small"),
        "Pts": st.column_config.NumberColumn(format="%d", width="small"),
    },
)
