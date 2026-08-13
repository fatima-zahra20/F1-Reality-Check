"""
diagnose.py - the diagnostic layer: why it happened.

Follows DIAGNOSTIC ANALYTICS/diagnostic_question_bank.md section by section.
That bank states its own design intent: "Each question below builds directly
on its descriptive counterpart", so every section carries a link through to the
matching block in Analyse, where the underlying facts are shown.

Everything here is read from the diag_* tables written by pipeline/s05_diagnostic.py.
Nothing is recomputed in the app: the statistics on the page are the statistics
the pipeline produced, so a number shown here can always be traced to a run.

A test that failed to reach significance is displayed as prominently as one
that succeeded. Almost every test carries a caveat, and the caveat sits next to
the conclusion rather than behind a click, because several of these findings
mean much less than the headline suggests. Counts are deliberately not written
out here; they go stale, and this docstring claimed twenty of twenty-one long
after it became twenty-eight of twenty-nine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit.errors import StreamlitAPIException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app_common import NEUTRAL, query, render_footer, team_colours  # noqa: E402
from story_common import (  # noqa: E402
    ACCENT, AXIS_BASE, INK, MUTED, PLOT_BASE, guide,
)

# ONE ENTRY PER QUESTION GROUP, not per notebook, and the two are not the same
# thing. There are seven notebooks in DIAGNOSTIC ANALYTICS and eleven groups in
# the question bank, because a notebook was a convenient place to do work and a
# group is a subject a reader looks for. Grouping the page by notebook filename
# made the reader learn the filing system before they could find a question.
#
# So the eleven titles below are the question bank's own, and they line up
# one-to-one with the descriptive layer's sections. Every section here hands
# over to the story section of the same name.
#
# All 29 tests are still here, each in exactly one group, and every group has at
# least one. Three groups draw on more than one notebook: race pace takes the
# stint-variation test from satart_lap and the sector-consistency test from
# tyre_strategy, tyre strategy takes the two degradation tests from satart_lap,
# and the three outcome groups split team_driver_outcome by subject. The
# notebook each test came from is shown on the page, so the provenance is not
# lost by regrouping.
#
# Numbers are recomputed against current silver, never carried over from the
# notebooks, which ran on pre-backfill data with the old caution flag.

# Which notebook each test was worked out in. Kept separate from the grouping
# above precisely because the two no longer coincide.
NOTEBOOKS = {
    "grid_setup": ["T13", "T03", "T04"],
    "satart_lap": ["T16", "T17", "T18", "T11a", "T11b"],
    "tyre_strategy": ["T19", "T20", "T21", "T05a", "T05b"],
    "pit_stops": ["T07a", "T07b", "T22"],
    "position": ["T06", "T15", "T23", "T08"],
    "incidents": ["T09", "T09b", "T12", "T10"],
    "team_driver_outcome": ["T24", "T01", "T01b", "T14", "T02"],
}
NOTEBOOK_OF = {tid: nb for nb, ids in NOTEBOOKS.items() for tid in ids}

SECTIONS = [
    ("grid_setup", "Pre-race, grid & setup", ["T13", "T03", "T04"],
     "Story of a Race", "grid_setup", "Pre-race, grid and setup"),
    ("lap1", "The start, lap 1", ["T16", "T17"],
     "Story of a Race", "lap1", "The start, lap 1"),
    ("lap_by_lap", "Race pace, lap by lap", ["T18", "T20"],
     "Story of a Race", "lap_by_lap", "Race pace, lap by lap"),
    ("tyres", "Tyre strategy",
     ["T11a", "T11b", "T05a", "T05b", "T19", "T21"],
     "Story of a Race", "tyres", "Tyre strategy"),
    ("pit_stops", "Pit stops", ["T07a", "T07b", "T22"],
     "Story of a Race", "pit_stops", "Pit stops"),
    ("position", "Position dynamics across the race", ["T06", "T15", "T08"],
     "Story of a Race", "position", "Position dynamics"),
    ("gaps", "Gaps & race context", ["T23"],
     "Story of a Race", "gaps", "Gaps and race context"),
    ("incidents", "Incidents & external context",
     ["T09", "T09b", "T12", "T10"],
     "Story of a Race", "incidents", "Incidents and conditions"),
    ("radio", "Team radio", ["T24"],
     "Story of a Race", "radio", "Team radio"),
    ("outcome", "Finish & outcome", ["T01", "T01b", "T14"],
     "Story of a Race", "outcome", "Finish and outcome"),
    ("teammate", "Driver vs teammate", ["T02"],
     "Story of a Driver", "teammate", "Against the teammate"),
]


# --- formatting ---------------------------------------------------------------

def fmt_p(p) -> str:
    """A p-value of exactly 0 is a rounding artefact, not a certainty."""
    if pd.isna(p):
        return "not applicable"
    if p == 0 or p < 1e-4:
        return "< 0.0001"
    return f"{p:.4f}"


def verdict(test) -> tuple[str, str]:
    """(coloured label, plain explanation) for one test's outcome."""
    caveat = test.caveat or ""
    if "DESCRIPTIVE ONLY" in caveat:
        return (":grey[Descriptive only]",
                "Reported as description. The sample is too small to test.")
    if test.significant:
        return (":green[Supported]",
                "The effect is statistically distinguishable from no effect.")
    return (":orange[Not supported]",
            "No effect large enough to separate from chance in this data.")


