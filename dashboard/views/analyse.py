"""
analyse.py - the descriptive layer, told as three stories about one race.

    Story of a Race     the whole field, race-wide
    Story of a Driver   one driver's race, through their eyes
    Story of a Team     both cars compared, the teammate lens

All three share the same Year and Race filter; Driver and Team add one more.
Each story answers the questions in
DESCRIPTIVE ANALYTICS/descriptive_question_bank.md, in the order the bank
asks them, so the page reads chronologically rather than by chart type.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import story_driver  # noqa: E402
import story_race  # noqa: E402
import story_team  # noqa: E402
import theme  # noqa: E402
import url_state  # noqa: E402
from app_common import query, render_footer  # noqa: E402

# --- shared filters -----------------------------------------------------------

races = query("""
    SELECT session_key, year, round, race_name, circuit, country, race_date,
           total_laps, entrants, dnf_count, safety_car_periods, vsc_periods,
           red_flag_periods, avg_track_temp, avg_air_temp, pct_samples_wet,
           is_wet_race, circuit_type
    FROM dim_race
    ORDER BY year DESC, round
""")

st.sidebar.title("F1 Reality Check")
st.sidebar.caption(f"{len(races)} races · {races.year.min()}-{races.year.max()}")

# Before any figure is built on this page: the switch also refreshes the shared
# chart palette, so a flip and the charts change together rather than a rerun
# apart.
theme.render_toggle()

seasons = sorted(races.year.unique(), reverse=True)
url_state.restore("season_choice", int, valid=set(seasons))
season = st.sidebar.selectbox("Season", seasons, key="season_choice")
season_races = races[races.year == season]

# Options are session_keys, not names: two seasons share race names, and the
# label becomes a lookup instead of a filter over the frame.
labels = {
    int(r.session_key): f"R{int(r.round):02d}  {r.race_name}"
    for r in season_races.itertuples()
}
options = list(labels)

# Changing season leaves the previous season's race in session state, which is
# no longer a valid option. Reset it before the widget renders - Streamlit
# will otherwise try to format a value it cannot find and raise.
#
# options[-1], NOT options[0]. `races` is ordered year DESC then round, so
# within a season the rounds run ASCENDING and options[0] is round 1. The page
# therefore opened on the oldest race of the newest season, which is the least
# interesting race it could have picked and reads as a bug. The last entry is
# the most recent round, so the default is now the newest race there is.
url_state.restore("race_choice", int, valid=options)
if st.session_state.get("race_choice") not in options:
    st.session_state["race_choice"] = options[-1]

session_key = st.sidebar.selectbox(
    "Race",
    options,
    format_func=lambda k: labels.get(k, str(k)),
    key="race_choice",
)
race = season_races[season_races.session_key == session_key].iloc[0]


# --- story picker -------------------------------------------------------------

STORIES = ["Story of a Race", "Story of a Driver", "Story of a Team"]
# st.radio rather than st.segmented_control: the pill styling is nicer, but
# Streamlit 1.51's own AppTest harness cannot model a segmented_control's
# state, which breaks every automated check of this page after the first
# render. A testable page is worth more than the pills.
url_state.restore("story_choice", str, valid=STORIES)
story = st.radio(
    "Story", STORIES, horizontal=True, label_visibility="collapsed",
    key="story_choice",
)

st.title(story)
st.caption(
    f"{race.race_name} · {race.circuit}, {race.country} · "
    f"{pd.to_datetime(race.race_date).strftime('%d %B %Y')} · "
    f"{race.circuit_type} circuit"
)


# --- section picker -----------------------------------------------------------
# Every section of the chosen story renders, in order, on one scroll. Each maps
# to a file in DESCRIPTIVE ANALYTICS and answers that file's questions. Sections
# differ by story, since a team has no "gaps" questions and only a driver has a
# teammate.
#
# THE SECTION LIST DOES NOT FILTER, it aims. Nothing is hidden any more, so it
# marks where you are and jumps you where you want to be. See the block below
# for why it is hand-written markup rather than an st.radio.
#
# The hand-over from Diagnose still works. That page sets section_choice
# directly, so arriving here still opens on the section it sent you to, and now
# the rest of the story is visible around it rather than hidden behind it.
#
# Rendering all ten sections costs 1.31s cold and nothing once cached, so there
# was no performance reason to keep them apart.

MODULES = {
    "Story of a Race": story_race,
    "Story of a Driver": story_driver,
    "Story of a Team": story_team,
}
section_pairs = MODULES[story].section_options()
section_titles = {key: title for key, title in section_pairs}
section_keys = list(section_titles)

# Switching story changes which sections exist, and Diagnose can hand over a
# section directly. Fall back to the first section rather than raising when the
# stored key is not valid here.
url_state.restore("section_choice", str, valid=section_keys)
if st.session_state.get("section_choice") not in section_keys:
    st.session_state["section_choice"] = section_keys[0]

st.sidebar.divider()

# THE RADIO IS BACK, AND IT JUMPS. Clicking a section scrolls the page to it.
# That is all it does, and it is all that is left of four attempts at making the
# sidebar track the scroll.
#
# WHAT WAS REMOVED AND WHY. A script that hunted for Base Web's own radio marker
# and drew a copy over it, then hand-written HTML carrying a class and a data
# attribute, then markdown links coloured by inline style. Each passed every
# test here and failed in the browser, differently each time, because each one
# depended on the shape of a DOM this file does not own and cannot inspect from
# where it runs. Following the scroll is deferred rather than left half-working.
#
# WHY THE JUMP IS DIFFERENT. It touches nothing of Streamlit's. It looks up one
# id that this file wrote, on a rerun that has already happened because the
# radio was clicked, and calls scrollIntoView. A console line from the browser
# has already confirmed those ids resolve. It is the one piece that was never
# the thing failing.
section = st.sidebar.radio(
    "Section", section_keys,
    format_func=lambda k: section_titles.get(k, k),
    key="section_choice",
)

st.caption("Every section of this story is below, in order. Pick one in the "
           "sidebar to jump to it, or just scroll.")


def render_story(render_one) -> None:
    """
    Each section in the story's order, behind an invisible scroll target.

    NO HEADING IS ADDED HERE. Every block already opens with its own subheader,
    so adding one printed all 31 titles twice, in two sizes, disagreeing on
    punctuation. The anchor is an empty div instead: it gives the sidebar
    something to scroll to and puts nothing on the page.
    """
    for key, _title in section_pairs:
        st.divider()
        st.markdown(f'<div id="sec-{key}"></div>', unsafe_allow_html=True)
        render_one(key)


if story == "Story of a Race":
    render_story(lambda k: story_race.render(race, k))

elif story == "Story of a Driver":
    # The driver list depends on the race, so it is built here rather than
    # alongside the season and race pickers above.
    entrants = query("""
        SELECT f.driver_number, d.full_name, d.name_acronym, f.team_name,
               f.finish_position
        FROM fact_driver_race f
        JOIN dim_race r ON r.session_key = f.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = f.driver_number AND d.year = r.year
        WHERE f.session_key = ?
        ORDER BY f.finish_position IS NULL, f.finish_position
    """, (int(race.session_key),))

    if entrants.empty:
        st.info("No entrants recorded for this race.")
    else:
        driver_labels = {
            int(r.driver_number):
                f"{r.full_name or ('#' + str(int(r.driver_number)))}"
                f"  ·  {r.team_name}"
            for r in entrants.itertuples()
        }
        driver_options = list(driver_labels)

        # Changing race leaves the previous race's driver selected, who may
        # not have entered this one. Reset before the widget renders.
        url_state.restore("driver_choice", int, valid=driver_options)
        if st.session_state.get("driver_choice") not in driver_options:
            st.session_state["driver_choice"] = driver_options[0]

        driver_number = st.sidebar.selectbox(
            "Driver", driver_options,
            format_func=lambda k: driver_labels.get(k, str(k)),
            key="driver_choice",
        )
        if story_driver.intro(race, driver_number):
            render_story(lambda k: story_driver.render(race, driver_number, k))

else:
    teams = query("""
        SELECT DISTINCT team_name FROM fact_driver_race
        WHERE session_key = ? ORDER BY team_name
    """, (int(race.session_key),))

    if teams.empty:
        st.info("No teams recorded for this race.")
    else:
        team_options = teams.team_name.tolist()

        # Changing race can leave a team selected that did not enter this one
        # (Cadillac joined in 2026). Reset before the widget renders.
        url_state.restore("team_choice", str, valid=team_options)
        if st.session_state.get("team_choice") not in team_options:
            st.session_state["team_choice"] = team_options[0]

        team = st.sidebar.selectbox("Team", team_options, key="team_choice")
        if story_team.intro(race, team):
            render_story(lambda k: story_team.render(race, team, k))

# After every widget, so session_state holds what the reader chose rather than
# what it held on arrival. driver_choice and team_choice are mutually exclusive
# by story; remember() skips whichever is not set rather than writing it empty.
url_state.remember("season_choice", "race_choice", "story_choice",
                   "section_choice", "driver_choice", "team_choice")

st.sidebar.divider()
st.sidebar.caption(
    "Data from the OpenF1 API, rebuilt weekly. Clean laps exclude safety car, "
    "VSC and red flag periods."
)


# --- jump to the chosen section, and a way back to the top ----------------------
# Two things, and nothing else. Everything that tried to read or restyle
# Streamlit's own markup has been taken out.
#
#   clicking the radio    scrolls to that section
#   a button, low right   returns to the top
#
# WHY THIS SHOULD HOLD WHERE THE REST DID NOT. The jump looks up one id that
# render_story wrote and calls scrollIntoView on it. A console line from the
# browser already confirmed those ids resolve, so the only assumption left is
# that a component iframe can reach its parent document, which the same console
# line also confirmed. The button is an element this script creates and owns.
#
# No class names, no data attributes, no stylesheet, no reading of Base Web's
# DOM, and nothing scheduled on the parent window that could outlive the iframe
# and fight the next rerun.
_scroll_to = None
if st.session_state.get("_scrolled_to") != (story, section):
    st.session_state["_scrolled_to"] = (story, section)
    _scroll_to = f"sec-{section}"

# A placeholder rather than an f-string, so the JavaScript stays readable as
# JavaScript: doubling every brace is a silent correctness risk, because a
# mis-escape is valid Python and broken JavaScript.
_JUMP_JS = """
<script>
(function () {
  // CAPTURED ONCE, AND NEVER WRITTEN AS window.parent AGAIN BELOW.
  //
  // This is the bug that broke every version before it, and it broke them from
  // the very first line of the try block, which is why nothing else in here
  // ever got a chance to run.
  //
  // A previous version stored a cleanup closure on the parent document and the
  // next rerun called it. But that closure was written by an iframe that no
  // longer exists, and inside a destroyed iframe `window.parent` is null. So
  // `window.parent.removeEventListener` threw TypeError, on line one, on every
  // rerun. First load worked because there was no previous cleanup to call.
  // Story of a Race looked fine and Driver and Team did not, purely because of
  // which one you happened to load first.
  //
  // parentWin is resolved here, while this iframe is alive, and every reference
  // below uses it. A stale closure holding parentWin still works, because the
  // parent window object outlives the child that captured it.
  const parentWin = window.parent;
  const doc = parentWin.document;
  const scrollTo = __SCROLL__;

  try {
    // Undo the previous run using THIS iframe's live window, rather than
    // asking a dead one to undo itself. Guarded, because a listener that was
    // never added is not an error worth stopping for.
    if (doc.__f1Scroll) {
      try {
        parentWin.removeEventListener("scroll", doc.__f1Scroll, true);
        parentWin.removeEventListener("resize", doc.__f1Scroll);
      } catch (ignored) { /* nothing to remove */ }
      doc.__f1Scroll = null;
    }

    // ONE scrollIntoView IS NOT ENOUGH, and this is why Story of a Driver and
    // Story of a Team did not move while Story of a Race did.
    //
    // A rerun does two things at once: this script scrolls to the section, and
    // Streamlit restores the scroll position the page had before the rerun.
    // Whichever finishes last wins. On the lighter page the jump won; on the
    // heavier ones the restore landed afterwards and put the page back.
    //
    // So the jump is re-asserted for about a second, and stops early once the
    // section is actually at the top, which keeps it from fighting you if you
    // start scrolling yourself. The timers are this iframe's own, NOT
    // window.parent's: a parent timer outlives the iframe that set it, which is
    // how an earlier version left orphaned scripts running against later reruns.
    function jump(id, attempt) {
      const target = doc.getElementById(id);
      if (!target) {
        if (attempt < 8) setTimeout(function () { jump(id, attempt + 1); }, 150);
        else console.warn("F1 jump: no element with id " + id);
        return;
      }
      const top = target.getBoundingClientRect().top;
      // Arrived. Streamlit's header takes the first few dozen pixels, so this
      // is "near the top" rather than "at zero".
      if (attempt > 0 && Math.abs(top) < 80) return;
      target.scrollIntoView({
        behavior: attempt === 0 ? "smooth" : "auto", block: "start",
      });
      if (attempt < 6) setTimeout(function () { jump(id, attempt + 1); }, 160);
    }

    if (scrollTo) jump(scrollTo, 0);

    // Whatever actually scrolls. Streamlit scrolls an inner div on some
    // versions and the document on others, so this asks rather than assumes.
    function scroller() {
      let el = doc.querySelector('[id^="sec-"]');
      el = el ? el.parentElement : null;
      while (el && el !== doc.body) {
        const oy = getComputedStyle(el).overflowY;
        if ((oy === "auto" || oy === "scroll")
            && el.scrollHeight > el.clientHeight + 4) return el;
        el = el.parentElement;
      }
      return doc.scrollingElement || doc.documentElement;
    }

    // Any button left by a previous rerun goes before this one is added.
    doc.querySelectorAll("[data-f1-top]").forEach(function (n) {
      if (n.parentNode) n.parentNode.removeChild(n);
    });

    const toTop = doc.createElement("button");
    toTop.setAttribute("data-f1-top", "1");
    toTop.setAttribute("aria-label", "Back to top");
    toTop.title = "Back to top";
    Object.assign(toTop.style, {
      position: "fixed", right: "24px", bottom: "24px",
      width: "44px", height: "44px", borderRadius: "50%",
      border: "none", background: "#E10600", color: "#FFFFFF",
      cursor: "pointer", zIndex: "999999", opacity: "0",
      pointerEvents: "none", display: "flex", alignItems: "center",
      justifyContent: "center", boxShadow: "0 2px 8px rgba(0,0,0,0.28)",
      transition: "opacity 180ms ease-out",
    });
    // An SVG chevron rather than a character, so no platform can decide to
    // render it as an emoji.
    toTop.innerHTML =
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" '
      + 'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" '
      + 'stroke-linejoin="round"><polyline points="18 15 12 9 6 15">'
      + '</polyline></svg>';
    toTop.addEventListener("click", function () {
      const s = scroller();
      if (s === doc.scrollingElement || s === doc.documentElement) {
        parentWin.scrollTo({top: 0, behavior: "smooth"});
      } else {
        s.scrollTo({top: 0, behavior: "smooth"});
      }
    });
    doc.body.appendChild(toTop);

    function updateButton() {
      const s = scroller();
      const y = (s === doc.scrollingElement || s === doc.documentElement)
                ? (parentWin.scrollY || s.scrollTop) : s.scrollTop;
      const show = y > 400;
      toTop.style.opacity = show ? "1" : "0";
      toTop.style.pointerEvents = show ? "auto" : "none";
    }

    // capture:true because a scroll event on an inner container does not
    // bubble, and Streamlit scrolls a div rather than the document.
    parentWin.addEventListener("scroll", updateButton, true);
    parentWin.addEventListener("resize", updateButton);

    // The HANDLER is stored, not a cleanup closure. The next rerun removes it
    // with its own live window reference, so nothing ever asks a destroyed
    // iframe to tidy up after itself. updateButton keeps working even once
    // this iframe is gone, because everything it touches is the parent's.
    doc.__f1Scroll = updateButton;

    updateButton();
  } catch (err) {
    console.error("F1 jump failed:", err);
  }
})();
</script>
"""

components.html(_JUMP_JS.replace("__SCROLL__", json.dumps(_scroll_to)),
                height=0)

render_footer()
