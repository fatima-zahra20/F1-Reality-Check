"""
race_map.py - the track map and the single-car panel for "Find perfect lap".

Everything here answers one question: where was every car, and what was
happening to it, at a chosen moment of a chosen race.

Two honesty rules run through the whole module.

First, what a race supports is looked up, never assumed. map_coverage carries
one row per race saying whether it has a track outline, recorded positions and
car telemetry, and the wording shown to the reader comes from there. This
matters because the module previously hardcoded the claim that no race had
telemetry, which was an ingestion bug being reported as a fact about Formula
One. Channels that are missing are still listed, labelled: an absent row reads
as a bug, a labelled gap reads as a fact.

Second, car positions on the map are DERIVED unless the coverage row says
otherwise. For most races a car's place on the outline is computed from lap
timing. That is right for running order and spacing and wrong for the racing
line, and the page says so where the map is, not in a footnote.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app_common import NEUTRAL, fmt_gap, fmt_lap, query, team_colours
from theme import ACCENT, ink

SECTOR_CHOICES = ["All of the lap", "Sector 1", "Sector 2", "Sector 3"]

# The unhighlighted outline. A mid grey on purpose, so it reads as background
# against either page colour and needs no theme of its own.
TRACK_LINE = "#C9C9D1"


def track_highlight() -> str:
    """
    The darkened sector, the start/finish marker and the car labels.

    A function rather than a constant because it follows the page: near-black
    on light, near-white on dark. As a module-level string it would be fixed at
    import and the highlighted sector would disappear into a dark background.
    """
    return ink()


NOT_RECORDED = "not recorded"

# Deliberately distinct from NOT_RECORDED. The telemetry exists upstream; it
# simply has not been pulled into this project's bronze layer yet. Saying
# "not recorded" claimed something false about the sport.
NOT_RETRIEVED = "not retrieved"

# Used when a chosen driver cannot anchor the moment, because they have no
# timed lap at that number. The page checks for it to explain itself.
ANCHOR_FALLBACK = "the leader starting the lap"


# --- data ------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def coverage() -> pd.DataFrame:
    return query("SELECT * FROM map_coverage ORDER BY year, session_key")


@st.cache_data(show_spinner=False)
def outline(circuit_key: int) -> pd.DataFrame:
    df = query("""
        SELECT seq, x, y, z, path_fraction, time_fraction,
               source_session, source_lap_duration
        FROM map_circuit_outline WHERE circuit_key = ? ORDER BY seq
    """, (int(circuit_key),))
    # Yas Marina's z is NULL throughout, which SQLite returns as None and
    # pandas types as object. Coerced here so every consumer sees floats.
    df["z"] = pd.to_numeric(df["z"], errors="coerce")
    return df


@st.cache_data(show_spinner=False)
def measured_positions(session_key: int) -> pd.DataFrame:
    """Recorded x/y/z for the races that have it. Empty for the rest."""
    df = query("""
        SELECT driver_number, date, lap_number, x, y, z
        FROM map_measured_xy WHERE session_key = ? ORDER BY date
    """, (int(session_key),))
    if len(df):
        df["date"] = pd.to_datetime(df["date"], format="ISO8601", utc=True)
    return df


# Recorded samples are thinned to roughly one every 2.4s, so anything inside
# this window is the same instant for drawing purposes. Beyond it the car has
# moved far enough that the derived position is the better answer.
MEASURED_TOLERANCE_S = 3.0


def apply_measured(cars: pd.DataFrame, meas: pd.DataFrame,
                   moment: pd.Timestamp) -> tuple[pd.DataFrame, int]:
    """
    Replace derived dots with recorded ones wherever a real sample exists.

    Per driver rather than per race, because coverage is ragged: Monaco's
    position feed stops on lap 5 while its car telemetry runs the whole race,
    and a retired car simply stops producing samples. Drivers with no sample
    near this instant keep their derived position, so the map never loses a
    car in exchange for being more accurate about the others.

    Returns (cars, how many dots are now recorded).
    """
    if meas.empty or cars.empty:
        return cars, 0

    near = meas.assign(
        dt=(meas.date - moment).abs().dt.total_seconds())
    near = near.sort_values("dt").groupby("driver_number", as_index=False).head(1)
    near = near[near.dt <= MEASURED_TOLERANCE_S]
    if near.empty:
        return cars, 0

    cars = cars.copy()
    cars["measured"] = False
    lookup = near.set_index("driver_number")
    hit = cars.driver_number.isin(lookup.index)
    for axis in ("x", "y", "z"):
        cars.loc[hit, f"map_{axis}"] = (
            cars.loc[hit, "driver_number"].map(lookup[axis]).astype(float))
    cars.loc[hit, "measured"] = True
    cars.loc[hit, "on_track"] = True
    return cars, int(hit.sum())


def has_elevation(path: pd.DataFrame) -> bool:
    """Yas Marina's z channel is entirely null; every other circuit has one."""
    return path.z.notna().any() and float(path.z.max() - path.z.min()) > 0.5