def _stats(test, coefs: pd.DataFrame) -> None:
    """The full statistical apparatus, one click away from the conclusion."""
    with st.expander("Method and statistics"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Effect size",
                  f"{test.effect_size:.3f}" if pd.notna(test.effect_size) else "-",
                  str(test.effect_size_type or "").replace("_", " ") or None)
        c2.metric("p-value", fmt_p(test.p_value))
        c3.metric("Test statistic",
                  f"{test.statistic:,.3f}" if pd.notna(test.statistic) else "-")
        c4.metric("Sample size", f"{int(test.n):,}" if pd.notna(test.n) else "-")

        st.caption(f"**Method.** {test.method}")

        mine = coefs[coefs.test_id == test.test_id]
        if len(mine):
            show = pd.DataFrame({
                "Predictor": mine.predictor,
                "Coefficient": mine.coefficient,
                "Standardised": mine.std_coefficient,
                "Std error": mine.std_error,
                "p": mine.p_value.map(fmt_p),
                "95% CI low": mine.ci_lower,
                "95% CI high": mine.ci_upper,
                "VIF": mine.vif,
            })
            st.dataframe(show, hide_index=True, width="stretch",
                         column_config={
                             c: st.column_config.NumberColumn(format="%.4f")
                             for c in ["Coefficient", "Standardised", "Std error",
                                       "95% CI low", "95% CI high", "VIF"]})
            if mine.vif.notna().any():
                st.caption(
                    f"VIF peaks at {mine.vif.max():.2f}. Above about 5 the "
                    "predictors overlap enough that individual coefficients "
                    "stop being separable; these are below that."
                )


def _link(story: str, section_key: str, block: str, key: str) -> None:
    """
    Send the reader to the descriptive section this question builds on, with
    both the story and the section already selected.

    st.switch_page resolves its argument relative to whichever file Streamlit
    was launched with. In production that is streamlit_app.py at the repo root,
    so the path carries the dashboard/views prefix; run this page directly and
    the prefix is wrong. Both are tried rather than assuming one, so the link
    works under the app and under the test harness.
    """
    if st.button(f"See \"{block}\" in {story}", key=key):
        st.session_state["story_choice"] = story
        st.session_state["section_choice"] = section_key
        for path in ("dashboard/views/analyse.py", "analyse.py"):
            try:
                st.switch_page(path)
            except StreamlitAPIException:
                continue


# --- chart helpers -------------------------------------------------------------

def _bar_layout(fig, xtitle, ytitle, height, reverse_y=False):
    y = dict(title=ytitle, **AXIS_BASE)
    if reverse_y:
        y["autorange"] = "reversed"
    fig.update_layout(height=height, showlegend=False,
                      xaxis=dict(title=xtitle, **AXIS_BASE), yaxis=y,
                      **PLOT_BASE)
    return fig


def group_bar(groups: pd.DataFrame, metric: str, group_type: str,
              xtitle: str, colour_by_team: bool = False,
              sort: bool = True, height: int | None = None):
    """Horizontal bars for a group comparison, with confidence intervals."""
    g = groups[(groups.metric == metric) & (groups.group_type == group_type)].copy()
    if g.empty:
        return None
    if sort:
        g = g.sort_values("value")

    colours = ([team_colours().get(n, NEUTRAL) for n in g.group_name]
               if colour_by_team else INK)
    has_ci = g.ci_lower.notna().all() and g.ci_upper.notna().all()

    fig = go.Figure(go.Bar(
        x=g.value, y=g.group_name, orientation="h", marker_color=colours,
        error_x=dict(type="data", symmetric=False,
                     array=(g.ci_upper - g.value) if has_ci else None,
                     arrayminus=(g.value - g.ci_lower) if has_ci else None,
                     color="rgba(0,0,0,0.45)", thickness=1.2)
        if has_ci else None,
        customdata=np.stack([g.n], axis=-1),
        hovertemplate="<b>%{y}</b><br>%{x:.3f}<br>n=%{customdata[0]:,}<extra></extra>",
    ))
    return _bar_layout(fig, xtitle, None, height or max(300, 30 * len(g)))


