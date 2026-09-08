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


def red_flag_holds(session_key: int, driver_numbers=None) -> pd.DataFrame:
    """
    Spells in the pit lane under a race suspension, which are recorded but are
    not pit stops. See NOTES_LOG #65 and DATA_DICTIONARY on is_red_flag_stop.

    Optionally narrowed to specific cars, for the driver and team pages.
    """
    sql = ("SELECT driver_number, lap_number, detail FROM fact_event "
           "WHERE session_key = ? AND event_type = 'red_flag_stop'")
    if driver_numbers is not None:
        nums = ",".join(str(int(n)) for n in driver_numbers)
        if not nums:
            return pd.DataFrame(columns=["driver_number", "lap_number", "detail"])
        sql += f" AND driver_number IN ({nums})"
    return query(sql + " ORDER BY driver_number, lap_number", (session_key,))


def held_note(held: pd.DataFrame) -> str:
    """
    The line that goes directly under a pit stop count.

    Phrased around "not counted", because that is the question it exists to
    answer: someone looking at a red-flagged race sees a stop total lower than
    they remember and needs to know the difference was deliberate, not missing.

    The detail strings are used verbatim rather than reworded here. They are
    built once in s04 alongside the tyre lookup, and restating that logic in the
    dashboard is how the two drift apart.
    """
    n = len(held)
    if not n:
        return ""
    lead = ("Not counted as a pit stop" if n == 1
            else f"Not counted as pit stops ({n} records)")
    return f"{lead}: " + "; ".join(held.detail) + "."


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