@st.cache_data(show_spinner=False)
def race_laps(session_key: int) -> pd.DataFrame:
    """Every lap of one race, with the driver and team resolved."""
    df = query("""
        SELECT l.*, d.full_name, d.name_acronym, r.team_name
        FROM fact_lap l
        JOIN dim_race dr ON dr.session_key = l.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = l.driver_number AND d.year = dr.year
        LEFT JOIN fact_driver_race r
               ON r.session_key = l.session_key
              AND r.driver_number = l.driver_number
        WHERE l.session_key = ?
        ORDER BY l.driver_number, l.lap_number
    """, (int(session_key),))
    df["date_start"] = pd.to_datetime(df["date_start"], format="ISO8601",
                                      utc=True, errors="coerce")
    df["driver"] = df.full_name.fillna(
        df.name_acronym.fillna("Car " + df.driver_number.astype(str)))
    return df


# --- placement -------------------------------------------------------------------

def leader_moment(laps: pd.DataFrame, lap_number: int) -> pd.Timestamp | None:
    """
    The instant the leader starts the chosen lap.

    Taken as the earliest lap start among the cars on that lap rather than by
    looking up position 1, because position is sampled separately and can lag
    a swap by a few seconds. The first car across the line is the leader by
    definition.
    """
    on_lap = laps[(laps.lap_number == lap_number) & laps.date_start.notna()]
    return on_lap.date_start.min() if len(on_lap) else None


def sector_band(sector: int, bounds: tuple[float, float]) -> tuple[float, float]:
    """The stretch of lap, as time fraction, that one sector covers."""
    f1, f2 = bounds
    return {1: (0.0, f1), 2: (f1, f2), 3: (f2, 1.0)}[sector]


def moment_for(laps: pd.DataFrame, lap_number: int, driver_number: int | None,
               sector: int | None,
               bounds: tuple[float, float] | None = None
               ) -> tuple[pd.Timestamp | None, str]:
    """
    The instant the map describes, and a plain description of it.

    With no driver chosen there is nothing to centre on, so the map uses the
    moment the leader starts the lap.

    Once a driver is chosen the page is about that car, and the moment becomes
    the middle of their time in the chosen sector. Anchoring on the leader
    instead put the chosen driver wherever they happened to be, which was
    usually not the sector being highlighted: on lap 13 at Melbourne 2023,
    Norris sat 88.8% around the lap, deep in sector 3, while sector 2 was
    darkened. Choosing a sector has to move the car into it or the control
    means nothing.

    The midpoint is used rather than the sector entry so the dot sits clear of
    both boundaries. Checked across 3,600 driver-laps, a sector midpoint lands
    in the intended sector 3,599 times.
    """
    fallback = leader_moment(laps, lap_number), ANCHOR_FALLBACK
    if driver_number is None:
        return fallback

    row = laps[(laps.driver_number == driver_number)
               & (laps.lap_number == lap_number)]
    if not len(row) or pd.isna(row.iloc[0].date_start):
        return fallback

    r = row.iloc[0]
    s1, s2, s3 = (r.duration_sector_1, r.duration_sector_2, r.duration_sector_3)
    offset, where = None, None

    if sector == 1 and pd.notna(s1):
        offset, where = s1 / 2, "halfway through sector 1"
    elif sector == 2 and pd.notna(s1) and pd.notna(s2):
        offset, where = s1 + s2 / 2, "halfway through sector 2"
    elif sector == 3 and pd.notna(s1) and pd.notna(s2) and pd.notna(s3):
        offset, where = s1 + s2 + s3 / 2, "halfway through sector 3"
    elif pd.notna(r.lap_duration):
        offset, where = r.lap_duration / 2, "halfway around the lap"

    # Lap 1 has no sector times and no duration in any race, so there is
    # nothing to offset from; the lap's own start is the honest answer.
    if offset is None:
        return r.date_start, "at the start of the lap"

    # The darkened band is the FIELD's median sector boundary, a fixed place
    # on the track. This driver's own split can sit either side of it: a lap
    # with a slow sector 1, from traffic or a lock-up, pushes their sector-2
    # midpoint past the median boundary and the dot lands just outside the
    # band it is supposed to be in. That happened on 2.1% of driver-laps.
    # Where their own timing disagrees with the band, the band wins, because
    # a dot outside the highlighted stretch reads as a bug no matter how
    # defensible the arithmetic behind it.
    if bounds is not None and sector and pd.notna(r.lap_duration) \
            and r.lap_duration > 0:
        lo, hi = sector_band(sector, bounds)
        if not lo <= offset / r.lap_duration <= hi:
            offset = (lo + hi) / 2 * r.lap_duration

    return r.date_start + pd.to_timedelta(float(offset), unit="s"), where


