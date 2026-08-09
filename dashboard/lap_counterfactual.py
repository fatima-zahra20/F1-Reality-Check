"""
lap_counterfactual.py - section 3, what could have been different.

The arithmetic is deliberately simple, and the reason matters.

A counterfactual here does NOT re-predict the lap from scratch. It takes the
lap that was actually driven and adds the model's estimate of what each change
would have been worth. So the lap's own residual, the part no factor explains,
travels with it untouched. That is the honest treatment: 76% of within-race
variation is unexplained, and a page that rebuilt the lap from coefficients
would quietly replace the driver with the average of everyone.

    new lap time = the real lap time + sum of the changes

TWO BLOCKS, TWO KINDS OF CLAIM. Choices are estimated against cars on the same
lap of the same race, where fuel, track and weather are shared and difference
away. Conditions can only be estimated in the pooled model, because they are
identical for every car on a lap and vanish under that differencing. The first
group can carry "this could have been done differently". The second cannot; it
is circumstance, and the page says so rather than implying anyone chose the
weather.

BOUNDS EXIST SO THE MODEL IS NOT ASKED SOMETHING IT CANNOT ANSWER. Every slider
is limited to what has actually been recorded, either in this race or anywhere
in four seasons. Combinations no driver has run are the point and are allowed;
values no sensor has ever seen are extrapolation and are not.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app_common import query
from story_common import ACCENT, AXIS_BASE, INK, MUTED, PLOT_BASE

# The levers, in the order they are shown. `flag` renders as a checkbox,
# `choice` as a select, everything else as a slider bounded by the data.
CHOICE_LEVERS = [
    ("compound", "Tyre compound", "choice", ""),
    ("tyre_age", "Tyre age", "int", "laps on the tyre"),
    ("gap_ahead", "Gap to the car ahead", "float", "s"),
    ("in_dirty_air", "Running in dirty air", "flag", "within 1.5s"),
    ("out_of_position", "Out of position", "flag", "gap counted in laps"),
    ("being_lapped", "Being lapped", "flag", "a lap or more down"),
]

CONDITION_LEVERS = [
    ("rainfall", "Rain", "flag", "rain flag"),
    ("track_temperature", "Track temperature", "float", "C"),
    ("air_temperature", "Air temperature", "float", "C"),
    ("humidity", "Humidity", "float", "%"),
    ("wind_speed", "Wind speed", "float", "m/s"),
    ("wind_direction", "Wind direction", "int", "degrees"),
    ("yellow_sector", "Sector yellow flag", "flag", "yellow in a sector"),
    ("lap_number", "Lap number, as fuel load", "int", "laps of fuel burned"),
]

COMPOUNDS = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE"]

# Matches s05b. gap_ahead beyond this is clear track either way.
GAP_CAP_SECONDS = 10.0


# --- data --------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def model() -> pd.DataFrame:
    return query("SELECT * FROM lap_counterfactual_model")


@st.cache_data(show_spinner=False)
def bounds(session_key: int) -> pd.DataFrame:
    return query("""
        SELECT * FROM lap_counterfactual_bounds
        WHERE session_key = ? OR session_key IS NULL
    """, (int(session_key),))


def limits(bnds: pd.DataFrame, term: str, widen: bool) -> tuple | None:
    """
    The low and high a slider may take.

    `widen` swaps this race's range for everything ever recorded, which is what
    lets a reader build a combination no driver has run without letting them
    invent a track temperature that has never existed.
    """
    scope = "ever recorded" if widen else "this race"
    hit = bnds[(bnds.term == term) & (bnds.scope == scope)]
    if hit.empty:
        hit = bnds[bnds.term == term]
    if hit.empty:
        return None
    r = hit.iloc[0]
    if pd.isna(r.low) or pd.isna(r.high):
        return None
    lo, hi = float(r.low), float(r.high)
    return (lo, hi) if hi > lo else (lo, lo + 1.0)


# --- the arithmetic ------------------------------------------------------------------

def _coef(m: pd.DataFrame, term: str) -> float:
    hit = m[m.term == term]
    return float(hit.coefficient.iloc[0]) if len(hit) else 0.0


def compound_effect(m: pd.DataFrame, compound, tyre_age: float) -> float:
    """
    What a compound is worth at a given tyre age, against a HARD of that age.

    Both halves are needed. The level term is the tyre when new and the
    interaction is how fast it gives that up, so quoting either alone describes
    a tyre that does not exist.
    """
    if compound is None or pd.isna(compound) or compound == "HARD":
        return 0.0
    age = 0.0 if pd.isna(tyre_age) else float(tyre_age)
    return (_coef(m, f"C(compound)[T.{compound}]")
            + _coef(m, f"C(compound)[T.{compound}]:tyre_age") * age)


def wind_effect(m: pd.DataFrame, circuit: str, speed, bearing) -> float:
    """Wind as this circuit's own two components, the way s05b fitted it."""
    if pd.isna(speed) or pd.isna(bearing) or not circuit:
        return 0.0
    rad = np.radians(float(bearing))
    u = float(speed) * np.cos(rad)
    v = float(speed) * np.sin(rad)
    return (_coef(m, f"C(circuit_short_name)[{circuit}]:wind_u") * u
            + _coef(m, f"C(circuit_short_name)[{circuit}]:wind_v") * v)


