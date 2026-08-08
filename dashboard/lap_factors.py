"""
lap_factors.py - section 2, "what made the lap what it was".

Answers one question about one lap: why was it faster or slower than a normal
lap in the same race, and how much of that is actually explained by anything
recorded.

Two different things are shown, and they are different kinds of claim.

The WATERFALL is a decomposition, not a test. Each factor's contribution is
its coefficient times how far this lap sat from a typical lap in this race.
It is arithmetic on a fitted model.

The ANOVA is the test. Type II sums of squares over 77,000 clean race laps,
giving each factor a share of the variance it explains, an F statistic and a
p-value. It answers "does this factor matter at all", which is a question
about the sport rather than about one lap.

A single lap has no R-squared, so the page never claims one. What it does
claim, prominently, is the size of the leftover: about 93% of why one lap
differs from another in the same race is not explained by anything measured.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app_common import query
from story_common import ACCENT, AXIS_BASE, INK, MUTED, PLOT_BASE, guide

NUMERIC_TERMS = ["lap_number", "tyre_age", "rainfall", "track_temperature",
                 "air_temperature", "humidity", "wind_speed",
                 "gap_ahead", "in_dirty_air", "out_of_position",
                 "being_lapped", "yellow_sector"]

# The factors fact_lap stores directly, and therefore the ones that can be
# null. A null here costs that factor its bar and nothing more: see blocked()
# and missing_factors(). The rest of the model inputs are computed in derive()
# and cannot be missing.
SOFT_TERMS = ["compound", "lap_number", "tyre_age", "track_temperature",
              "air_temperature", "humidity", "wind_speed", "wind_direction"]

PRETTY = {
    "lap_number": "Fuel load",
    "tyre_age": "Tyre age",
    "rainfall": "Rain",
    "track_temperature": "Track temperature",
    "air_temperature": "Air temperature",
    "humidity": "Humidity",
    "wind_speed": "Wind speed",
    "gap_ahead": "Traffic ahead",
    "in_dirty_air": "Dirty air",
    "out_of_position": "Out of position, gap counted in laps",
    "being_lapped": "Being lapped",
    "yellow_sector": "Sector yellow flag",
    "compound": "Tyre compound",
    "wind_direction": "Wind direction",
    "team": "The car",
    "wind": "Wind direction",
}

UNITS = {
    "lap_number": "laps of fuel burned",
    "tyre_age": "laps on the tyre",
    "rainfall": "rain flag",
    "track_temperature": "C",
    "air_temperature": "C",
    "humidity": "%",
    "wind_speed": "m/s",
    "gap_ahead": "s to the car ahead",
    "in_dirty_air": "within 1.5s",
    "out_of_position": "car ahead a lap or more away",
    "being_lapped": "a lap or more behind the leader",
    "yellow_sector": "yellow in a marshal sector",
}

# Mirrors s05b. The app derives these from columns fact_lap already carries,
# rather than the pipeline shipping a second copy of every lap.
GAP_CAP_SECONDS = 10.0
DIRTY_AIR_SECONDS = 1.5


def derive(lap) -> dict:
    """The model inputs that are computed rather than stored."""
    gap = getattr(lap, "interval_seconds", np.nan)
    laps_behind = getattr(lap, "interval_laps", np.nan)
    leader_laps = getattr(lap, "gap_to_leader_laps", np.nan)
    yellow = getattr(lap, "yellow_sector_flag", np.nan)
    bearing = np.radians(float(lap.wind_direction)) \
        if pd.notna(getattr(lap, "wind_direction", np.nan)) else np.nan
    speed = float(lap.wind_speed) if pd.notna(lap.wind_speed) else np.nan
    return {
        "gap_ahead": GAP_CAP_SECONDS if pd.isna(gap)
                     else min(float(gap), GAP_CAP_SECONDS),
        "in_dirty_air": 0 if pd.isna(gap) else int(float(gap) < DIRTY_AIR_SECONDS),
        # A gap reported in laps, or no gap at all, means the car ahead was not
        # close enough to time. gap_ahead is a filled cap on those laps, so the
        # flag tells the model not to read it as clear air. Mirrors s05b.
        "out_of_position": int(pd.notna(laps_behind) or pd.isna(gap)),
        "being_lapped": int(pd.notna(leader_laps)),
        "yellow_sector": 0 if pd.isna(yellow) else int(yellow),
        "wind_u": speed * np.cos(bearing),
        "wind_v": speed * np.sin(bearing),
    }


# --- data ------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def anova() -> pd.DataFrame:
    return query("SELECT * FROM lap_factor_anova ORDER BY rank")


@st.cache_data(show_spinner=False)
def coefficients() -> pd.DataFrame:
    return query("SELECT * FROM lap_factor_model")


@st.cache_data(show_spinner=False)
def reference(session_key: int) -> pd.DataFrame:
    return query("SELECT * FROM lap_factor_reference WHERE session_key = ?",
                 (int(session_key),))


@st.cache_data(show_spinner=False)
def tow() -> pd.DataFrame:
    """Top speed and DRS usage by gap to the car ahead. Six races only."""
    return query("SELECT * FROM telemetry_tow")


@st.cache_data(show_spinner=False)
def telemetry() -> pd.DataFrame:
    """DRS coefficients and the diagnostics that qualify them."""
    return query("SELECT * FROM telemetry_effect")


def telemetry_note(rows: pd.DataFrame, term: str) -> str:
    """The note s05d wrote for one diagnostic, or nothing if it is absent."""
    hit = rows[(rows.kind == "diagnostic") & (rows.term == term)]
    return str(hit.note.iloc[0]) if len(hit) and pd.notna(hit.note.iloc[0]) else ""


# --- decomposition ----------------------------------------------------------------

def _compound_coef(coefs: pd.DataFrame, level) -> float:
    """
    Treatment coding leaves the base compound out of the parameter list, so a
    lap on it contributes exactly zero rather than being missing.
    """
    if not level or pd.isna(level):
        return 0.0
    hit = coefs[coefs.term == f"C(compound)[T.{level}]"]
    return float(hit.coefficient.iloc[0]) if len(hit) else 0.0


def blocked(lap, refs: pd.DataFrame) -> str | None:
    """
    Why this lap cannot be decomposed AT ALL, or None when it can.

    Only three things belong here, and none of them is a missing value.

    A missing factor is a hole in the data, not a reason to refuse: the lap
    still has a time, the model still has a baseline, and every other factor
    can still be priced. Those cases are reported by missing_factors() and the
    waterfall is drawn without them. Refusing the whole breakdown because one
    of fourteen columns is null threw away thirteen answers to protect one.

    What genuinely cannot be done:

      no baseline    the race was dropped when the model was fitted, so there
                     is no reference lap to measure departures from
      no endpoint    the lap has no duration, so the waterfall has nothing to
                     add up to
      not a racing   a neutralised or pit-out lap has a real time that is not
      lap            racing pace. The model was never fitted on these, so its
                     coefficients would be extrapolation and the leftover bar
                     would be thirty seconds of safety car labelled "not
                     measured". That is worse than saying no.
    """
    if refs.empty:
        return ("This race is not in the model. Its stint data could not be "
                "trusted, so it was excluded when the model was fitted.")
    if pd.isna(lap.lap_duration) or pd.isna(lap.lap_vs_median):
        return "This lap has no recorded duration."
    if lap.neutralised == 1:
        return ("This lap ran under a safety car, virtual safety car or red "
                "flag, so its time reflects a delta rather than racing pace.")
    if lap.is_pit_out_lap == 1:
        return "This is a pit out lap, so part of it was spent in the pit lane."
    return None


def missing_factors(lap) -> list[str]:
    """
    Which factors this lap has no value for, named the way the page names them.

    A factor in this list gets no bar. The lap is then priced as if it sat at
    the race's reference for that factor, so its real effect falls into the
    "not measured" bar rather than being attributed to something else. That is
    the honest place for it, and the page says which ones went there.
    """
    return [PRETTY.get(c, c) for c in SOFT_TERMS
            if pd.isna(getattr(lap, c, np.nan))]


def _level_coef(coefs: pd.DataFrame, factor: str, level) -> float:
    if not level or pd.isna(level):
        return 0.0
    hit = coefs[coefs.term == f"C({factor})[T.{level}]"]
    return float(hit.coefficient.iloc[0]) if len(hit) else 0.0


def decompose(lap, coefs: pd.DataFrame, refs: pd.DataFrame,
              teams_in_race=None) -> pd.DataFrame:
    """
    One row per factor: how many seconds it added to or took off this lap.

    Measured against a typical lap of the same race rather than a global
    average, because that is the comparison the question implies. Fifty laps
    of fuel burned is unremarkable at lap 50 and remarkable at lap 5.

    Three factors need more than a coefficient lookup.

    The car is compared to the other cars that were actually in this race,
    not to a base team buried in the coding. "Half a second better than the
    average car here" is a statement about the race; "0.06s versus Alpine" is
    an artefact of alphabetical ordering.

    Wind direction has a coefficient per circuit, because position
    coordinates carry no compass bearing and the model had to learn each
    track's orientation for itself.

    Traffic is derived from the gap to the car ahead, which fact_lap already
    records but nothing was reading.
    """
    by_term = coefs.set_index("term").coefficient
    num_ref = refs.set_index("term").reference_value
    extra = derive(lap)

    def ref_level(term):
        hit = refs[refs.term == term]
        return hit.reference_level.iloc[0] if len(hit) else None

    rows = []
    for term in NUMERIC_TERMS:
        if term not in by_term.index or term not in num_ref.index:
            continue
        value = float(extra.get(term, getattr(lap, term, np.nan)))
        ref = float(num_ref[term])
        if pd.isna(value) or pd.isna(ref):
            continue
        rows.append({
            "factor": PRETTY[term], "term": term,
            "seconds": float(by_term[term]) * (value - ref),
            "value": value, "reference": ref, "unit": UNITS.get(term, ""),
        })

    # No compound means no bar. It must NOT fall through to _level_coef, which
    # returns 0.0 for a null level and would therefore price an unknown tyre as
    # the model's base compound: a confident number invented from nothing.
    if pd.notna(getattr(lap, "compound", None)):
        rows.append({
            "factor": PRETTY["compound"], "term": "compound",
            "seconds": (_level_coef(coefs, "compound", lap.compound)
                        - _level_coef(coefs, "compound", ref_level("compound"))),
            "value": lap.compound, "reference": ref_level("compound"),
            "unit": "",
        })

    team = getattr(lap, "team_name", None)
    if team and teams_in_race:
        field = np.mean([_level_coef(coefs, "team_name", t)
                         for t in teams_in_race])
        rows.append({
            "factor": PRETTY["team"], "term": "team",
            "seconds": _level_coef(coefs, "team_name", team) - float(field),
            "value": team, "reference": "the average car here", "unit": "",
        })

    circuit = ref_level("circuit")
    if circuit and not pd.isna(extra["wind_u"]):
        seconds = 0.0
        for axis in ("u", "v"):
            term = f"C(circuit_short_name)[{circuit}]:wind_{axis}"
            if term in by_term.index and f"wind_{axis}" in num_ref.index:
                seconds += float(by_term[term]) * (
                    extra[f"wind_{axis}"] - float(num_ref[f"wind_{axis}"]))
        rows.append({
            "factor": PRETTY["wind"], "term": "wind", "seconds": seconds,
            "value": f"{lap.wind_speed:g} m/s from {int(lap.wind_direction)} deg",
            "reference": "the race's usual wind", "unit": "",
        })

    out = pd.DataFrame(rows)
    return out.reindex(out.seconds.abs().sort_values(ascending=False).index)


def summarise(lap, parts: pd.DataFrame, coefs: pd.DataFrame,
              refs: pd.DataFrame, teams_in_race=None) -> dict:
    """
    The three numbers the waterfall has to add up to.

    The baseline is the model's prediction for a reference lap of this race:
    median conditions, the race's usual compound, and the average car in it.
    Every bar after that is a departure from exactly that lap, so baseline
    plus contributions plus residual reconstructs the real lap time.
    """
    by_term = coefs.set_index("term").coefficient
    num_ref = refs.set_index("term").reference_value

    baseline = float(coefs.loc[coefs.term == "Intercept", "coefficient"].iloc[0])
    for term in NUMERIC_TERMS + ["wind_u", "wind_v"]:
        if term in by_term.index and term in num_ref.index:
            baseline += float(by_term[term]) * float(num_ref[term])

    def ref_level(term):
        hit = refs[refs.term == term]
        return hit.reference_level.iloc[0] if len(hit) else None

    baseline += _level_coef(coefs, "compound", ref_level("compound"))

    if teams_in_race:
        baseline += float(np.mean([_level_coef(coefs, "team_name", t)
                                   for t in teams_in_race]))

    circuit = ref_level("circuit")
    if circuit:
        for axis in ("u", "v"):
            term = f"C(circuit_short_name)[{circuit}]:wind_{axis}"
            if term in by_term.index and f"wind_{axis}" in num_ref.index:
                baseline += float(by_term[term]) * float(num_ref[f"wind_{axis}"])

    explained = float(parts.seconds.sum())
    actual = float(lap.lap_vs_median)
    return {"baseline": baseline, "explained": explained,
            "unexplained": actual - baseline - explained, "actual": actual}


# --- charts ------------------------------------------------------------------------

def waterfall(parts: pd.DataFrame, totals: dict) -> go.Figure:
    """From a typical lap of this race to this one, in seconds."""
    labels = (["Typical lap here"] + parts.factor.tolist()
              + ["Not measured", "This lap"])
    values = ([totals["baseline"]] + parts.seconds.tolist()
              + [totals["unexplained"], 0])
    measures = (["absolute"] + ["relative"] * len(parts)
                + ["relative", "total"])

    fig = go.Figure(go.Waterfall(
        orientation="v", measure=measures, x=labels, y=values,
        text=[f"{v:+.3f}" if m != "total" else f"{totals['actual']:+.3f}"
              for v, m in zip(values, measures)],
        textposition="outside",
        connector=dict(line=dict(color="rgba(0,0,0,0.18)")),
        increasing=dict(marker=dict(color=ACCENT)),
        decreasing=dict(marker=dict(color="#2E7D32")),
        totals=dict(marker=dict(color=INK)),
    ))
    fig.update_layout(
        height=430,
        yaxis=dict(title="Seconds against the race median", **AXIS_BASE),
        xaxis=dict(title=None, tickangle=-30, **AXIS_BASE),
        **PLOT_BASE,
    )
    return fig


def variance_bar(table: pd.DataFrame) -> go.Figure:
    """Share of within-race lap variation each factor explains."""
    df = table.sort_values("pct_variance")
    colours = [ACCENT if r.is_residual else MUTED for r in df.itertuples()]
    fig = go.Figure(go.Bar(
        x=df.pct_variance, y=df.factor, orientation="h",
        marker_color=colours,
        text=[f"{v:.2f}%" for v in df.pct_variance],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>%{x:.3f}% of variance<extra></extra>",
    ))
    fig.update_layout(
        height=max(300, 34 * len(df)),
        xaxis=dict(title="Share of within-race lap-time variance (%)",
                   **AXIS_BASE),
        yaxis=dict(title=None, **AXIS_BASE),
        **PLOT_BASE,
    )
    return fig


def tow_chart(df: pd.DataFrame) -> go.Figure:
    """
    Top speed against gap to the car ahead.

    Deliberately not a fitted line. The claim is that cars running close reach
    a higher top speed than cars running alone, and a bar per gap band either
    shows that or it does not. A regression would let a reader assume the
    relationship is smooth, which is not something these six races can support.
    """
    order = ["under 1s", "1 to 1.5s", "1.5 to 2s", "2 to 3s", "3 to 5s",
             "over 5s"]
    df = df.set_index("bucket").reindex(
        [b for b in order if b in set(df.bucket)]).reset_index()

    fig = go.Figure(go.Bar(
        x=df.bucket, y=df.mean_top_speed,
        marker_color=[ACCENT if b == "under 1s" else MUTED for b in df.bucket],
        text=[f"{v:.1f}" for v in df.mean_top_speed],
        textposition="outside",
        customdata=np.stack([df.laps, df.mean_drs_share * 100,
                             df.top_speed_vs_clear_air], axis=-1),
        hovertemplate=("<b>%{x} behind</b><br>%{y:.1f} km/h mean top speed"
                       "<br>%{customdata[2]:+.1f} km/h vs clear air"
                       "<br>%{customdata[1]:.1f}% of the lap with DRS open"
                       "<br>%{customdata[0]:,} laps<extra></extra>"),
    ))
    lo = float(df.mean_top_speed.min())
    hi = float(df.mean_top_speed.max())
    pad = max(4.0, 0.35 * (hi - lo))
    fig.update_layout(
        height=340,
        xaxis=dict(title="Gap to the car ahead at the start of the lap",
                   **AXIS_BASE),
        # The axis does not start at zero on purpose: every bar is above
        # 300 km/h and a zero baseline would flatten a real 20 km/h spread
        # into six bars of the same height.
        yaxis=dict(title="Mean top speed (km/h)", range=[lo - pad, hi + pad],
                   **AXIS_BASE),
        **PLOT_BASE,
    )
    return fig