def cars_at(laps: pd.DataFrame, moment: pd.Timestamp) -> pd.DataFrame:
    """
    Where every car is at one instant, as a fraction through its own lap.

    A driver's current lap is the last one they started at or before the
    moment, and normally the fraction is simply how much of that lap's
    duration has elapsed.

    Lap 1 breaks that, and breaks it for every race. It has no recorded
    duration for any driver, and every car shares one lap-1 start timestamp,
    because the lap begins when the race does rather than when each car
    crosses the line. Elapsed-over-duration is therefore undefined for the
    whole field, which left lap 1 with no cars at all and lap 2 with one.

    So when a lap has no duration the fraction is measured from the other end:
    how soon the car will cross the line, against a normal lap for that race.
    A car twenty seconds from the line on a ninety-second circuit is most of
    the way round. Capped at one full lap, which parks the whole field on the
    start/finish line at the instant lap 1 begins. That is where they are: on
    the grid. The cap matters because lap 1's window also swallows the
    formation lap, so the raw remaining time is closer to two laps than one.

    Cars with more elapsed than their lap took never finished it: retired,
    red-flagged, or sitting in the pit lane. They come back on_track False
    rather than placed somewhere invented.
    """
    valid = laps[laps.date_start.notna()].sort_values(
        ["driver_number", "lap_number"]).copy()
    if valid.empty:
        return pd.DataFrame()
    valid["next_start"] = valid.groupby("driver_number").date_start.shift(-1)

    started = valid[valid.date_start <= moment]
    if started.empty:
        return pd.DataFrame()

    cur = (started.sort_values("date_start")
                  .groupby("driver_number", as_index=False).tail(1).copy())
    cur["elapsed"] = (moment - cur.date_start).dt.total_seconds()

    # SQLite hands NULL back as None, which makes these columns object dtype
    # and turns any comparison into a TypeError rather than a False.
    duration = pd.to_numeric(cur.lap_duration, errors="coerce")
    reference = pd.to_numeric(cur.session_median_lap, errors="coerce")

    timed = duration.notna() & (cur.elapsed <= duration)
    by_elapsed = cur.elapsed / duration

    remaining = (cur.next_start - moment).dt.total_seconds()
    untimed = (duration.isna() & cur.next_start.notna()
               & reference.notna() & (reference > 0) & (remaining >= 0))
    by_remaining = 1.0 - np.minimum(remaining, reference) / reference

    cur["on_track"] = timed | untimed
    cur["time_fraction"] = np.where(
        timed, by_elapsed, np.where(untimed, by_remaining, np.nan))
    cur["time_fraction"] = cur.time_fraction.clip(0, 1)
    return cur


def place_on_outline(cars: pd.DataFrame, path: pd.DataFrame) -> pd.DataFrame:
    """
    Turn a fraction through the lap into a point on the track.

    Interpolated against the outline's time_fraction, never its distance, so a
    car crawling through a hairpin is drawn in the hairpin. See the note in
    s05c_racemap.resample_path.
    """
    cars = cars.copy()
    ok = cars.time_fraction.notna()
    tf = path.time_fraction.to_numpy(float)
    for axis in ("x", "y", "z"):
        cars[f"map_{axis}"] = np.nan
        values = path[axis].to_numpy(float)
        if np.isnan(values).all():
            continue
        cars.loc[ok, f"map_{axis}"] = np.interp(
            cars.loc[ok, "time_fraction"], tf, values)
    return cars


def sector_bounds(laps: pd.DataFrame) -> tuple[float, float] | None:
    """
    Where the two sector lines fall, as a fraction of lap time.

    Median across every timed lap of the race, so one driver's scruffy lap
    cannot move a boundary. Returns None when sector times are too sparse to
    locate them.
    """
    ok = laps[laps.lap_duration.notna()
              & laps.duration_sector_1.notna()
              & laps.duration_sector_2.notna()]
    if len(ok) < 20:
        return None
    f1 = (ok.duration_sector_1 / ok.lap_duration).median()
    f2 = ((ok.duration_sector_1 + ok.duration_sector_2) / ok.lap_duration).median()
    return float(f1), float(f2)


def sector_of(fraction: float, bounds: tuple[float, float] | None) -> int | None:
    if bounds is None or pd.isna(fraction):
        return None
    f1, f2 = bounds
    return 1 if fraction < f1 else (2 if fraction < f2 else 3)


# --- drawing ---------------------------------------------------------------------

CAR_HOVER = ("<b>%{customdata[0]}</b><br>%{customdata[1]}"
             "<br>P%{customdata[2]}<br>%{customdata[3]}<extra></extra>")

# The elevation axis is drawn at true scale: one metre of climb is the same
# length on screen as one metre of track. Nothing is stretched.
#
# This makes most circuits look nearly flat, and that is the honest picture.
# Spa's famous 102 m of climb is spread over 7 km, so as a gradient it is a
# gentle ridge, not a mountain; Monza's 12 m across 2.2 km is invisible, and
# Monza is in fact flat. An exaggerated axis would make every circuit look
# dramatic and would say nothing true about which ones actually are.
#
# A floor keeps a circuit with no measurable relief from collapsing the scene
# to zero height, which plotly rejects.
MIN_Z_RATIO = 0.001

# Viewing angle above the track, held fixed. Letting it move is what makes a
# 3D plot lurch: plotly's default orbit drag tumbles the scene freely, so a
# small horizontal movement can also flip the circuit on its side. Pinning the
# tilt means the only thing a drag can do is spin the track left or right.
CAMERA_TILT_DEG = 25.0

