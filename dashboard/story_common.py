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

# A pit out-lap or a lap behind a safety car is not a racing lap. Blocks that
# describe pace exclude them; blocks that describe what happened do not.
CLEAN_LAP = "neutralised = 0 AND is_pit_out_lap = 0"

# Within this gap a driver is in the attack window rather than circulating
# alone. One second is the sport's own definition, not a derived threshold.
FIGHTING_SECONDS = 1.0

PLOT_BASE = dict(
    margin=dict(l=10, r=10, t=10, b=10),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
)
AXIS_BASE = dict(fixedrange=True, gridcolor="rgba(0,0,0,0.08)")

INK = "#31333F"      # the driver being described
ACCENT = "#E10600"   # something that needs attention
MUTED = "#9A9AA5"    # the comparison, never the subject


def guide(text: str) -> None:
    """One consistent 'how to read this' line under each plot."""
    st.caption(f"**How to read this.** {text}")


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
                   zerolinecolor="rgba(0,0,0,0.3)", **AXIS_BASE),
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