def scatter_fit(points: pd.DataFrame, xtitle: str, ytitle: str,
                slope: float | None = None, intercept: float | None = None,
                colour_by_group: bool = False, reverse_y: bool = False):
    """Observation cloud with the fitted line drawn through it."""
    if points.empty:
        return None
    colours = ([team_colours().get(g, NEUTRAL) for g in points.group_name]
               if colour_by_group and points.group_name.notna().any() else INK)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=points.x, y=points.y, mode="markers",
        marker=dict(size=5, color=colours, opacity=0.45,
                    line=dict(width=0)),
        customdata=np.stack([points.label.fillna("")], axis=-1),
        hovertemplate="%{customdata[0]}<br>%{x:.3f}, %{y:.3f}<extra></extra>",
    ))
    if slope is not None and intercept is not None:
        xs = np.array([points.x.min(), points.x.max()])
        fig.add_trace(go.Scatter(
            x=xs, y=intercept + slope * xs, mode="lines",
            line=dict(color=ACCENT, width=2.5), hoverinfo="skip",
        ))
    return _bar_layout(fig, xtitle, ytitle, 460, reverse_y=reverse_y)


# --- per-test charts -----------------------------------------------------------

def chart(test_id: str, tests: pd.DataFrame, coefs: pd.DataFrame,
          groups: pd.DataFrame, points: pd.DataFrame) -> None:
    """Draws whichever chart fits this test, plus its reading guide."""
    pts = points[points.test_id == test_id]
    cf = coefs[coefs.test_id == test_id]

    # --- grid and setup ---
    if test_id == "T13":
        fig = group_bar(groups, "quali_delta_seconds", "teammate_pair",
                        "Qualifying gap between teammates (s)", sort=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "One row per teammate pairing. The bar is the average "
                "qualifying gap between the two drivers, and the whisker is "
                "the 95% confidence interval. A whisker that clears zero means "
                "the gap is real rather than noise. Because both drivers share "
                "a car, what is left is close to a pure driver difference."
            )

    elif test_id == "T03":
        slope = cf[cf.predictor == "grid_position"].coefficient
        # Grid and finish are both small integers, so a raw scatter collapses
        # into an unreadable block. Counting how often each pairing occurs is
        # the same data, legible.
        if len(pts):
            grid = (pts.groupby(["x", "y"]).size().reset_index(name="races"))
            fig = go.Figure(go.Heatmap(
                x=grid.x, y=grid.y, z=grid.races, colorscale="Reds",
                hovertemplate="Started P%{x}<br>Finished P%{y}"
                              "<br>%{z} times<extra></extra>",
                colorbar=dict(title="Races"),
            ))
            if len(slope):
                xs = np.array([pts.x.min(), pts.x.max()])
                b = pts.y.mean() - slope.iloc[0] * pts.x.mean()
                fig.add_trace(go.Scatter(
                    x=xs, y=b + slope.iloc[0] * xs, mode="lines",
                    line=dict(color=INK, width=2, dash="dash"),
                    hoverinfo="skip", showlegend=False))
            fig.update_layout(height=480, **PLOT_BASE,
                              xaxis=dict(title="Grid position", **AXIS_BASE),
                              yaxis=dict(title="Finish position",
                                         autorange="reversed", **AXIS_BASE))
            st.plotly_chart(fig, width="stretch")
            guide(
                "Each cell counts how often a driver starting in that grid "
                "slot finished in that position, darker meaning more often. "
                "The dark band running corner to corner is the relationship: "
                "drivers mostly finish near where they started. The dashed "
                "line is the fitted trend. Cells far from the band are the "
                "recoveries and the retirements. Classified finishers only."
            )

    elif test_id == "T04":
        fig = group_bar(groups, "grid_to_finish_slope", "circuit_type",
                        "Places lost per grid place further back", sort=False,
                        height=240)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "How steeply grid position translates into finish position at "
                "each circuit type. A taller bar would mean a poor grid slot "
                "hurts more there. The two bars are almost identical, which is "
                "the finding: street circuits did not punish a bad grid slot "
                "more than permanent ones."
            )

    # --- the start ---
    elif test_id == "T16":
        if len(pts):
            # Mean swing per grid slot answers "do certain grid positions
            # systematically gain" far more directly than 1,568 raw points.
            by_slot = (pts.groupby("x")
                          .agg(mean_swing=("y", "mean"), n=("y", "size"))
                          .reset_index())
            fig = go.Figure(go.Bar(
                x=by_slot.x, y=by_slot.mean_swing,
                marker_color=[ACCENT if v < 0 else INK for v in by_slot.mean_swing],
                customdata=np.stack([by_slot.n], axis=-1),
                hovertemplate="Grid P%{x}<br>%{y:+.2f} places on average"
                              "<br>n=%{customdata[0]}<extra></extra>",
            ))
            fig.update_layout(height=380, showlegend=False, **PLOT_BASE,
                              xaxis=dict(title="Grid position", **AXIS_BASE),
                              yaxis=dict(title="Average places gained on lap 1",
                                         zeroline=True,
                                         zerolinecolor="rgba(0,0,0,0.35)",
                                         **AXIS_BASE))
            st.plotly_chart(fig, width="stretch")
            guide(
                "Average places gained on the opening lap from each grid slot. "
                "Bars above the line gained, bars below lost. The pattern "
                "tilts upward toward the back simply because a car at the "
                "front has places to lose and none to gain. The regression "
                "behind this explains only 7.5% of lap-1 movement, so the "
                "individual race matters far more than the starting slot."
            )

    # --- tyres ---
    elif test_id == "T11a":
        g = groups[(groups.test_id == "T11a")]
        corr = g[g.metric == "degradation_slope"].sort_values("value")
        unc = g[g.metric == "degradation_slope_uncorrected"]
        if len(corr):
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=corr.value, y=corr.group_name, orientation="h",
                name="Fuel-corrected", marker_color=INK,
                error_x=dict(type="data", symmetric=False,
                             array=(corr.ci_upper - corr.value),
                             arrayminus=(corr.value - corr.ci_lower),
                             color="rgba(0,0,0,0.45)", thickness=1.2)
                if corr.ci_lower.notna().all() else None,
                hovertemplate="<b>%{y}</b><br>%{x:+.3f}s per lap<extra></extra>",
            ))
            if len(unc):
                u = unc.set_index("group_name").reindex(corr.group_name)
                fig.add_trace(go.Bar(
                    x=u.value, y=u.index, orientation="h",
                    name="Uncorrected", marker_color=MUTED,
                    hovertemplate="<b>%{y}</b><br>%{x:+.3f}s per lap"
                                  "<extra>uncorrected</extra>",
                ))
            fig.update_layout(height=340, barmode="group",
                              legend=dict(orientation="h", yanchor="bottom",
                                          y=1.0, x=0),
                              xaxis=dict(title="Lap time change per lap of tyre age (s)",
                                         zeroline=True,
                                         zerolinecolor="rgba(0,0,0,0.35)",
                                         **AXIS_BASE),
                              yaxis=dict(title=None, **AXIS_BASE), **PLOT_BASE)
            st.plotly_chart(fig, width="stretch")
            guide(
                "Positive means lap times got slower as the tyre aged, which "
                "is degradation. Whiskers are 95% confidence intervals: where "
                "two whiskers overlap, those compounds cannot be separated. "
                "The dark bars are corrected for fuel burn, the pale bars are "
                "not, and the gap between them is how much a car speeding up "
                "on a lightening fuel load masks tyre wear. Compounds are "
                "chosen by teams for expected conditions, never assigned at "
                "random, so this measures how each was used, not its chemistry."
            )

    elif test_id == "T11b":
        fig = group_bar(groups, "degradation_slope", "team",
                        "Lap time change per lap of tyre age (s)",
                        colour_by_team=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "Average degradation slope by team, positive meaning lap times "
                "worsened as tyres aged. Compound is not held constant here, "
                "and the compound effect is roughly ten times larger than the "
                "team effect, so read this as a weak signal sitting on top of "
                "a much stronger one."
            )

    # --- strategy ---
    elif test_id == "T05b":
        fig = group_bar(groups, "positions_gained", "pit_group",
                        "Average places gained", sort=False, height=260)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "Average places gained by stop count, with 95% confidence "
                "intervals. These are the raw group means, which look similar. "
                "The model behind this section adds grid position as a "
                "covariate, and only then does a stop-count penalty appear: "
                "multi-stop strategies are run disproportionately by cars "
                "starting further back, which have more places available to "
                "gain, and that masks the cost."
            )

    # --- pit stops ---
    elif test_id == "T07a":
        fig = group_bar(groups, "mean_lane_duration", "team",
                        "Average time in pit lane (s)", colour_by_team=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "Average pit lane transit per team with 95% confidence "
                "intervals, disaster stops removed. Teams whose intervals do "
                "not overlap are genuinely different. The spread across the "
                "whole field is only about a second, so this is a real effect "
                "that is small next to almost everything else in a race."
            )

    elif test_id == "T07b" and len(cf):
        lane = cf[cf.predictor == "lane_duration"]
        if len(lane):
            st.metric("Cost of one extra second in the pit lane",
                      f"{lane.coefficient.iloc[0]:.3f} places",
                      f"95% CI {lane.ci_lower.iloc[0]:.3f} to "
                      f"{lane.ci_upper.iloc[0]:.3f}", delta_color="off")
            guide(
                "Each extra second stationary or crawling in the lane costs "
                "about a tenth of a finishing place. Real, but adding stop "
                "duration to a model that already knows the grid slot improves "
                "it by only 0.010 of R squared. The dominant missing factor is "
                "whether the car ahead pitted on the same lap, the undercut."
            )

    # --- position dynamics ---
    elif test_id == "T06" and len(cf):
        gap = cf[cf.predictor == "gap_to_ahead"]
        tyre = cf[cf.predictor == "tyre_delta"]
        c1, c2 = st.columns(2)
        if len(gap):
            c1.metric("Effect of gap to the car ahead",
                      f"{gap.coefficient.iloc[0]:.3f} log-odds per second",
                      "closer is far better", delta_color="off")
        if len(tyre):
            c2.metric("Effect of tyre age difference",
                      f"{tyre.coefficient.iloc[0]:.3f} log-odds per lap",
                      "fresher tyres help", delta_color="off")
        guide(
            "Both numbers are log-odds, the natural scale of a logistic "
            "model: negative means the chance of completing a pass falls as "
            "that quantity rises. Gap matters enormously per unit but only "
            "ranges over two seconds here; tyre age matters little per lap but "
            "ranges over tens of laps, so their real-world weight is closer "
            "than the raw coefficients suggest."
        )

    elif test_id == "T15" and len(cf):
        show = cf[["predictor", "coefficient", "ci_lower", "ci_upper"]].copy()
        show["predictor"] = show.predictor.str.replace("_", " ")
        fig = go.Figure(go.Bar(
            x=show.coefficient, y=show.predictor, orientation="h",
            marker_color=[ACCENT if v < 0 else INK for v in show.coefficient],
            error_x=dict(type="data", symmetric=False,
                         array=show.ci_upper - show.coefficient,
                         arrayminus=show.coefficient - show.ci_lower,
                         color="rgba(0,0,0,0.45)", thickness=1.2),
            hovertemplate="<b>%{y}</b><br>%{x:+.3f} places<extra></extra>",
        ))
        st.plotly_chart(
            _bar_layout(fig, "Effect on position swing (places)", None, 260),
            width="stretch")
        guide(
            "What each event does to a driver's position on the lap it "
            "happens. Pitting costs about nine tenths of a place; a safety car "
            "discounts that, because everyone else has slowed too. Together "
            "these explain only 2.6% of all swing variance, which is expected: "
            "most laps are quiet, and the model is describing the few that "
            "are not."
        )

    # --- gaps ---
    elif test_id == "T08":
        fig = group_bar(groups, "lapping_rate", "team",
                        "Share of classified finishes ending lapped",
                        colour_by_team=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "The share of a team's classified finishes that ended at least "
                "one lap down. This counts only cars that finished, so it "
                "measures pure pace deficit and not reliability: a car that "
                "retires never gets classified as lapped at all."
            )

    # --- incidents ---
    elif test_id == "T09":
        fig = group_bar(groups, "dnf_rate", "team", "Share of starts ending in a DNF",
                        colour_by_team=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "The share of race starts that ended in retirement, by team, "
                "across all four seasons. This one counts every starter, so "
                "unlike the lapping chart it does include reliability. The "
                "spread runs from roughly one race in sixteen to better than "
                "one in five."
            )

    elif test_id == "T09b":
        fig = group_bar(groups, "dnf_rate", "sainz_team",
                        "Share of starts ending in a DNF", sort=False, height=220)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "The same driver either side of a team change, which holds the "
                "driver constant and varies the car. Each rate lands close to "
                "that team's own average, which points at the car rather than "
                "the driver. With about 34 races per side this cannot reach "
                "significance, so it corroborates the team-level result rather "
                "than testing it."
            )

    elif test_id == "T12":
        fig = group_bar(groups, "position_change_variance", "condition",
                        "Variance in places gained across the field",
                        sort=False, height=220)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "How much finishing positions scattered relative to the grid, "
                "in wet races against dry ones. Wet races do show more "
                "scatter, but with only 17 wet races the difference cannot be "
                "separated from chance, so the visible gap is not evidence."
            )

    elif test_id == "T10":
        tab_team, tab_driver = st.tabs(["By team", "By driver"])
        with tab_team:
            fig = group_bar(groups, "wet_advantage", "team",
                            "Extra places gained in the wet vs dry",
                            colour_by_team=True)
            if fig:
                st.plotly_chart(fig, width="stretch")
        with tab_driver:
            fig = group_bar(groups, "wet_advantage", "driver",
                            "Extra places gained in the wet vs dry")
            if fig:
                st.plotly_chart(fig, width="stretch")
        guide(
            "Each entity against its own dry-weather baseline, so a positive "
            "bar means gaining more places in the wet than usual. No "
            "significance test is attached, and none should be: detecting a "
            "two-position effect reliably needs around 57 wet races per entity "
            "and there are 17 in total. The measure also flatters cars that "
            "start near the back, which have more to gain in any weather."
        )

    # --- outcome ---
    elif test_id == "T01":
        slope = cf[cf.predictor == "pace_vs_median"].coefficient
        fig = scatter_fit(
            pts, "Race pace against the session median (s per lap)",
            "Team points in the race", colour_by_group=True,
            slope=slope.iloc[0] if len(slope) else None,
            intercept=(pts.y.mean() - slope.iloc[0] * pts.x.mean())
            if len(slope) else None,
        )
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "Each dot is one team in one race, coloured by team. Left is "
                "faster than the field median that day, and higher is more "
                "points. The red line is the fitted relationship: about 10.9 "
                "points per second of lap time. Pace and reliability both "
                "track car quality, so neither coefficient should be read as "
                "an independent cause."
            )

    elif test_id == "T01b" and len(cf):
        pace = cf[cf.predictor == "mean_pace"]
        if len(pace):
            st.metric("Season points per second of lap time",
                      f"{pace.coefficient.iloc[0]:,.0f} points",
                      f"95% CI {pace.ci_lower.iloc[0]:,.0f} to "
                      f"{pace.ci_upper.iloc[0]:,.0f}", delta_color="off")
            guide(
                "The same relationship measured across whole seasons rather "
                "than single races. The confidence interval is wide because "
                "there are only 40 team-seasons in four years of data. DNF "
                "rate adds nothing detectable once pace is in the model, which "
                "is a statement about precision at this sample size, not proof "
                "that reliability does not matter."
            )

    elif test_id == "T14":
        fig = group_bar(groups, "predicted_net_gain_at_P10", "team",
                        "Predicted places gained from a P10 start",
                        colour_by_team=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "What each team's grid-to-finish conversion is worth from an "
                "identical P10 start, which strips out the advantage a "
                "backmarker gets from simply having more places available to "
                "gain. Bars above zero convert better than their grid slot "
                "implies. Without this correction the raw comparison flatters "
                "slow teams."
            )

    # --- added so every notebook question has a figure (T17-T24) ---
    elif test_id == "T17":
        fig = group_bar(groups, "lap1_chaos_rate", "circuit_type",
                        "Share of races with a lap-1 incident", sort=False,
                        height=240)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "How often the opening lap produced a flag, safety car or car "
                "event, at each circuit type. The two bars sit close together, "
                "which is the finding: a street circuit is no more likely to "
                "produce a first-lap incident than a permanent one."
            )

    elif test_id == "T18" and len(cf):
        show = cf.copy()
        show["predictor"] = (show.predictor
                             .str.replace("C(compound)[T.", "compound: ", regex=False)
                             .str.replace("]", "", regex=False)
                             .str.replace("_", " "))
        show = show.sort_values("coefficient")
        fig = go.Figure(go.Bar(
            x=show.coefficient, y=show.predictor, orientation="h",
            marker_color=[MUTED if p >= 0.05 else
                          (ACCENT if v < 0 else INK)
                          for v, p in zip(show.coefficient, show.p_value)],
            error_x=dict(type="data", symmetric=False,
                         array=show.ci_upper - show.coefficient,
                         arrayminus=show.coefficient - show.ci_lower,
                         color="rgba(0,0,0,0.45)", thickness=1.2),
            customdata=np.stack([show.p_value.map(fmt_p)], axis=-1),
            hovertemplate="<b>%{y}</b><br>%{x:+.3f}s"
                          "<br>p = %{customdata[0]}<extra></extra>",
        ))
        st.plotly_chart(
            _bar_layout(fig, "Effect on lap time vs the session median (s)",
                        None, 320), width="stretch")
        guide(
            "Each bar is that factor's effect on how far a lap sits from the "
            "session median, holding the others fixed. Negative is faster. "
            "Compound effects are against hard tyres. Pale bars did not reach "
            "significance, which includes tyre age itself: once fuel load is in "
            "the model, tyre age adds nothing detectable on its own."
        )

    elif test_id == "T19":
        tab_cause, tab_team = st.tabs(["By cause", "Unflagged share by team"])
        with tab_cause:
            fig = group_bar(groups, "cause_share", "cause",
                            "Share of unusually slow laps", height=260)
            if fig:
                st.plotly_chart(fig, width="stretch")
        with tab_team:
            fig = group_bar(groups, "unflagged_share", "team",
                            "Share of a team's slow laps with no flag",
                            colour_by_team=True)
            if fig:
                st.plotly_chart(fig, width="stretch")
        guide(
            "An unusually slow lap is one above that driver's own spread in that "
            "race, so the bar is not simply 'slow circuits'. Most such laps carry "
            "no flag at all, meaning traffic, a mistake or damage rather than a "
            "neutralisation. The per-team view shows that mix barely moves "
            "between teams."
        )

    elif test_id == "T20":
        fig = group_bar(groups, "sector_delta_seconds", "pair_sector",
                        "Sector time gap between teammates (s)", height=300)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "Only the pairings whose sector advantage survived Bonferroni "
                "correction are shown, one bar per teammate pair and sector. The "
                "whisker is the 95% confidence interval. Sectors are never "
                "compared with each other, since they differ in length; each bar "
                "compares two drivers in the same car through the same sector."
            )

    elif test_id == "T21" and len(cf):
        show = cf.copy()
        show["predictor"] = show.predictor.str.replace("_delta", "").str.replace("_", " ")
        show = show.sort_values("coefficient")
        fig = go.Figure(go.Bar(
            x=show.coefficient, y=show.predictor, orientation="h",
            marker_color=[MUTED if p >= 0.05 else INK for p in show.p_value],
            error_x=dict(type="data", symmetric=False,
                         array=show.ci_upper - show.coefficient,
                         arrayminus=show.coefficient - show.ci_lower,
                         color="rgba(0,0,0,0.45)", thickness=1.2),
            customdata=np.stack([show.p_value.map(fmt_p)], axis=-1),
            hovertemplate="<b>%{y}</b><br>%{x:+.2f} log-odds"
                          "<br>p = %{customdata[0]}<extra></extra>",
        ))
        st.plotly_chart(
            _bar_layout(fig, "Effect on the odds of finishing ahead (log-odds)",
                        None, 280), width="stretch")
        guide(
            "Restricted to races where a team ran its two cars on different stop "
            "counts. Each bar is how much that difference shifts the odds of the "
            "first car finishing ahead. Race pace dominates: the quicker car wins "
            "the intra-team battle largely regardless of which strategy it was "
            "handed. Pale bars did not reach significance."
        )

    elif test_id == "T22":
        tab_team, tab_stop = st.tabs(["By team", "By stop number"])
        with tab_team:
            fig = group_bar(groups, "disaster_rate", "team",
                            "Share of stops that were disasters",
                            colour_by_team=True)
            if fig:
                st.plotly_chart(fig, width="stretch")
        with tab_stop:
            fig = group_bar(groups, "disaster_rate", "stop_number",
                            "Share of stops that were disasters", sort=False,
                            height=240)
            if fig:
                st.plotly_chart(fig, width="stretch")
        guide(
            "A disaster is a stop above this dataset's own upper Tukey fence, "
            "recalculated from the data rather than fixed at a number of seconds. "
            "The team spread looks wide, but with this many stops it is still "
            "within what chance produces, so it is not evidence of one crew being "
            "reliably worse."
        )

    elif test_id == "T23":
        fig = group_bar(groups, "correlation", "method",
                        "Correlation with overtakes made", sort=False, height=220)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "Two ways of measuring the same relationship. They disagree even "
                "on the sign, and both sit near zero, which is the point: how "
                "long a driver spends within a second of the car ahead says "
                "almost nothing about how many passes they complete. Following "
                "closely is what happens both when you are attacking and when you "
                "are stuck."
            )

    elif test_id == "T24":
        fig = group_bar(groups, "correlation_with_radio", "outcome",
                        "Correlation with radio volume", height=260)
        if fig:
            st.plotly_chart(fig, width="stretch")
            guide(
                "Radio volume measured against each driver's own race, correlated "
                "with what happened to them. Bars right of zero mean more radio "
                "went with more of that outcome. The direction of cause almost "
                "certainly runs backwards: a car in contention or in trouble "
                "generates radio traffic, rather than radio traffic producing "
                "points."
            )

    # --- teammates ---
    elif test_id == "T02" and len(cf):
        show = cf.copy()
        show["magnitude"] = show.std_coefficient.abs()
        show = show.sort_values("magnitude")
        show["predictor"] = show.predictor.str.replace("_delta", "").str.title()
        fig = go.Figure(go.Bar(
            x=show.magnitude, y=show.predictor, orientation="h",
            marker_color=[INK if p < 0.05 else MUTED for p in show.p_value],
            customdata=np.stack([show.p_value.map(fmt_p)], axis=-1),
            hovertemplate="<b>%{y}</b><br>relative weight %{x:.2f}"
                          "<br>p = %{customdata[0]}<extra></extra>",
        ))
        st.plotly_chart(
            _bar_layout(fig, "Relative importance (standardised coefficient)",
                        None, 280), width="stretch")
        guide(
            "How much each factor contributes to the points gap between two "
            "drivers in the same car, on a common scale so the four can be "
            "ranked. Race pace dominates. Pale bars did not reach "
            "significance. The model explains 37% of the gap, so most of what "
            "separates teammates over a season is race-to-race variation these "
            "four factors do not capture."
        )


