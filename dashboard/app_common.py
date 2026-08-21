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
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

REPO = "fatima-zahra20/F1-Reality-Check"
ASSET_URL = f"https://github.com/{REPO}/releases/download/data-latest/dashboard.db.gz"

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
LOCAL_DB = APP_DIR / "data" / "dashboard.db"

# Fallback for teams whose colour is missing in the source data.
NEUTRAL = "#8A8A94"


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
    """Open what was just written and confirm it is this project's bundle."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
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
    return sorted(tmp.glob(f"{CACHE_PREFIX}*.db"),
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
def get_connection() -> sqlite3.Connection:
    """
    One connection per server process, not per session - the download and the
    67 MB file are shared by every visitor rather than repeated for each.

    Re-checked every BUNDLE_TTL seconds. The bundle is stored under a name
    derived from the Release's own ETag, so a new publish lands in a NEW file
    and sessions still reading the old one are never pulled out from under.
    """
    if LOCAL_DB.exists():
        return sqlite3.connect(LOCAL_DB, check_same_thread=False)

    target = Path(tempfile.gettempdir()) / f"{CACHE_PREFIX}{_asset_stamp()}.db"

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
            return sqlite3.connect(fallback, check_same_thread=False)

    _prune(keep=target)

    # check_same_thread=False: Streamlit serves reruns from a worker pool, so
    # the connection is touched by threads other than the one that opened it.
    return sqlite3.connect(target, check_same_thread=False)


# Same TTL as the connection, deliberately. Without it these results would
# outlive the bundle they were read from: the connection would quietly move to
# newly published data while every chart on the page went on showing values
# cached from the old one, which is the original bug wearing a different hat.
@st.cache_data(ttl=BUNDLE_TTL, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql(sql, get_connection(), params=params)


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

def render_footer() -> None:
    """Author credit, shown at the bottom of every page for consistency."""
    st.divider()
    st.markdown(
        """
        <div style="text-align:center; color:#6B6B76; font-size:0.85rem;
                    padding: 0 0 1.5rem;">
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
