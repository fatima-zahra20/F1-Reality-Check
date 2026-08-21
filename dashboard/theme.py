"""
theme.py - light and dark, decided in one place.

How the switch works
--------------------
Streamlit has no public API for setting the theme from Python. What makes an
in-page switch possible was checked in Streamlit's own source rather than
assumed: `AppSession._create_new_session_message` runs on the SCRIPT_STARTED
event, not only on connect, and it repopulates the theme from current config
every time. So rewriting the config options and calling st.rerun() reaches the
browser on the next script start.

WHY THERE IS ONE [theme] SECTION IN config.toml AND NOT [theme.light] PLUS
[theme.dark]. The split looks like the obvious way to ship two palettes. It is
a trap: defining both makes Streamlit follow the visitor's SYSTEM appearance
and ignore `base` entirely. The app then opened dark on a dark machine, against
its own configured default, and the switch could not pull it back because
`base` no longer decided anything. One section, rewritten at runtime, keeps the
app in charge of its own default.

KNOWN LIMITATION, WORTH KNOWING BEFORE THIS SHIPS. Config options are process
wide, not per session. One server process serves every visitor, so if two
people are on the deployed app at once and one flips the switch, the other sees
it change on their next interaction. The alternative is Streamlit's built-in
Appearance menu, which is genuinely per visitor but hidden three clicks deep
and cannot default to light. This was chosen knowingly.

What actually breaks in dark mode
---------------------------------
Not the widgets. Streamlit restyles those from the config values below. The
charts break, for three reasons:

    INK is a near-black line colour and vanishes on a near-black background.
    Grid and zero lines are drawn in translucent BLACK and disappear.
    A white-based colourscale like "Reds" turns low values into bright blocks.

ACCENT (F1 red) and MUTED (mid grey) read correctly on both, so they are plain
constants and always were.

Why AXIS_BASE and PLOT_BASE live here
-------------------------------------
They are dictionaries, and Python binds those by reference. Every module that
did `from story_common import AXIS_BASE` holds the same object, so refreshing it
in place reaches all 35 call sites without editing one of them. story_common
re-exports them so those imports keep working unchanged.

INK could not get the same treatment. A string is bound by value, so a module
that imported it would keep the light colour forever. It became `ink()`, which
is why that one name changed at its 21 call sites and the dicts did not.
"""

from __future__ import annotations

import streamlit as st
from streamlit import config as _st_config

# --- the two palettes ----------------------------------------------------------

# `chrome` values are Streamlit config options, written at runtime by the switch.
# The rest are chart colours, read by the helpers below.
LIGHT = {
    "chrome": {
        "theme.base": "light",
        "theme.backgroundColor": "#FFFFFF",
        "theme.secondaryBackgroundColor": "#F0F2F6",
        "theme.textColor": "#31333F",
    },
    "ink": "#31333F",                    # the subject of the chart
    "grid": "rgba(0,0,0,0.08)",
    "zero": "rgba(0,0,0,0.30)",
    # Plotly's own "Reds", which starts at white and is correct on a white page.
    "heat": [[0.0, "#FFF5F0"], [0.25, "#FCBBA1"], [0.5, "#FC9272"],
             [0.75, "#EF3B2C"], [1.0, "#99000D"]],
}

DARK = {
    "chrome": {
        "theme.base": "dark",
        "theme.backgroundColor": "#0F0F14",
        "theme.secondaryBackgroundColor": "#1A1A22",
        "theme.textColor": "#E8E8ED",
    },
    "ink": "#E8E8ED",
    "grid": "rgba(255,255,255,0.14)",
    "zero": "rgba(255,255,255,0.38)",
    # Starts at the page's own background instead of white, so an empty-ish cell
    # recedes rather than glowing, and climbs to the same F1 red at the top.
    "heat": [[0.0, "#1A1A22"], [0.25, "#4A1512"], [0.5, "#8A1008"],
             [0.75, "#C21207"], [1.0, "#FF3B2F"]],
}

ACCENT = "#E10600"   # something that needs attention
MUTED = "#9A9AA5"    # the comparison, never the subject

# Mutated in place by apply(). Imported by reference across the dashboard, so
# these two objects must never be reassigned, only updated.
PLOT_BASE = dict(
    margin=dict(l=10, r=10, t=10, b=10),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
)
AXIS_BASE = dict(fixedrange=True, gridcolor=LIGHT["grid"])

