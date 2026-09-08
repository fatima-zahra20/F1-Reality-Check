"""
story_common.py - pieces shared by every "Story of..." view.

Keeps the three stories looking like one page rather than three: the same
chart styling, the same 'how to read this' treatment, the same definition of
a clean lap.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app_common import query
# The palette moved to theme.py when dark mode arrived. These are re-exported,
# not redefined, so `from story_common import AXIS_BASE` keeps working and keeps
# holding the SAME dict object theme.apply() refreshes. Reassigning either of
# them here would silently detach every importer from the live palette.
from theme import (  # noqa: F401
    ACCENT, AXIS_BASE, MUTED, PLOT_BASE, heat, ink, zero_line,
)

# A pit out-lap or a lap behind a safety car is not a racing lap. Blocks that
# describe pace exclude them; blocks that describe what happened do not.
CLEAN_LAP = "neutralised = 0 AND is_pit_out_lap = 0"

# Within this gap a driver is in the attack window rather than circulating
# alone. One second is the sport's own definition, not a derived threshold.
FIGHTING_SECONDS = 1.0


def guide(text: str) -> None:
    """One consistent 'how to read this' line under each plot."""
    st.caption(f"**How to read this.** {text}")


HELD_COLUMNS = ["driver_number", "lap_number", "value",
                "tyre_before", "tyre_after", "detail"]


def tyre_label(before, after) -> str:
    """
    "HARD to MEDIUM", "fresh MEDIUM", "MEDIUM", or nothing.

    "fresh" rather than "no change": tyre_after is read from a stint that begins
    at or after the stop, so its presence already means a new stint started. The
    same compound is therefore a new set of it, which is exactly what the whole
    field did at Zandvoort 2023 in the rain.
    """
    if pd.isna(after):
        return ""
    if pd.isna(before):
        return str(after)
    if before == after:
        return f"fresh {after}"
    return f"{before} to {after}"


def with_tyres(df: pd.DataFrame) -> pd.DataFrame:
    """Add a formatted `Tyre` column from the tyre_before/tyre_after pair."""
    df = df.copy()
    df["Tyre"] = [tyre_label(b, a)
                  for b, a in zip(df.get("tyre_before", []),
                                  df.get("tyre_after", []))]
    return df


def red_flag_holds(session_key: int, driver_numbers=None) -> pd.DataFrame:
    """
    Spells in the pit lane under a race suspension, which are recorded but are
    not pit stops. See NOTES_LOG #65 and DATA_DICTIONARY on is_red_flag_stop.

    Optionally narrowed to specific cars, for the driver and team pages.
    """
    cols = ", ".join(HELD_COLUMNS)
    sql = (f"SELECT {cols} FROM fact_event "
           "WHERE session_key = ? AND event_type = 'red_flag_stop'")
    if driver_numbers is not None:
        nums = ",".join(str(int(n)) for n in driver_numbers)
        if not nums:
            return pd.DataFrame(columns=HELD_COLUMNS)
        sql += f" AND driver_number IN ({nums})"
    return query(sql + " ORDER BY driver_number, lap_number", (session_key,))


def held_table(held: pd.DataFrame, show_car: bool = False) -> None:
    """
    The red-flag half of a pit stop section: one row per spell held.

    Held time is shown in minutes because that is the scale it lives on. These
    run 20 to 41 minutes and printing 2,486.0 s invites the reader to compare it
    with a 24 second stop, which is the confusion the split exists to end.
    """
    t = with_tyres(held)
    t["Held"] = t.value / 60.0
    cols = (["driver_number"] if show_car else []) + ["lap_number", "Held", "Tyre"]
    st.dataframe(
        t[cols].rename(columns={"driver_number": "Car", "lap_number": "Lap"}),
        hide_index=True, width="stretch",
        column_config={
            "Car": st.column_config.NumberColumn(format="%d", width="small"),
            "Lap": st.column_config.NumberColumn(format="%d", width="small"),
            "Held": st.column_config.NumberColumn(format="%.0f min"),
        },
    )


def hbar(df, x, y, colours, hover, xtitle=None, zeroline=False, height=None):
    """One horizontal bar chart, styled once so every block looks the same."""
    fig = go.Figure(go.Bar(
        x=df[x], y=df[y], orientation="h",
        marker_color=colours,
        customdata=hover,
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}"
                      "<br>%{x}<extra></extra>",
    ))
    fig.update_layout(
        height=height or max(320, 24 * len(df)),
        xaxis=dict(title=xtitle, zeroline=zeroline,
                   zerolinecolor=zero_line(), **AXIS_BASE),
        yaxis=dict(title=None, **AXIS_BASE),
        **PLOT_BASE,
    )
    return fig


def line_layout(fig, xtitle: str, ytitle: str, height: int = 380,
                reverse_y: bool = False):
    """Shared layout for the lap-indexed line charts."""
    y = dict(title=ytitle, **AXIS_BASE)
    if reverse_y:
        y["autorange"] = "reversed"
    fig.update_layout(
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        xaxis=dict(title=xtitle, **AXIS_BASE),
        yaxis=y,
        **PLOT_BASE,
    )
    return fig


def field(session_key: int) -> pd.DataFrame:
    """One row per driver in this race, with names and team resolved."""
    return query("""
        SELECT f.*, d.full_name, d.name_acronym
        FROM fact_driver_race f
        JOIN dim_race r ON r.session_key = f.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = f.driver_number AND d.year = r.year
        WHERE f.session_key = ?
        ORDER BY f.finish_position IS NULL, f.finish_position
    """, (session_key,))


def labels(df: pd.DataFrame) -> pd.Series:
    """Three-letter code where known, car number otherwise."""
    return df.name_acronym.fillna(df.driver_number.astype(str))