CAMERA_AZIMUTH_DEFAULT = 315.0
CAMERA_DISTANCE_DEFAULT = 2.1
CAMERA_DISTANCE_MIN = 1.4
CAMERA_DISTANCE_MAX = 3.4


# --- orientation ------------------------------------------------------------------
#
# THERE IS NO NORTH HERE, AND THAT IS NOT AN OVERSIGHT. Position coordinates
# arrive in each circuit's own frame and the rotation to compass north is not
# recorded anywhere: `location` carries x, y and z only, and no table in the
# project holds a latitude or a longitude. s05b_perfect.add_wind_components
# reaches the same conclusion from the other direction, which is why wind enters
# the model as two components crossed with circuit rather than as a bearing.
#
# So a needle labelled N would be a guess wearing the clothes of a measurement,
# wrong by an unknown amount per circuit, with nothing on screen to warn a
# reader. The two instruments below answer the questions a reader actually has,
# and both are exact:
#
#   which way is the lap run   -> the path is stored in lap order
#   which way am I looking     -> the camera bearing is an input we set
#
# Both are stated in the circuit's own frame and neither claims a cardinal
# direction.

ARROW_COUNT = 8

# The inset sits in the top-right corner of the 3D scene, in paper coordinates.
DIAL_DOMAIN = [0.80, 0.99]

# Radii inside the dial, as fractions of its half-width. The outline is scaled
# so its furthest point lands on DIAL_TRACK_R, then the viewer dot and the
# cardinal letters sit at fixed distances clear of it. Keeping all three here
# means the spacing can be tuned without hunting through the drawing code.
DIAL_TRACK_R = 0.46      # outer edge of the circuit outline
DIAL_EYE_R = 0.56        # the red "you are here" marker
DIAL_LABEL_R = 0.64      # N, E, S, W
DIAL_LIMIT = 0.74        # axis range, leaving room for the letters


