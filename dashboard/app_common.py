"""
app_common.py - shared data access for every page of the dashboard.

streamlit_app.py is the router (st.navigation); each file under views/ is a
page it can show. Pages do not import each other, so anything more than one
of them needs (the database connection, formatting helpers, team colours)
lives here instead.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import shutil
import tempfile
from pathlib import Path

import duckdb
import pandas as pd
import requests
import streamlit as st

REPO = "fatima-zahra20/F1-Reality-Check"

# Must name the asset pipeline/s06_publish.py uploads (its ASSET_NAME). Nothing
# checks the two agree: a mismatch simply 404s and the app reports that the data
# has not been published yet.
ASSET_URL = (f"https://github.com/{REPO}/releases/download/data-latest/"
             "dashboard.duckdb.gz")

# How long the app may serve a bundle without re-checking the Release behind it.
#
# This used to be forever. The download was cached under a FIXED name in the
# container's temp directory and reused for the life of that container, so:
#
#   publishing new data changed nothing until somebody clicked Reboot, and
#   a download that arrived truncated stayed broken for exactly as long.
#
# Fifteen minutes is a bounded staleness window, not a guess. The check is one
# HEAD request of a few hundred milliseconds, and it runs only when this cache
# expires, never per page view. Publishing now reaches visitors on its own,
# which is the thing that lets the pipeline run unattended.
BUNDLE_TTL = 900

# Checked after every download. Deliberately a few core names rather than all
# twenty-one: the app should keep working when the pipeline gains a table, and
# should refuse to start when it has been handed something that is not this
# bundle at all.
REQUIRED_TABLES = {"dim_race", "fact_lap", "diag_tests", "map_coverage"}


class BundleUnavailable(RuntimeError):
    """Carries a message already fit to show a visitor."""


def _open(path: Path) -> "duckdb.DuckDBPyConnection":
    """
    Open a bundle read-only, with timestamps pinned to UTC.

    THE TIMEZONE LINE IS NOT OPTIONAL AND IS NOT COSMETIC. fact_lap.date_start
    and fact_event.event_time are stored as TIMESTAMP WITH TIME ZONE, and DuckDB
    renders those in whatever timezone the HOST is set to. On this laptop that
    is Africa/Casablanca, so the column arrives as
    datetime64[us, Africa/Casablanca]; on Streamlit Cloud it would arrive as UTC.
    Same instants either way, but a page that formats one without converting
    shows a clock time that is right in one place and an hour out in the other,
    for half the year. That is the same shape of bug as the strftime("%B") one
    data_vintage() was rewritten to avoid: correct locally, wrong deployed, and
    invisible until someone notices the numbers.

    SET GLOBAL rather than SET, because query() takes a cursor per call and a
    cursor does NOT inherit a plain SET from the connection that made it.
    Measured: with SET, the connection reported UTC and its cursor still
    reported Africa/Casablanca.

    read_only because DuckDB takes a write lock on a file opened for writing,
    which would stop a second local process from opening the same bundle.
    """
    con = duckdb.connect(str(path), read_only=True)
    con.execute("SET GLOBAL TimeZone = 'UTC'")
    return con

# Resolved from this file's own location, not the current working directory,
# so it doesn't matter whether streamlit was launched from the repo root or
# from inside dashboard/ - both land on the same paths.
APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
ASSETS_DIR = APP_DIR / "assets"

# In data/ beside this file, kept out of the source listing because it is a
# 67 MB generated file and everything else here is hand-written. The pipeline
# writes it there (pipeline/serving.py owns that path); git ignores it. On
# Streamlit Cloud it is absent, so the download below runs instead, which is the
# normal deployed path rather than a fallback.
LOCAL_DB = APP_DIR / "data" / "dashboard.duckdb"

# Fallback for teams whose colour is missing in the source data.
NEUTRAL = "#8A8A94"

# Written out rather than taken from strftime("%B"), which formats in the
# server's locale. The dashboard is in English everywhere else and should not
# start speaking another language because of where it happens to be hosted.
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


# --- data -----------------------------------------------------------------

CACHE_PREFIX = "f1_reality_check_"


@st.cache_data(ttl=BUNDLE_TTL, show_spinner=False)
def _asset_stamp() -> str:
    """
    A short string identifying the bundle currently on the Release.

    Whatever the CDN offers, in order of usefulness: an ETag is derived from the
    content and so changes exactly when the file does; Last-Modified and
    Content-Length together are a workable substitute.

    Returns "unknown" rather than raising. A HEAD that fails should fall through
    to the download, which produces a real error with a real message, rather
    than breaking here on a header that happened to be absent.
    """
    try:
        r = requests.head(ASSET_URL, timeout=15, allow_redirects=True)
        parts = [r.headers.get("ETag", ""),
                 r.headers.get("Last-Modified", ""),
                 r.headers.get("Content-Length", "")]
    except requests.RequestException:
        return "unknown"
    joined = "|".join(p for p in parts if p)
    if not joined:
        return "unknown"
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _check_readable(path: Path) -> None:
    """
    Open what was just written and confirm it is this project's bundle.

    This check does more work than it used to. A DuckDB file written by a newer
    release than the one installed here cannot be read at all, and the failure
    arrives as a plain exception on connect rather than as a missing table. So
    the connect is inside the try, and any failure to open is reported the same
    way a damaged download is: with a message a visitor can act on.
    """
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        raise BundleUnavailable(
            f"the downloaded file could not be opened ({exc.__class__.__name__}). "
            "This usually means it was written by a different version of duckdb "
            "than the one installed here."
        ) from exc
    try:
        names = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main'").fetchall()}
    finally:
        con.close()
    missing = REQUIRED_TABLES - names
    if missing:
        raise BundleUnavailable(
            "the downloaded file is missing " + ", ".join(sorted(missing)))


def _download_to(path: Path) -> None:
    """
    Fetch, decompress and check the bundle, then move it into place.

    Written to a .part file and renamed only once it is a database that opens
    and holds the tables the app needs, so a partial download can never be
    mistaken for a finished one. That mistake used to persist until somebody
    rebooted the app, because the half-written file satisfied the "does it
    exist" test that was the only thing guarding the cache.

    Truncation is caught for free: gzip carries a CRC and an uncompressed length
    in its trailer, so decompressing a cut-short stream raises rather than
    quietly producing a short file. That is why there is no separate checksum
    here.
    """
    part = path.with_suffix(".part")
    try:
        r = requests.get(ASSET_URL, timeout=180)
    except requests.RequestException as e:
        raise BundleUnavailable(
            f"the data could not be downloaded ({type(e).__name__})") from e

    if r.status_code == 404:
        raise BundleUnavailable("NOT_PUBLISHED")
    if r.status_code != 200:
        raise BundleUnavailable(
            f"the download returned HTTP {r.status_code}")

    try:
        with gzip.open(io.BytesIO(r.content), "rb") as src, open(part, "wb") as dst:
            shutil.copyfileobj(src, dst)
        _check_readable(part)
    except BundleUnavailable:
        part.unlink(missing_ok=True)
        raise
    except Exception as e:
        part.unlink(missing_ok=True)
        raise BundleUnavailable(
            f"the download arrived damaged ({type(e).__name__})") from e

    part.replace(path)


def _cached_bundles() -> list[Path]:
    """Previously downloaded bundles, newest first."""
    tmp = Path(tempfile.gettempdir())
    return sorted(tmp.glob(f"{CACHE_PREFIX}*.duckdb"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def _prune(keep: Path) -> None:
    """
    Drop bundles from earlier publishes.

    Unlinking a file another session still has open is safe on Linux, which is
    what Streamlit Cloud runs: the open handle keeps working and the name simply
    goes away. Windows refuses, which is why this never raises. Local runs use
    LOCAL_DB and never reach here at all.
    """
    for old in _cached_bundles():
        if old != keep:
            try:
                old.unlink()
            except OSError:
                pass


@st.cache_resource(ttl=BUNDLE_TTL, show_spinner="Loading race data…")
def get_connection() -> "duckdb.DuckDBPyConnection":
    """
    One connection per server process, not per session - the download and the
    bundle are shared by every visitor rather than repeated for each.

    Re-checked every BUNDLE_TTL seconds. The bundle is stored under a name
    derived from the Release's own ETag, so a new publish lands in a NEW file
    and sessions still reading the old one are never pulled out from under.

    Opened through _open, which pins the timezone as well as opening read-only.

    There is no check_same_thread to pass. sqlite3 refused cross-thread use by
    default and had to be told not to; DuckDB serialises access itself. query()
    still takes a cursor per call, which is the documented way to hand work to
    Streamlit's worker pool.
    """
    if LOCAL_DB.exists():
        return _open(LOCAL_DB)

    target = Path(tempfile.gettempdir()) / f"{CACHE_PREFIX}{_asset_stamp()}.duckdb"

    if not target.exists():
        try:
            _download_to(target)
        except BundleUnavailable as e:
            # Serving slightly old data beats serving an error page. A failed
            # refresh should not take down an app that was working a minute
            # ago, so a previous bundle is used if there is one.
            fallback = next((p for p in _cached_bundles() if p != target), None)
            if fallback is None:
                if str(e) == "NOT_PUBLISHED":
                    st.error(
                        "The data has not been published yet.\n\n"
                        "Run `python pipeline/s06_publish.py --execute` to "
                        f"upload it to the `data-latest` release of `{REPO}`."
                    )
                else:
                    st.error(
                        f"**The race data could not be loaded**, because "
                        f"{e}.\n\nThis is usually temporary. Refreshing the "
                        "page in a minute will try again."
                    )
                st.stop()
            st.warning(
                "**Showing the previously downloaded data.** The latest "
                f"version could not be fetched, because {e}."
            )
            return _open(fallback)

    _prune(keep=target)

    return _open(target)


# Same TTL as the connection, deliberately. Without it these results would
# outlive the bundle they were read from: the connection would quietly move to
# newly published data while every chart on the page went on showing values
# cached from the old one, which is the original bug wearing a different hat.
@st.cache_data(ttl=BUNDLE_TTL, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """
    Run a query and get a DataFrame back. Same signature as before, same `?`
    placeholders, so no page needed changing.

    NOT pd.read_sql. It accepts a DuckDB connection and works, but it goes
    through the DB-API and builds Python objects one row at a time; .df() goes
    through Arrow. On the pipeline side the same swap took a scan from 264s to
    20s, and here it is the difference between a page rendering and a page
    someone waits on.

    .cursor() per call rather than sharing the one connection: Streamlit serves
    reruns from a worker pool, so this is touched by several threads at once,
    and a cursor is DuckDB's documented way to give each its own handle on the
    same open database. It costs nothing - no file is reopened.
    """
    cur = get_connection().cursor()
    try:
        if params:
            return cur.execute(sql, list(params)).df()
        return cur.execute(sql).df()
    finally:
        cur.close()


@st.cache_data(ttl=BUNDLE_TTL, show_spinner=False)
def team_colours() -> dict[str, str]:
    df = query("SELECT team_name, team_colour FROM dim_team")
    return {
        r.team_name: (f"#{r.team_colour}" if pd.notna(r.team_colour) else NEUTRAL)
        for r in df.itertuples()
    }


def _join_and(items: list[str]) -> str:
    """["a", "b", "c"] -> "a, b and c"."""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


@st.cache_data(ttl=BUNDLE_TTL, show_spinner=False)
def coverage_gaps(kind: str) -> str:
    """
    Which seasons are missing a kind of record, counted rather than remembered.

    Returns a phrase like "6 of 22 races in 2023 and 3 of 11 in 2026", or an
    empty string when every race is covered.

    WHY THIS IS COMPUTED. Three pages carried this sentence with the numbers
    written in by hand: "6 of 22 races in 2023", "8 of 11 races in 2026",
    "2,744 messages across 2023, but 217 across 2026". Every one was true when
    it was typed. The 2023 figures still are, because that season is closed and
    its numbers are final. The 2026 figures stopped being true at the next race
    weekend and would go stale again after every one after that.

    This is the same drift that had the landing page announcing 70 races while
    the sidebar, which counted, said 81. A number nobody recomputes does not
    stay wrong quietly for long, and a dashboard whose whole claim is that it
    tests things should not be the last to notice.
    """
    if kind == "championship":
        covered = "SELECT DISTINCT session_key FROM fact_championship"
        params: tuple = ()
    else:
        covered = ("SELECT DISTINCT session_key FROM fact_event "
                   "WHERE event_type = ?")
        params = (kind,)

    rows = query(f"""
        SELECT r.year AS year,
               COUNT(*) AS races,
               SUM(CASE WHEN c.session_key IS NULL THEN 1 ELSE 0 END) AS missing
        FROM dim_race r
        LEFT JOIN ({covered}) c ON c.session_key = r.session_key
        GROUP BY r.year
        ORDER BY r.year
    """, params)

    gaps = rows[rows.missing > 0]
    if gaps.empty:
        return ""

    parts = [f"{int(g.missing)} of {int(g.races)} in {int(g.year)}"
             for g in gaps.itertuples()]
    # The first one carries the noun so the list reads as a sentence rather
    # than as three bare ratios.
    first = gaps.iloc[0]
    parts[0] = (f"{int(first.missing)} of {int(first.races)} races "
                f"in {int(first.year)}")
    return _join_and(parts)


# --- formatting -------------------------------------------------------------

def fmt_lap(seconds) -> str:
    """Lap times read as m:ss.sss, never as a raw float."""
    if seconds is None or pd.isna(seconds):
        return "-"
    m, s = divmod(float(seconds), 60)
    return f"{int(m)}:{s:06.3f}"


def fmt_gap(seconds, laps) -> str:
    if pd.notna(laps) and laps:
        return f"+{int(laps)} lap" + ("s" if laps > 1 else "")
    if pd.isna(seconds):
        return "-"
    return "-" if seconds == 0 else f"+{seconds:.3f}s"


# --- assets ------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def find_asset_b64(stem: str) -> str | None:
    """
    Looks for assets/<stem>.{jpg,jpeg,png,webp} and returns it as a base64
    data URI, or None if none exist.

    Base64-embedding rather than a static file URL because Streamlit Cloud
    needs enableStaticServing configured to serve plain files, and a data URI
    works identically local and deployed with no extra config.
    """
    mime = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp"}
    for ext, kind in mime.items():
        path = ASSETS_DIR / f"{stem}{ext}"
        if path.exists():
            data = base64.b64encode(path.read_bytes()).decode()
            return f"data:image/{kind};base64,{data}"
    return None


# --- footer -------------------------------------------------------------------

@st.cache_data(ttl=BUNDLE_TTL, show_spinner=False)
def data_vintage() -> str:
    """
    "Data through 26 July 2026, 81 races", or "" if it cannot be read.

    WHY THIS IS ON EVERY PAGE. Two reasons, and the second is the one that
    earned it.

    For a reader: it dates everything above it. A dashboard that states findings
    without saying when the data stops is asking to be read as current forever,
    and any sentence that does drift is at least now read against a date rather
    than as a claim about today.

    For you: it is the only way to see what the deployed app is actually
    serving. Before this, confirming a publish had landed meant rebooting and
    hoping, because a page that looks right looks identical whether it is
    showing this week's bundle or last month's.

    Returns an empty string rather than raising. This runs at the bottom of
    every page including ones already reporting a problem, and a footer is
    never worth an error.
    """
    try:
        row = query("SELECT MAX(race_date) AS latest, COUNT(*) AS races "
                    "FROM dim_race")
        if not len(row):
            return ""
        raw = row.latest.iloc[0]
        races = int(row.races.iloc[0])
        if raw is None or (isinstance(raw, float) and raw != raw):
            return ""

        # PARSED BY HAND, not by pandas, and not formatted with strftime.
        #
        # This returned the right sentence locally and an empty one on Streamlit
        # Cloud, which runs a different Python and a different pandas against
        # the same data. The two candidates were pd.to_datetime's handling of a
        # tz-aware ISO string and strftime's %B, which is locale dependent and
        # therefore not guaranteed to be English on someone else's machine.
        #
        # race_date is stored as ISO 8601 and always starts YYYY-MM-DD, so the
        # first ten characters are all this needs. Slicing them removes both
        # candidates at once and cannot behave differently on another host.
        stamp = str(raw)[:10]
        year, month, day = (int(p) for p in stamp.split("-"))
        return (f"Data through {day} {MONTHS[month - 1]} {year}, "
                f"{races} races")
    except Exception as exc:
        # Degrades to no line, but says why in the server log. Swallowing this
        # silently is what turned a one-line bug into an evening of guessing.
        print(f"[data_vintage] {type(exc).__name__}: {exc}")
        return ""


def render_footer() -> None:
    """Author credit and data vintage, at the bottom of every page."""
    st.divider()
    vintage = data_vintage()
    st.markdown(
        f"""
        <div style="text-align:center; color:#6B6B76; font-size:0.85rem;
                    padding: 0 0 1.5rem;">
            {f'{vintage}<br><br>' if vintage else ''}
            Data Analyst<br>
            Boutkhil Fatima Zahra<br>
            <a href="https://github.com/fatima-zahra20" target="_blank"
               style="color:#6B6B76;">GitHub</a>
            &nbsp;&middot;&nbsp;
            <a href="https://www.linkedin.com/in/fatima-zahra-boutkhil-393011309/"
               target="_blank" style="color:#6B6B76;">LinkedIn</a>
        </div>
        """,
        unsafe_allow_html=True,
    )