def evaluate(m: pd.DataFrame, before: dict, after: dict,
             circuit: str) -> pd.DataFrame:
    """
    One row per lever, with what changing it is worth in seconds.

    Tyre compound and tyre age are computed together rather than separately,
    because the compound's value depends on the age and adding two independent
    deltas would double-count the interaction between them.
    """
    rows = []

    def same(a, b) -> bool:
        if pd.isna(a) and pd.isna(b):
            return True
        if isinstance(a, str) or isinstance(b, str):
            return a == b
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return a == b

    def add(term, label, unit, seconds, group, ident, shown_before=None,
            shown_after=None):
        b = before.get(term) if shown_before is None else shown_before
        a = after.get(term) if shown_after is None else shown_after
        rows.append({
            "term": term, "factor": label, "unit": unit,
            "before": b, "after": a,
            # + 0.0 turns -0.0 into 0.0, so an untouched row does not display
            # as "-0.0000" and read like a rounded-away gain.
            "seconds": float(seconds) + 0.0,
            "changed": not same(b, a), "group": group,
            "identification": ident,
        })

    def num(d, term):
        v = d.get(term)
        return 0.0 if v is None or pd.isna(v) else float(v)

    # --- tyre ---
    # The two levers interact, so the interaction has to be charged to one of
    # them, and the choice is not free. Two rules decide it:
    #
    #   the compound bar must be exactly zero when the compound is unchanged,
    #   otherwise the table reads "before MEDIUM, after MEDIUM, -0.050s"
    #
    #   no bar may be positive inside the best case, which is by construction
    #   the minimum, otherwise the page shows a gain and a penalty for the same
    #   recommendation
    #
    # Pricing the swap at the NEW age satisfies both. Pricing it at the old age
    # satisfies only the first: switching to the tyre that is quickest when
    # fresh looks like a loss when charged at the age you were trying to
    # escape. The age bar then carries the compound's own degradation, which is
    # the right home for it, and the two still sum to the whole tyre effect.
    tyre_before = (compound_effect(m, before.get("compound"),
                                   before.get("tyre_age"))
                   + _coef(m, "tyre_age") * num(before, "tyre_age"))
    tyre_after = (compound_effect(m, after.get("compound"),
                                  after.get("tyre_age"))
                  + _coef(m, "tyre_age") * num(after, "tyre_age"))
    compound_seconds = (
        compound_effect(m, after.get("compound"), after.get("tyre_age"))
        - compound_effect(m, before.get("compound"), after.get("tyre_age")))

    add("compound", "Tyre compound", "", compound_seconds,
        "choice", "within-lap")
    add("tyre_age", "Tyre age", "laps on the tyre",
        (tyre_after - tyre_before) - compound_seconds,
        "choice", "within-lap")

    for term, label, _kind, unit in CHOICE_LEVERS:
        if term in ("compound", "tyre_age"):
            continue
        add(term, label, unit,
            _coef(m, term) * (num(after, term) - num(before, term)),
            "choice", "within-lap")

    # --- conditions ---
    for term, label, _kind, unit in CONDITION_LEVERS:
        if term in ("wind_speed", "wind_direction"):
            continue
        add(term, label, unit,
            _coef(m, term) * (num(after, term) - num(before, term)),
            "condition", "pooled")

    # Speed and direction move together, so they are one row. The model prices
    # them jointly, through this circuit's own two components, and splitting
    # them would report a headwind as if it had a size but no direction.
    def wind_label(d):
        s, b = d.get("wind_speed"), d.get("wind_direction")
        if pd.isna(s) or pd.isna(b):
            return "not recorded"
        return f"{float(s):.1f} m/s from {int(round(float(b)))} deg"

    wind_seconds = (
        _coef(m, "wind_speed") * (num(after, "wind_speed")
                                  - num(before, "wind_speed"))
        + wind_effect(m, circuit, after.get("wind_speed"),
                      after.get("wind_direction"))
        - wind_effect(m, circuit, before.get("wind_speed"),
                      before.get("wind_direction")))
    add("wind", "Wind, speed and direction", "", wind_seconds,
        "condition", "pooled",
        shown_before=wind_label(before), shown_after=wind_label(after))

    return pd.DataFrame(rows)