# --- page ---------------------------------------------------------------------

tests = query("SELECT * FROM diag_tests")
coefs = query("SELECT * FROM diag_coefficients")
groups = query("SELECT * FROM diag_groups")
points = query("SELECT * FROM diag_points")

st.title("Diagnose")
st.caption(
    "Why it happened. Every finding below is the output of a statistical test "
    "run over all "
    f"{int(tests.n.max()):,} observations available to it, not an impression "
    "formed from watching races."
)

c1, c2, c3 = st.columns(3)
c1.metric("Questions tested", len(tests))
c2.metric("Effects supported", int(tests.significant.sum()),
          f"{len(tests) - int(tests.significant.sum())} not supported")
c3.metric("Carrying a caveat", int(tests.caveat.notna().sum()),
          "read them, they matter")

st.info(
    "A supported result means the effect is unlikely to be chance. It does "
    "not mean the effect is large, and it does not establish cause. Where a "
    "test was underpowered or the design confounded, that is stated on the "
    "test itself rather than left for the reader to infer."
)

st.divider()


# --- section picker -----------------------------------------------------------
# ONE NOTEBOOK AT A TIME, and only the dropdown to switch. Analyse shows a whole
# story at once because a story is meant to be read straight through. A test
# bank is not: these are 29 independent questions in seven groups, and stacking
# them makes a page nobody finishes. The difference is deliberate.