def _heading_points(path: pd.DataFrame, n: int) -> pd.DataFrame:
    """
    n points spaced evenly along the stored path, each with its local heading.

    CENTRED DIFFERENCE, not the step to the next point. Measuring from here to
    somewhere ahead gives the direction of a CHORD, which always leans toward
    the outside of a turn by half the arc it spans. Checked on a circle whose
    answer is known: the forward difference put every arrow 11 degrees off,
    consistently, which on screen reads as arrows that do not quite follow the
    line they sit on. Taking the point before and the point after cancels it.

    The outline is already resampled to even spacing, so a fixed number of
    points either side is a fixed distance either side.
    """
    if len(path) < 3:
        return pd.DataFrame()

    idx = np.linspace(0, len(path) - 1, n, endpoint=False).astype(int)
    step = max(1, len(path) // (n * 4))
    before = (idx - step) % len(path)
    after = (idx + step) % len(path)

    here, back, ahead = path.iloc[idx], path.iloc[before], path.iloc[after]
    out = here[["x", "y"]].copy().reset_index(drop=True)
    out["z"] = here["z"].to_numpy() if "z" in here else 0.0
    out["dx"] = ahead.x.to_numpy() - back.x.to_numpy()
    out["dy"] = ahead.y.to_numpy() - back.y.to_numpy()
    out["dz"] = ((ahead.z.to_numpy() - back.z.to_numpy())
                 if "z" in here else np.zeros(len(out)))

    # Plotly measures marker.angle clockwise from straight up; atan2 measures
    # counter-clockwise from the positive x axis. Hence 90 minus, not plus.
    out["angle"] = 90.0 - np.degrees(np.arctan2(out.dy, out.dx))
    return out


def travel_arrows_2d(path: pd.DataFrame, colour: str,
                     n: int = ARROW_COUNT) -> go.Scatter | None:
    """Arrowheads along the flat outline, pointing the way the lap is run."""
    pts = _heading_points(path, n)
    if pts.empty:
        return None
    return go.Scatter(
        x=pts.x, y=pts.y, mode="markers", name="Direction of travel",
        marker=dict(symbol="arrow", size=13, angle=pts.angle,
                    color=colour, line=dict(width=0)),
        hovertemplate="The lap runs this way<extra></extra>",
        showlegend=False,
    )


def travel_arrows_3d(path: pd.DataFrame, colour: str,
                     n: int = ARROW_COUNT) -> go.Cone | None:
    """
    The same arrows in the 3D scene.

    Cones rather than markers because Scatter3d cannot rotate a symbol, so a
    marker would point the same way whatever the car is doing.
    """
    pts = _heading_points(path, n)
    if pts.empty:
        return None
    span = float(max(path.x.max() - path.x.min(), path.y.max() - path.y.min()))
    return go.Cone(
        x=pts.x, y=pts.y, z=pts.z,
        u=pts.dx, v=pts.dy, w=pts.dz,
        sizemode="absolute", sizeref=span * 0.05, anchor="tail",
        showscale=False, colorscale=[[0, colour], [1, colour]],
        hovertemplate="The lap runs this way<extra></extra>",
    )


# WHICH WAY THE ROTATION TURNS. The MultiViewer API gives a `rotation` per
# circuit but never states its convention: it is either the angle the map must
# be turned to put north up, or the bearing north already sits at. Both are
# internally consistent, so the data alone cannot choose between them.
#
# Settled by comparing each circuit's LONG AXIS, which is unmistakable on a
# satellite view, against what each convention predicts:
#
#   Las Vegas   runs along the Strip, a north-south boulevard.
#               "bearing" gives N-S. "rotate_map" gives W-E, which is wrong.
#   Monza       main straight runs roughly north-south.
#               "bearing" gives NNE-SSW. "rotate_map" gives WNW-ESE.
#
# Two independent circuits, both agreeing, with the two answers 90 degrees apart
# rather than marginally different.
#
# Monaco looks like the obvious test and is useless for it: there the two
# conventions give the SAME line, E-W, differing only in which end is north. An
# elevation test was tried first and settled nothing either, because it rested
# on a guess about which way Casino Square sits from the harbour.
#
# One constant on purpose. If a circuit ever looks wrong on a map, flip this
# word and all 24 follow.
NORTH_CONVENTION = "bearing"          # or "rotate_map"


def north_angle(rotation: float | None) -> float | None:
    """
    Where north points, in the circuit's own frame, as degrees anticlockwise
    from the positive x axis. None when the circuit has no recorded rotation.
    """
    if rotation is None or pd.isna(rotation):
        return None
    r = float(rotation)
    # "rotate_map": turning the map by r puts north up, so north currently sits
    # r degrees clockwise of straight up. "bearing": r already is that heading.
    return (90.0 - r) % 360.0 if NORTH_CONVENTION == "rotate_map" else r % 360.0


def view_dial(path: pd.DataFrame, azimuth: float, colour: str,
              rotation: float | None = None) -> list:
    """
    A small circuit-shaped dial showing where the camera is looking from.

    Not a compass. It carries no cardinal labels, because none can be honestly
    assigned. What it does say is exact: this is the circuit seen from directly
    above, and the marker is the corner of it you are currently viewing from.
    That is what a reader loses after spinning the scene, and it is the only
    thing they lose.

    Drawn on ordinary 2D axes overlaid on the 3D scene, so it stays put while
    the circuit turns underneath it.
    """
    cx, cy = float(path.x.mean()), float(path.y.mean())
    dx, dy = path.x - cx, path.y - cy

    # Scaled by the FURTHEST point from the centre, not by the bounding box or
    # by an average. That fixes the outline's outer edge at a known radius for
    # every circuit, so the ring of labels sits the same distance clear of the
    # track whether it is a long thin one like Monza or a compact one like
    # Monaco. Scaling by a mean let a stretched circuit reach past its own
    # labels while a compact one left them floating far out.
    reach_max = float(np.hypot(dx, dy).max()) or 1.0
    ux, uy = dx / reach_max * DIAL_TRACK_R, dy / reach_max * DIAL_TRACK_R

    angle = np.radians(azimuth)
    eye_x, eye_y = DIAL_EYE_R * np.cos(angle), DIAL_EYE_R * np.sin(angle)

    traces = [
        go.Scatter(
            x=ux, y=uy, mode="lines", xaxis="x2", yaxis="y2",
            line=dict(color=colour, width=1.5), opacity=0.55,
            hoverinfo="skip", showlegend=False,
        ),
    ]

    # Cardinal points, when the circuit's rotation is known. Drawn from north:
    # the other three are exactly 90 degrees apart from it, so only one of the
    # four is ever a measurement and the rest are arithmetic. Compass order runs
    # clockwise while these axes run anticlockwise, hence the minus.
    theta = north_angle(rotation)
    if theta is not None:
        for label, offset in (("N", 0), ("E", -90), ("S", -180), ("W", -270)):
            a = np.radians(theta + offset)
            traces.append(go.Scatter(
                x=[DIAL_LABEL_R * np.cos(a)], y=[DIAL_LABEL_R * np.sin(a)],
                mode="text", text=[label], xaxis="x2", yaxis="y2",
                textfont=dict(size=11 if label == "N" else 9, color=colour),
                opacity=1.0 if label == "N" else 0.6,
                hoverinfo="skip", showlegend=False,
            ))
        a = np.radians(theta)
        traces.append(go.Scatter(
            x=[0, DIAL_TRACK_R * np.cos(a)], y=[0, DIAL_TRACK_R * np.sin(a)],
            mode="lines", xaxis="x2", yaxis="y2",
            line=dict(color=colour, width=1, dash="dot"), opacity=0.5,
            hovertemplate=f"North, {theta:.0f} degrees in this circuit's "
                          "frame<extra></extra>", showlegend=False,
        ))

    traces.append(go.Scatter(
        x=[eye_x * 0.30, eye_x], y=[eye_y * 0.30, eye_y],
        mode="lines+markers", xaxis="x2", yaxis="y2",
        line=dict(color=ACCENT, width=2),
        marker=dict(size=[0, 9], color=ACCENT, symbol="circle"),
        hovertemplate=f"You are viewing from here<br>{azimuth:.0f} degrees in "
                      "this circuit's frame<extra></extra>",
        showlegend=False,
    ))
    return traces


def camera_eye(azimuth_deg: float, distance: float) -> dict:
    """
    Camera position for a bearing IN THE CIRCUIT'S OWN FRAME, at fixed height.

    The bearing is not a compass bearing. Zero degrees is the positive x axis
    of the position feed, which points somewhere different at every circuit.
    """
    tilt = np.radians(CAMERA_TILT_DEG)
    angle = np.radians(azimuth_deg)
    return dict(x=float(distance * np.cos(tilt) * np.cos(angle)),
                y=float(distance * np.cos(tilt) * np.sin(angle)),
                z=float(distance * np.sin(tilt)))


def _sector_slice(path: pd.DataFrame, bounds, sector: int) -> pd.DataFrame:
    lo, hi = sector_band(sector, bounds)
    return path[(path.time_fraction >= lo) & (path.time_fraction <= hi)]


def _car_data(df: pd.DataFrame) -> np.ndarray:
    return np.stack([df.driver, df.team_name.fillna("-"),
                     df.position.fillna(0).astype(int), df.tyre], axis=-1)


def _cars_to_draw(cars: pd.DataFrame, focus: int | None,
                  colours: dict[str, str]) -> pd.DataFrame:
    """
    The cars to place, with their colour, label and tyre string.

    Nothing is drawn until a driver is chosen. An empty track is the resting
    state: twenty unlabelled dots on first load say less than the outline
    alone, and the page is about one car at a time.
    """
    if focus is None:
        return cars.iloc[0:0]
    on = cars[cars.map_x.notna()].copy()
    if not len(on):
        return on
    on["colour"] = on.team_name.map(colours).fillna(NEUTRAL)
    on["label"] = on.name_acronym.fillna(on.driver_number.astype(str))
    on["tyre"] = np.where(
        on.compound.isna(), "tyre not recorded",
        on.compound.astype(str) + np.where(
            on.tyre_age.isna(), "",
            ", " + on.tyre_age.fillna(0).astype(int).astype(str) + " laps old"))
    return on


def relief_metres(path: pd.DataFrame) -> float:
    """
    Metres from the circuit's lowest point to its highest.

    A circuit with no z channel gives NaN, which would reach the axis ratio
    and be rejected by plotly, so it comes back as zero relief instead.
    """
    relief = float(path.z.max() - path.z.min())
    return relief if np.isfinite(relief) and relief > 0 else 0.0


def draw_3d(path: pd.DataFrame, cars: pd.DataFrame, bounds, sector: int | None,
            focus: int | None, colours: dict[str, str],
            azimuth: float = CAMERA_AZIMUTH_DEFAULT,
            distance: float = CAMERA_DISTANCE_DEFAULT,
            height: int = 620,
            north_rotation: float | None = None) -> go.Figure:
    """
    The same map with its real elevation, as a scene that turns left and right.

    Movement is deliberately restricted. Plotly's default 3D drag is a free
    orbit, which tumbles the circuit through every axis at once and makes a
    small hand movement throw the whole track sideways. Three things hold it
    still: the drag mode is turntable rather than orbit, so the horizon stays
    level and a drag can only spin the circuit; the viewing height is pinned;
    and the camera is driven from explicit bearing and distance values, so the
    page can offer slow, stepped controls instead of relying on the mouse.

    The z channel is genuine survey elevation: measured against published
    figures it correlates at 0.967 with a median difference of 2.3 m, and
    Spa's steepest climb falls at 16-18% of the lap, which is where Eau Rouge
    is.

    Height is drawn at true scale against the horizontal, so the slope you see
    is the slope the cars climb. Most circuits look close to flat as a result,
    which is what they are.
    """
    fig = go.Figure()

    # A circuit with no recorded elevation still draws, as a flat plane.
    path = path.assign(z=path.z.fillna(0.0))

    fig.add_trace(go.Scatter3d(
        x=path.x, y=path.y, z=path.z, mode="lines",
        line=dict(color=TRACK_LINE, width=10), hoverinfo="skip",
        showlegend=False))

    arrows = travel_arrows_3d(path, track_highlight())
    if arrows is not None:
        fig.add_trace(arrows)

    for trace in view_dial(path, azimuth, track_highlight(), north_rotation):
        fig.add_trace(trace)

    if sector and bounds:
        seg = _sector_slice(path, bounds, sector)
        fig.add_trace(go.Scatter3d(
            x=seg.x, y=seg.y, z=seg.z, mode="lines",
            line=dict(color=track_highlight(), width=10), hoverinfo="skip",
            showlegend=False))

    start = path.iloc[0]
    fig.add_trace(go.Scatter3d(
        x=[start.x], y=[start.y], z=[start.z], mode="markers",
        marker=dict(size=5, color=track_highlight(), symbol="diamond"),
        hovertemplate="Start/finish line<extra></extra>", showlegend=False))

    on = _cars_to_draw(cars, focus, colours)
    if len(on):
        rest = on[on.driver_number != focus]
        if len(rest):
            fig.add_trace(go.Scatter3d(
                x=rest.map_x, y=rest.map_y, z=rest.map_z.fillna(0.0),
                mode="markers",
                marker=dict(size=5, color=rest.colour, opacity=0.85,
                            line=dict(color="white", width=1)),
                customdata=_car_data(rest), hovertemplate=CAR_HOVER,
                showlegend=False))

        mine = on[on.driver_number == focus]
        if len(mine):
            fig.add_trace(go.Scatter3d(
                x=mine.map_x, y=mine.map_y, z=mine.map_z.fillna(0.0),
                mode="markers+text", text=mine.label, textposition="top center",
                textfont=dict(size=13, color=track_highlight()),
                marker=dict(size=11, color=mine.colour,
                            line=dict(color="white", width=2)),
                customdata=_car_data(mine), hovertemplate=CAR_HOVER,
                showlegend=False))

    span_x = float(path.x.max() - path.x.min())
    span_y = float(path.y.max() - path.y.min())
    span = max(span_x, span_y)

    # True scale: the height axis covers exactly as many metres per unit of
    # screen as the horizontal axes do.
    z_ratio = max(relief_metres(path) / span, MIN_Z_RATIO)

    hidden = dict(visible=False, showgrid=False, zeroline=False,
                  showbackground=False)
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        scene=dict(
            xaxis=hidden, yaxis=hidden, zaxis=hidden,
            aspectmode="manual",
            aspectratio=dict(x=span_x / span, y=span_y / span, z=z_ratio),
            camera=dict(eye=camera_eye(azimuth, distance),
                        up=dict(x=0, y=0, z=1),
                        center=dict(x=0, y=0, z=0)),
            # turntable keeps the horizon level; orbit lets the track tumble.
            dragmode="turntable",
            domain=dict(x=[0, 1], y=[0, 1]),
            # Tied to the camera controls only, so changing the lap or the
            # driver redraws the cars without yanking the view back, while
            # moving a camera slider does move it.
            uirevision=f"{azimuth}:{distance}",
        ),
        # The view dial. Ordinary 2D axes laid over the scene's top-right
        # corner, so it holds still while the circuit turns beneath it.
        # scaleanchor keeps the miniature outline the right shape rather than
        # stretching it to fill a square.
        xaxis2=dict(domain=DIAL_DOMAIN, anchor="y2", visible=False,
                    fixedrange=True, range=[-DIAL_LIMIT, DIAL_LIMIT]),
        yaxis2=dict(domain=DIAL_DOMAIN, anchor="x2", visible=False,
                    fixedrange=True, range=[-DIAL_LIMIT, DIAL_LIMIT],
                    scaleanchor="x2"),
    )
    return fig