_CHOICE_KEY = "theme_choice"


# --- which theme is live -------------------------------------------------------

def active() -> str:
    """
    "light" or "dark", light until the reader says otherwise.

    Deliberately does NOT consult st.context.theme. That reports what the
    browser last rendered, which follows the visitor's system preference when
    they have not chosen, and letting it decide is exactly how the app ended up
    opening dark against its own configured default.

    THE CHOICE IS KEPT IN THE URL, not only in session state. Reloading a page
    starts a NEW session, and session state does not survive that, so the switch
    silently snapped back to light on every refresh. The query string is the one
    thing a reload carries with it, so it is what the choice is stored in. It
    also means a reader who bookmarks or shares the page keeps the theme.
    """
    chosen = st.session_state.get(_CHOICE_KEY)
    if chosen is None:
        chosen = st.query_params.get("theme")
        chosen = chosen if chosen in ("light", "dark") else "light"
        # Settled once per session, including the light default. Leaving it
        # unset would make "never chosen" and "chose light" indistinguishable a
        # few lines below, and the switch would then rerun on every first load
        # trying to apply a theme that was already correct.
        st.session_state[_CHOICE_KEY] = chosen
    return "dark" if chosen == "dark" else "light"


def _sync_chrome(wanted: str) -> bool:
    """
    Make the config match `wanted`. True when it had to change.

    Compares against the LIVE config rather than remembering what this session
    last wrote, because config is process wide: another visitor's flip can move
    it underneath this session. Reading the real value makes this self
    correcting, and returning False when it already matches is what stops a
    fresh session paying for a pointless rerun.
    """
    chrome = (DARK if wanted == "dark" else LIGHT)["chrome"]
    if _st_config.get_option("theme.base") == chrome["theme.base"]:
        return False
    for option, value in chrome.items():
        _st_config.set_option(option, value)
    return True


def palette() -> dict:
    return DARK if active() == "dark" else LIGHT


def ink() -> str:
    """The subject colour: near-black on light, near-white on dark."""
    return palette()["ink"]


def zero_line() -> str:
    """A zero line needs more contrast than the grid it sits in."""
    return palette()["zero"]


def heat() -> list:
    """Sequential colourscale for count heatmaps, anchored to the page colour."""
    return palette()["heat"]


def apply() -> None:
    """
    Point the shared layout dicts at the active palette.

    Updating in place rather than reassigning is the whole trick: the modules
    that imported these dicts are holding these exact objects.
    """
    AXIS_BASE["gridcolor"] = palette()["grid"]


# --- the switch ----------------------------------------------------------------

def render_toggle(container=None) -> None:
    """
    The dark mode switch, plus the palette refresh that has to go with it.

    Every page calls this once, before any figure is built. It reruns only when
    the reader actually flips it: comparing against the stored choice rather
    than against the reported theme is what stops a rerun loop if a flip does
    not take.
    """
    apply()
    where = container if container is not None else st.sidebar

    on = where.toggle(
        "Dark mode", value=(active() == "dark"), key="dark_mode_switch",
        help="Switches the whole page, charts included. Kept on reload.",
    )
    wanted = "dark" if on else "light"

    # active() ran when the toggle's value was computed, so this is always
    # "light" or "dark", never None.
    previous = st.session_state[_CHOICE_KEY]
    flipped = previous != wanted

    if flipped:
        st.session_state[_CHOICE_KEY] = wanted

    # The URL is the only thing a reload carries, so BOTH values are written,
    # not just dark. Recording only the non-default would mean "light" was
    # spelled as an absent parameter, and an absent parameter is also what a
    # first visit looks like: the two states would be indistinguishable, and
    # choosing light then reloading would land on light by accident rather than
    # by instruction. Written every run, not only on a flip, so a page reached
    # by a link that omits it still ends up carrying it.
    st.query_params["theme"] = wanted

    # Two reasons to rerun, and both are needed. The reader just flipped the
    # switch; or this session arrived carrying a theme the process config does
    # not yet reflect, which is exactly what a reload with ?theme=dark looks
    # like, and also what another visitor's flip leaves behind.
    if _sync_chrome(wanted) or flipped:
        apply()
        st.rerun()