section_titles = {key: title for key, title, *_ in SECTIONS}
section_keys = list(section_titles)

if st.session_state.get("diag_section") not in section_keys:
    st.session_state["diag_section"] = section_keys[0]

chosen = st.sidebar.selectbox(
    "Section", section_keys,
    format_func=lambda k: section_titles.get(k, k),
    key="diag_section",
)
st.sidebar.caption(
    "Each section is one notebook in DIAGNOSTIC ANALYTICS. Figures are "
    "recomputed by the pipeline, not copied from the notebooks."
)

# drop=False so each row keeps test_id as a value, not only as its index.
by_id = tests.set_index("test_id", drop=False)

key, title, test_ids, story, section_key, block = next(
    s for s in SECTIONS if s[0] == chosen)

st.header(title)
present = [t for t in test_ids if t in by_id.index]

# The section key is no longer a filename, so the notebooks are looked up from
# the tests themselves. A group can draw on more than one.
books = sorted({NOTEBOOK_OF[t] for t in present if t in NOTEBOOK_OF})
st.caption(
    f"{len(present)} question{'s' if len(present) != 1 else ''}, worked out in "
    + ", ".join(f"`{b}.ipynb`" for b in books) + "."
)
st.divider()

for i, tid in enumerate(present):
    test = by_id.loc[tid]
    label, meaning = verdict(test)

    st.subheader(test.question)
    st.markdown(f"{label} &nbsp; {meaning}")
    st.markdown(f"**{test.conclusion}**")

    if pd.notna(test.caveat):
        st.warning(f"**Caveat.** {test.caveat}")

    chart(tid, tests, coefs, groups, points)
    _stats(test, coefs)

    if i < len(present) - 1:
        st.divider()

# The teammate-qualifying question belongs to two groups. It is answered once,
# under the grid, rather than repeating the same test on two pages.
if chosen == "teammate":
    st.divider()
    st.caption(
        "Whether a driver's advantage over their teammate is real rather than "
        "race-to-race noise is answered by the qualifying-delta test, shown "
        "under Pre-race, grid & setup. Their strategies diverging is under "
        "Tyre strategy."
    )

st.divider()
st.caption("These questions build on the descriptive layer:")
_link(story, section_key, block, key=f"link_{key}")

render_footer()
