"""
perfect.py - "Find perfect lap".

One page, read by scrolling, in the same three movements as the rest of the
project:

    1. The state of the race line    what was happening, at one instant
    2. Why the lap was what it was   which factors moved it, and by how much
    3. What could have been better   the same factors, moved on purpose

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
import lap_counterfactual as cf  # noqa: E402
import lap_factors as lf  # noqa: E402
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

table = lf.anova()
resid = table[table.is_residual == 1]
unexplained_pct = float(resid.pct_variance.iloc[0]) if len(resid) else float("nan")
r2 = float(table.model_r_squared.iloc[0]) if len(table) else float("nan")
model_n = int(table.model_n.iloc[0]) if len(table) else 0

# The headline is the size of the gap, not the factors that fill it. Leading
# with a tidy waterfall would imply lap time is a solved sum; it is not.
st.error(
    f"**About {unexplained_pct:.0f}% of it cannot be explained.** Across "
    f"{model_n:,} clean race laps, everything recorded here (fuel, tyres, "
    f"compound, weather) accounts for only {100 * r2:.0f}% of why one lap "
    "differs from another in the same race. The rest is the driver, the car, "
    "traffic and the racing line, and none of those are in this model."
)

if focus is None:
    st.info("Choose a driver above to break down one specific lap.")
else:
    one = laps[(laps.driver_number == focus) & (laps.lap_number == lap_number)]
    if not len(one):
        st.warning(f"{names[focus]} has no recorded lap {lap_number}.")
    else:
        lap = one.iloc[0]
        refs = lf.reference(int(session_key))
        stop = lf.blocked(lap, refs)

        if stop:
            st.warning(f"**This lap cannot be broken down.** {stop}")
        else:
            gaps = lf.missing_factors(lap)
            coefs = lf.coefficients()
            teams = sorted(laps.team_name.dropna().unique())
            parts = lf.decompose(lap, coefs, refs, teams)
            totals = lf.summarise(lap, parts, coefs, refs, teams)

            if gaps:
                st.info(
                    "**Not recorded for this lap: "
                    + ", ".join(g.lower() for g in gaps) + ".** "
                    + ("Those factors get no bar below. This lap is priced as "
                       "if it sat at the typical value for this race, so "
                       "whatever they were really worth is inside the "
                       "\"not measured\" bar rather than assigned to something "
                       "else. Everything else is unaffected.")
                )

            m1, m2, m3 = st.columns(3)
            m1.metric("This lap against the race median",
                      f"{totals['actual']:+.3f}s")
            m2.metric("Explained by measurable factors",
                      f"{totals['explained']:+.3f}s")
            m3.metric("Unexplained", f"{totals['unexplained']:+.3f}s")

            st.plotly_chart(
                lf.waterfall(parts, totals), width="stretch",
                key=f"wf_{session_key}_{lap_number}_{focus}")

            guide(
                "Reading left to right, this starts at what the model expects "
                "from a normal lap of this race, then adds or removes seconds "
                "for each way this lap differed from that. Red bars cost time, "
                "green bars gained it. The second-to-last bar is everything "
                "the data cannot account for, and it is usually the biggest "
                "one. The final bar is the lap as it was actually driven."
            )

            with st.expander("The numbers behind each bar"):
                show = parts.copy()
                show["This lap"] = [
                    f"{v}" if isinstance(v, str) else f"{v:g} {u}".strip()
                    for v, u in zip(show.value, show.unit)]
                show["Typical here"] = [
                    f"{v}" if isinstance(v, str) else
                    ("-" if pd.isna(v) else f"{v:g} {u}".strip())
                    for v, u in zip(show.reference, show.unit)]
                st.dataframe(
                    show[["factor", "This lap", "Typical here", "seconds"]]
                    .rename(columns={"factor": "Factor",
                                     "seconds": "Effect (s)"}),
                    hide_index=True, width="stretch",
                    column_config={"Effect (s)":
                                   st.column_config.NumberColumn(format="%+.4f")})

st.subheader("Which factors matter at all")

st.plotly_chart(lf.variance_bar(table), width="stretch", key="variance_bar")

guide(
    "This is not about one lap. It is a Type II ANOVA over every clean race "
    "lap in four seasons, showing how much of the variation in lap time each "
    "factor accounts for once all the others are taken into account. Read the "
    "share of variance, not the p-value: at this sample size every factor is "
    "statistically significant, including ones that explain almost nothing."
)

with st.expander("Full test results"):
    st.dataframe(
        table[["factor", "pct_variance", "f_statistic", "p_value", "df"]]
        .rename(columns={"factor": "Factor", "pct_variance": "% of variance",
                         "f_statistic": "F", "p_value": "p", "df": "df"}),
        hide_index=True, width="stretch",
        column_config={
            "% of variance": st.column_config.NumberColumn(format="%.3f"),
            "F": st.column_config.NumberColumn(format="%.1f"),
            "p": st.column_config.NumberColumn(format="%.2e"),
        })
    st.caption(
        f"Ordinary least squares, n = {model_n:,}, R2 = {r2:.3f}. Type II "
        "sums of squares, so each factor is measured after the others rather "
        "than in formula order, which matters because track and air "
        "temperature move together. Read the share of variance, not the "
        "p-value: several factors here are significant beyond any doubt while "
        "explaining less than a twentieth of one percent."
    )

with st.expander("What the two traffic rows do and do not mean"):
    st.markdown(
        "**Out of position** marks a lap where the timing feed reported the "
        "gap to the car ahead in laps rather than seconds. That happens when a "
        "car is caught by the leaders, and also when it is damaged, off the "
        "track, or limping to the pits. Those laps average +10.1s, and the "
        "three laps before the flag average +2.1s, +2.3s and +3.0s against a "
        "field baseline near +0.5s. So a car is already slow before the flag "
        "lands: part of this factor is a consequence of a bad lap rather than "
        "a cause of one. It stays in the model because leaving it out does not "
        "make the variance disappear, it moves it into the unexplained share, "
        "where this page would call it driving."
        "\n\n"
        "**Traffic ahead** and **dirty air** are close to nothing here, and "
        "that is a correction rather than an absence. Before out-of-position "
        "laps were flagged separately, their gap was filled in at the 10s cap, "
        "the model saw \"large gap, slow lap\", and read a dirty-air effect out "
        "of it. On properly timed laps the effect is 0.009s per second at "
        "p = 0.13. A driver held up behind another car matches that car's pace; "
        "it does not make their lap slower than it."
    )

# --- DRS and the tow --------------------------------------------------------------
# Its own subsection because its scope is different from everything above it.
# The panel leads with the coverage, not the finding, so a reader cannot take
# a six-race number for a four-season one.
tow = lf.tow()
tel = lf.telemetry()

if len(tow) and len(tel):
    st.subheader("DRS and the tow")

    st.warning(lf.telemetry_note(tel, "races covered"))
    st.caption(lf.telemetry_note(tel, "laps covered"))

    st.plotly_chart(lf.tow_chart(tow), width="stretch", key="tow_chart")

    close = tow[tow.bucket == "under 1s"]
    gain = float(close.top_speed_vs_clear_air.iloc[0]) if len(close) else None
    lead = ("This is the tow, measured rather than assumed. A car starting its "
            "lap within one second of the car ahead reaches "
            f"{abs(gain):.1f} km/h more top speed than one in clear air, which "
            "is the slipstream and DRS together."
            if gain is not None else
            "This is the tow, measured rather than assumed.")
    guide(
        lead + " Top speed is on the vertical axis, not lap time, because a "
        "tow shows up as speed at the end of a straight long before it shows "
        "up in a lap that is also carrying traffic, tyres and fuel."
    )

    drs = tel[(tel.kind == "coefficient") & (tel.term == "drs_share")]
    if len(drs):
        coef = float(drs.coefficient.iloc[0])
        st.metric("Ten percentage points more of the lap with DRS open",
                  f"{coef * 0.1:+.3f}s")

    for term in ("drs and dirty air correlation", "undocumented drs codes",
                 "ers", "top speed"):
        note = lf.telemetry_note(tel, term)
        if note:
            st.caption(note)

st.divider()

st.header("3. What could have been better")

if focus is None:
    st.info("Choose a driver above to run a counterfactual on one lap.")
else:
    one3 = laps[(laps.driver_number == focus) & (laps.lap_number == lap_number)]
    if not len(one3):
        st.warning(f"{names[focus]} has no recorded lap {lap_number}.")
    elif pd.isna(one3.iloc[0].lap_duration):
        st.warning("This lap has no recorded duration, so there is nothing to "
                   "improve on.")
    else:
        lap3 = one3.iloc[0]
        cmodel = cf.model()
        cbounds = cf.bounds(int(session_key))
        circuit = cover[cover.session_key == session_key]
        circuit_name = (circuit.circuit_short_name.iloc[0]
                        if len(circuit) else None)

        before = {t: getattr(lap3, t, None)
                  for t, _l, _k, _u in cf.CHOICE_LEVERS + cf.CONDITION_LEVERS}
        derived = lf.derive(lap3)
        for t in ("gap_ahead", "in_dirty_air", "out_of_position",
                  "being_lapped", "yellow_sector"):
            before[t] = derived.get(t, before.get(t))
        before["lap_number"] = lap3.lap_number

        widen = st.toggle(
            "Allow values from any race, not just this one",
            value=False, key="cf_widen",
            help="Opens every slider to the full range recorded across four "
                 "seasons, so you can build a combination no driver has run. "
                 "Values outside that range are still refused: the model has "
                 "no evidence there.")

        st.markdown("#### What could have been done differently")
        st.caption(
            "Tyre and traffic. These are estimated against the other cars on "
            "the same lap of the same race, so fuel, track state and weather "
            "are shared by everyone in the comparison and cancel out. That is "
            "what lets these read as choices."
        )

        ideal = cf.best_case(cmodel, before, cbounds, widen)
        if st.button("Set the best realistic case", key="cf_best"):
            for t, _l, _k, _u in cf.CHOICE_LEVERS:
                st.session_state[f"cf_{t}"] = (
                    ideal[t] if t == "compound" else
                    bool(ideal[t]) if _k == "flag" else
                    (int(ideal[t]) if _k == "int" else float(ideal[t])))

        after = dict(before)
        for group, levers in (("choice", cf.CHOICE_LEVERS),
                              ("condition", cf.CONDITION_LEVERS)):
            if group == "condition":
                st.markdown("#### What the day did to it")
                st.caption(
                    "Weather and fuel. Nobody chose any of these, and they "
                    "cannot be estimated the same way: every car on a lap "
                    "shares them, so they vanish from the same-lap comparison "
                    "above and can only be read from the pooled model. Move "
                    "them to ask what a different afternoon would have given, "
                    "not what the team should have done."
                )
            cols = st.columns(3)
            for i, (term, label, kind, unit) in enumerate(levers):
                col = cols[i % 3]
                current = before.get(term)
                if kind == "choice":
                    opts = cf.COMPOUNDS
                    idx = (opts.index(current)
                           if isinstance(current, str) and current in opts
                           else 0)
                    after[term] = col.selectbox(label, opts, index=idx,
                                                key=f"cf_{term}")
                elif kind == "flag":
                    after[term] = float(col.checkbox(
                        f"{label}", value=bool(current and float(current) >= 0.5),
                        key=f"cf_{term}", help=unit))
                else:
                    lim = cf.limits(cbounds, term, widen)
                    if lim is None or pd.isna(current):
                        col.caption(f"{label}: not recorded")
                        after[term] = current
                        continue
                    lo, hi = lim
                    value = float(min(max(float(current), lo), hi))
                    if kind == "int":
                        after[term] = float(col.slider(
                            f"{label} ({unit})", int(lo), int(hi), int(value),
                            key=f"cf_{term}"))
                    else:
                        after[term] = float(col.slider(
                            f"{label} ({unit})", float(lo), float(hi), value,
                            key=f"cf_{term}"))

        parts = cf.evaluate(cmodel, before, after, circuit_name)
        moved = float(parts.seconds.sum())
        actual = float(lap3.lap_duration)

        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("The lap as driven", f"{actual:.3f}s")
        c2.metric("What the changes are worth", f"{moved:+.3f}s")
        c3.metric("The lap it would have been", f"{actual + moved:.3f}s")

        chart = cf.delta_chart(parts)
        if chart is not None:
            st.plotly_chart(chart, width="stretch",
                            key=f"cf_{session_key}_{lap_number}_{focus}")
        else:
            st.info("Nothing has been changed yet. Move a control above, or "
                    "press the button, to see what it would have been worth.")

        for group, title in (("choice", "Choices, before and after"),
                             ("condition", "Conditions, before and after")):
            st.markdown(f"**{title}**")
            st.dataframe(
                cf.before_after(parts, group), hide_index=True,
                width="stretch",
                column_config={
                    "Effect (s)": st.column_config.NumberColumn(
                        format="%+.4f"),
                    "Changed": st.column_config.CheckboxColumn(),
                })

        blocked3 = lf.blocked(lap3, lf.reference(int(session_key)))
        resid_note = ""
        if not blocked3:
            coefs3 = lf.coefficients()
            teams3 = sorted(laps.team_name.dropna().unique())
            t3 = lf.summarise(lap3, lf.decompose(lap3, coefs3,
                                                 lf.reference(int(session_key)),
                                                 teams3),
                              coefs3, lf.reference(int(session_key)), teams3)
            resid_note = (
                f" On this lap that unexplained part is "
                f"{t3['unexplained']:+.3f}s, which is "
                f"{abs(t3['unexplained'] / moved):.0f} times the "
                f"{abs(moved):.3f}s above."
                if abs(moved) > 1e-6 else
                f" On this lap that unexplained part is "
                f"{t3['unexplained']:+.3f}s.")

        st.warning(
            "**This is the real lap with the changes added to it, not a lap "
            "rebuilt from scratch.** Everything the model cannot explain, "
            "which is 76% of why laps differ, travels with the lap unchanged."
            + resid_note
        )

render_footer()