def draw(path: pd.DataFrame, cars: pd.DataFrame, bounds, sector: int | None,
         focus: int | None, colours: dict[str, str],
         height: int = 560) -> go.Figure:
    """
    The track, then the cars on it.

    Drawn on a fixed 1:1 aspect ratio with the axes hidden. A circuit stretched
    to fit a widescreen box is not that circuit, and the shape is the whole
    point of showing a map rather than a table.
    """
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=path.x, y=path.y, mode="lines", name="Track",
        line=dict(color=TRACK_LINE, width=6), hoverinfo="skip",
        showlegend=False))

    arrows = travel_arrows_2d(path, track_highlight())
    if arrows is not None:
        fig.add_trace(arrows)

    if sector and bounds:
        seg = _sector_slice(path, bounds, sector)
        fig.add_trace(go.Scatter(
            x=seg.x, y=seg.y, mode="lines", name=f"Sector {sector}",
            line=dict(color=track_highlight(), width=6), hoverinfo="skip",
            showlegend=False))

    start = path.iloc[0]
    fig.add_trace(go.Scatter(
        x=[start.x], y=[start.y], mode="markers", name="Start/finish",
        marker=dict(symbol="line-ns", size=18, line=dict(color=track_highlight(),
                                                         width=3)),
        hovertemplate="Start/finish line<extra></extra>", showlegend=False))

    on = _cars_to_draw(cars, focus, colours)

    if len(on):
        rest = on[on.driver_number != focus]
        if len(rest):
            fig.add_trace(go.Scatter(
                x=rest.map_x, y=rest.map_y, mode="markers",
                marker=dict(size=10, color=rest.colour, opacity=0.75,
                            line=dict(color="white", width=1)),
                customdata=_car_data(rest), hovertemplate=CAR_HOVER,
                showlegend=False))

        mine = on[on.driver_number == focus]
        if len(mine):
            fig.add_trace(go.Scatter(
                x=mine.map_x, y=mine.map_y, mode="markers+text",
                text=mine.label, textposition="top center",
                textfont=dict(size=12, color=track_highlight()),
                marker=dict(size=24, color=mine.colour,
                            line=dict(color="white", width=2.5)),
                customdata=_car_data(mine), hovertemplate=CAR_HOVER,
                showlegend=False))

    pad = 0.06 * max(path.x.max() - path.x.min(), path.y.max() - path.y.min())
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False, fixedrange=True,
                   range=[path.x.min() - pad, path.x.max() + pad]),
        yaxis=dict(visible=False, fixedrange=True,
                   scaleanchor="x", scaleratio=1,
                   range=[path.y.min() - pad, path.y.max() + pad]),
    )
    return fig