def best_case(m: pd.DataFrame, before: dict, bnds: pd.DataFrame,
              widen: bool) -> dict:
    """
    The best this lap could have been, over choices only, inside the bounds.

    Nothing here touches the weather or the lap number. You cannot choose the
    rain, and you cannot choose to be on lap 62 when you are on lap 12, so an
    optimum that reaches for either is not an answer to "what could we have
    done differently". It is only an answer to "what if this had been a
    different afternoon", which is the second block's job.
    """
    out = dict(before)

    def pick(term, fallback_lo, fallback_hi):
        """
        The end of the range the model prefers, but never worse than reality.

        The bounds come from the pipeline's own derivation of a lever, and the
        app derives some of them from fact_lap instead, so the two can disagree
        by a little. Without the clamp a lap already sitting past the end of
        its race's recorded range gets "improved" backwards into it, and the
        best case comes out slower than the lap that was actually driven.
        """
        rng = limits(bnds, term, widen)
        lo, hi = rng if rng else (fallback_lo, fallback_hi)
        best = lo if _coef(m, term) > 0 else hi
        current = before.get(term)
        if current is None or pd.isna(current):
            return best
        current = float(current)
        return min(best, current) if _coef(m, term) > 0 else max(best, current)

    out["tyre_age"] = pick("tyre_age", 0.0, 0.0)

    # Pick the compound that is quickest at the age we just chose, rather than
    # the one that is quickest when new. They are not always the same tyre.
    out["compound"] = min(
        COMPOUNDS, key=lambda c: compound_effect(m, c, out["tyre_age"]))

    # The coefficient is negative, so a bigger gap is quicker: clear track.
    out["gap_ahead"] = pick("gap_ahead", 0.0, GAP_CAP_SECONDS)

    for term in ("in_dirty_air", "out_of_position", "being_lapped"):
        out[term] = 0.0 if _coef(m, term) > 0 else 1.0

    return out


# --- presentation ---------------------------------------------------------------------

def _fmt(value, unit: str) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "not recorded"
    if isinstance(value, str):
        return value
    if unit in ("within 1.5s", "gap counted in laps", "a lap or more down",
                "rain flag", "yellow in a sector"):
        return "yes" if float(value) >= 0.5 else "no"
    v = float(value)
    return f"{v:.0f} {unit}".strip() if abs(v - round(v)) < 1e-9 \
        else f"{v:.2f} {unit}".strip()


def before_after(parts: pd.DataFrame, group: str) -> pd.DataFrame:
    """The table the reader actually reads: every parameter, old and new."""
    df = parts[parts.group == group].copy()
    return pd.DataFrame({
        "Parameter": df.factor,
        "Before": [_fmt(v, u) for v, u in zip(df.before, df.unit)],
        "After": [_fmt(v, u) for v, u in zip(df.after, df.unit)],
        "Effect (s)": df.seconds.values,
        "Changed": df.changed.values,
    })


def delta_chart(parts: pd.DataFrame) -> go.Figure:
    """Only the levers that moved, largest first, gains and costs separated."""
    df = parts[parts.changed & (parts.seconds.abs() > 1e-9)].copy()
    if df.empty:
        return None
    df = df.reindex(df.seconds.abs().sort_values().index)
    fig = go.Figure(go.Bar(
        x=df.seconds, y=df.factor, orientation="h",
        marker_color=[ACCENT if s < 0 else MUTED for s in df.seconds],
        text=[f"{s:+.3f}s" for s in df.seconds], textposition="outside",
        hovertemplate="<b>%{y}</b><br>%{x:+.3f}s<extra></extra>",
    ))
    span = float(df.seconds.abs().max())
    fig.update_layout(
        height=max(240, 40 * len(df)),
        xaxis=dict(title="Seconds, negative is faster",
                   range=[-span * 1.6, span * 1.6], zeroline=True,
                   zerolinecolor=INK, **AXIS_BASE),
        yaxis=dict(title=None, **AXIS_BASE),
        **PLOT_BASE,
    )
    return fig