# --- the single-car panel --------------------------------------------------------

def _flags(lap) -> str:
    live = [name for name, col in (("Safety car", "sc_flag"),
                                   ("Virtual safety car", "vsc_flag"),
                                   ("Red flag", "red_flag"),
                                   ("Yellow in sector", "yellow_sector_flag"))
            if getattr(lap, col, 0) == 1]
    return ", ".join(live) if live else "Green, nothing flying"


def _num(value, fmt: str, suffix: str = "") -> str:
    return "-" if pd.isna(value) else f"{value:{fmt}}{suffix}"


def _kv(title: str, pairs: list[tuple[str, str]]) -> None:
    """
    One compact labelled block.

    st.metric is deliberately large; twenty-three of them stacked four to a
    row turned this panel into most of a screen of whitespace for about forty
    short values. This is the same information as a dense two-column list, so
    all four blocks sit side by side and the whole panel fits without
    scrolling.
    """
    rows = "".join(
        f"<tr>"
        f"<td style='padding:1px 12px 1px 0;color:#7A7A85;white-space:nowrap'>{k}</td>"
        f"<td style='padding:1px 0;font-weight:600;text-align:right;"
        f"white-space:nowrap'>{v}</td>"
        f"</tr>"
        for k, v in pairs)
    st.markdown(
        "<div style='font-size:0.8rem;line-height:1.45'>"
        f"<div style='font-weight:700;font-size:0.75rem;letter-spacing:.04em;"
        f"text-transform:uppercase;color:{ink()};margin-bottom:.35rem'>{title}</div>"
        f"<table style='border-collapse:collapse;width:100%'>{rows}</table>"
        "</div>",
        unsafe_allow_html=True)


def car_panel(lap, coverage_row) -> None:
    """Everything recorded about one car on one lap, and what is not."""
    pos = f"P{int(lap.position)}" if pd.notna(lap.position) else "unclassified"
    st.markdown(
        f"**{lap.driver}** &nbsp;·&nbsp; lap {int(lap.lap_number)} "
        f"&nbsp;·&nbsp; {pos} &nbsp;·&nbsp; **{fmt_lap(lap.lap_duration)}**"
        f" &nbsp;·&nbsp; {fmt_gap(lap.gap_to_leader_seconds, lap.gap_to_leader_laps)}"
        " to the leader"
    )

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        _kv("Timing", [
            ("Sector 1", _num(lap.duration_sector_1, ".3f", "s")),
            ("Sector 2", _num(lap.duration_sector_2, ".3f", "s")),
            ("Sector 3", _num(lap.duration_sector_3, ".3f", "s")),
            ("vs race median", _num(lap.lap_vs_median, "+.3f", "s")),
            ("To car ahead",
             fmt_gap(lap.interval_seconds, lap.interval_laps)),
        ])

    telemetry = bool(getattr(coverage_row, "has_car_data", 0))
    with c2:
        _kv("Speed", [
            ("Intermediate 1", _num(lap.i1_speed, ".0f", " km/h")),
            ("Intermediate 2", _num(lap.i2_speed, ".0f", " km/h")),
            ("Speed trap", _num(lap.st_speed, ".0f", " km/h")),
            ("Trace", "recorded" if telemetry else NOT_RETRIEVED),
            ("Gear, throttle, rpm",
             "recorded" if telemetry else NOT_RETRIEVED),
        ])

    with c3:
        _kv("Tyre", [
            ("Compound", lap.compound if pd.notna(lap.compound) else "-"),
            ("Age", _num(lap.tyre_age, ".0f", " laps")),
            ("Stint", _num(lap.stint_number, ".0f")),
            ("Pit out lap", "yes" if lap.is_pit_out_lap == 1 else "no"),
            ("Race control", _flags(lap)),
        ])

    with c4:
        _kv("Conditions at lap start", [
            ("Air", _num(lap.air_temperature, ".1f", " C")),
            ("Track", _num(lap.track_temperature, ".1f", " C")),
            ("Wind", _num(lap.wind_speed, ".1f", " m/s")
             + ("" if pd.isna(lap.wind_direction)
                else f" @ {int(lap.wind_direction)} deg")),
            ("Humidity", _num(lap.humidity, ".0f", "%")),
            ("Pressure", _num(lap.pressure, ".0f", " hPa")),
            ("Rain", "yes" if lap.rainfall == 1 else "no"),
        ])

    note = getattr(coverage_row, "telemetry_note", "")
    st.caption(
        f"**Telemetry.** {note} The three speeds above are timing-loop "
        "readings, published for every race."
        + ("" if coverage_row.has_measured_xy else
           " The dot on the map is derived from lap timing, not recorded."))
