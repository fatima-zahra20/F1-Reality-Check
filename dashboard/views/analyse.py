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

season = st.sidebar.selectbox(
    "Season", sorted(races.year.unique(), reverse=True), key="season_choice",
)
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
if st.session_state.get("race_choice") not in options:
    st.session_state["race_choice"] = options[0]

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
# THE RADIO NO LONGER FILTERS, it aims. Nothing is hidden any more, so the radio
# picks a target and the link underneath it jumps there, and the same row of
# links sits at the top of the page. That is done with ordinary markdown
# anchors against the anchor ids on each header: a radio cannot scroll a
# Streamlit page by itself, and the alternative is injecting JavaScript that
# reaches into Streamlit's own DOM and breaks on the next version bump.
#
# It also keeps the hand-over from Diagnose working. That page sets
# section_choice directly, so arriving here still points the radio at the
# section it sent you to, and now the rest of the story is visible around it
# rather than hidden behind it.
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
if st.session_state.get("section_choice") not in section_keys:
    st.session_state["section_choice"] = section_keys[0]

st.sidebar.divider()
section = st.sidebar.radio(
    "Section", section_keys,
    format_func=lambda k: section_titles.get(k, k),
    key="section_choice",
)


st.caption("Every section of this story is below. The sidebar scrolls to one.")


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
        if st.session_state.get("team_choice") not in team_options:
            st.session_state["team_choice"] = team_options[0]

        team = st.sidebar.selectbox("Team", team_options, key="team_choice")
        if story_team.intro(race, team):
            render_story(lambda k: story_team.render(race, team, k))

st.sidebar.divider()
st.sidebar.caption(
    "Data from the OpenF1 API, rebuilt weekly. Clean laps exclude safety car, "
    "VSC and red flag periods."
)


# --- the radio and the page follow each other ------------------------------------
# Two behaviours, one script, both JavaScript because a Streamlit radio has no
# native way to move the page and no native way to notice that the page moved.
#
#   clicking the radio  scrolls to that section
#   scrolling the page  moves the radio's red ring to match
#   a button, low right  returns to the top
#
# THE RING THAT MOVES IS A COPY, drawn over the real one. Streamlit owns the
# actual selection, and moving it for real means telling Python and reloading
# the page, which during one scroll of eleven sections is eleven reloads, each
# liable to throw the page back to the top. So instead the script draws a ring
# in the same place and the same size as the marker of whichever section you
# are in, and dims the real marker while the two differ. It reads as one ring
# that slides, and nothing reloads.
#
# The marker is found by measurement, not by selector: the first child of the
# label whose computed border-radius makes it a circle. Base Web's class names
# are generated and change between releases; a round box in the first position
# is a much more stable description of "the radio dot".
#
# If that lookup ever fails the script falls back to a plain dot at the right
# edge of the row, so the tracking degrades rather than disappearing.
#
# All of it reaches out of the component iframe into the host document. Same
# origin, so the browser allows it, but it depends on Streamlit keeping
# components same-origin. If that ever changes, everything here stops and
# nothing else breaks: every section is still on the page, the radio still
# selects one, and the page still scrolls normally.
_scroll_to = None
if st.session_state.get("_scrolled_to") != (story, section):
    st.session_state["_scrolled_to"] = (story, section)
    _scroll_to = f"sec-{section}"

# Placeholders rather than an f-string. Every brace in JavaScript has to be
# doubled inside an f-string, which across two hundred lines is a silent
# correctness risk for no benefit: a mis-escape produces valid Python and
# broken JavaScript, and nothing on the Python side can see it.
_TRACKER_JS = """
<script>
(function () {
  const doc = window.parent.document;
  const pairs = __PAIRS__;
  const scrollTo = __SCROLL__;

  try {
    if (scrollTo) {
      const target = doc.getElementById(scrollTo);
      if (target) target.scrollIntoView({behavior: "smooth", block: "start"});
    }

    // The host page is not reloaded between Streamlit reruns, so without this
    // every rerun leaves another live listener behind.
    if (doc.__f1SpyCleanup) doc.__f1SpyCleanup();
    doc.querySelectorAll("[data-f1-dot]").forEach(function (n) {
      if (n.parentNode) n.parentNode.removeChild(n);
    });

    let rows = [];
    let group = null;
    let ring = null;
    let haveMarkers = false;
    let last = null;

    // The radio's own circle, described by shape rather than by a class name.
    // Base Web generates its class names and changes them between releases; a
    // small round box inside the label is a far more durable description.
    function markerOf(label) {
      for (const el of label.children) {
        const box = el.getBoundingClientRect();
        if (box.width < 6 || box.width > 40) continue;
        if (Math.abs(box.width - box.height) > 3) continue;
        const radius = getComputedStyle(el).borderRadius;
        if (radius && (radius.indexOf("%") > -1
                       || parseFloat(radius) >= box.width / 3)) return el;
      }
      return null;
    }

    function restore() {
      for (const r of rows) {
        if (!r.marker) continue;
        r.marker.style.opacity = "";
        r.marker.style.filter = "";
      }
    }

    // Everything is rebuilt from the live DOM rather than cached, because
    // Streamlit replaces whole subtrees. This is why Driver and Team failed
    // while Race worked: both render another sidebar widget AFTER the radio,
    // so Streamlit re-rendered the sidebar a moment after this script had
    // attached, discarding the ring and every element reference with it.
    function attach() {
      const sidebar = doc.querySelector('[data-testid="stSidebar"]');
      if (!sidebar) return false;

      // Scope the search to the radio group holding our titles, so a label
      // belonging to some other widget can never be picked up.
      let grp = null;
      for (const candidate of sidebar.querySelectorAll('[role="radiogroup"]')) {
        const texts = [];
        candidate.querySelectorAll("label").forEach(function (n) {
          texts.push((n.innerText || "").trim());
        });
        if (pairs.some(function (p) { return texts.indexOf(p[1]) > -1; })) {
          grp = candidate;
          break;
        }
      }
      if (!grp) return false;

      const found = [];
      for (const pair of pairs) {
        const anchor = doc.getElementById("sec-" + pair[0]);
        let label = null;
        grp.querySelectorAll("label").forEach(function (n) {
          if (!label && (n.innerText || "").trim() === pair[1]) label = n;
        });
        if (anchor && label) found.push({anchor: anchor, label: label,
                                         marker: null});
      }
      if (!found.length) return false;

      restore();
      rows = found;
      group = grp;
      for (const r of rows) r.marker = markerOf(r.label);
      haveMarkers = rows.every(function (r) { return !!r.marker; });

      if (getComputedStyle(group).position === "static") {
        group.style.position = "relative";
      }

      ring = doc.createElement("div");
      ring.setAttribute("data-f1-dot", "1");
      Object.assign(ring.style, {
        position: "absolute", opacity: "0", pointerEvents: "none",
        borderRadius: "50%", boxSizing: "border-box",
        transition: "top 180ms ease-out, opacity 180ms ease-out",
      });
      if (haveMarkers) {
        ring.style.border = "2px solid #E10600";
        ring.innerHTML =
          '<div style="position:absolute;inset:3px;border-radius:50%;'
          + 'background:#E10600"></div>';
      } else {
        ring.style.background = "#E10600";
        ring.style.width = "8px";
        ring.style.height = "8px";
        ring.style.right = "2px";
      }
      group.appendChild(ring);

      last = null;
      update();
      return true;
    }

    function update() {
      if (!ring || !rows.length) return;

      // The section you are "in" is the last one whose anchor has passed the
      // top of the viewport. Anything else jitters at section boundaries.
      let best = rows[0];
      for (const r of rows) {
        if (r.anchor.getBoundingClientRect().top <= 120) best = r;
      }
      if (best === last) return;
      last = best;

      const groupBox = group.getBoundingClientRect();
      if (haveMarkers) {
        const box = best.marker.getBoundingClientRect();
        ring.style.width = box.width + "px";
        ring.style.height = box.height + "px";
        ring.style.left = (box.left - groupBox.left) + "px";
        ring.style.top = (box.top - groupBox.top) + "px";

        // EXACTLY ONE RED RING ON SCREEN. The row under the copy is hidden
        // outright because the copy replaces it. The row you last clicked is
        // greyed rather than hidden, so it still reads as a radio option with
        // an empty circle instead of leaving a hole in the list.
        let checked = null;
        for (const r of rows) {
          const input = r.label.querySelector("input");
          if (input && input.checked) { checked = r; break; }
        }
        for (const r of rows) {
          if (r === best) {
            r.marker.style.opacity = "0";
            r.marker.style.filter = "";
          } else if (r === checked) {
            r.marker.style.opacity = "0.35";
            r.marker.style.filter = "grayscale(1)";
          } else {
            r.marker.style.opacity = "";
            r.marker.style.filter = "";
          }
        }
      } else {
        const box = best.label.getBoundingClientRect();
        ring.style.top = (box.top - groupBox.top + (box.height - 8) / 2) + "px";
      }
      ring.style.opacity = "1";
    }

    // Whatever actually scrolls. Streamlit scrolls an inner div on some
    // versions and the document on others, so this asks rather than assumes.
    function scroller() {
      let el = rows.length ? rows[0].anchor.parentElement : null;
      while (el && el !== doc.body) {
        const oy = getComputedStyle(el).overflowY;
        if ((oy === "auto" || oy === "scroll")
            && el.scrollHeight > el.clientHeight + 4) return el;
        el = el.parentElement;
      }
      return doc.scrollingElement || doc.documentElement;
    }

    const toTop = doc.createElement("button");
    toTop.setAttribute("data-f1-dot", "1");
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
        window.parent.scrollTo({top: 0, behavior: "smooth"});
      } else {
        s.scrollTo({top: 0, behavior: "smooth"});
      }
    });
    doc.body.appendChild(toTop);

    function updateButton() {
      const s = scroller();
      const y = (s === doc.scrollingElement || s === doc.documentElement)
                ? (window.parent.scrollY || s.scrollTop) : s.scrollTop;
      const show = y > 400;
      toTop.style.opacity = show ? "1" : "0";
      toTop.style.pointerEvents = show ? "auto" : "none";
    }

    let queued = false;
    function onScroll() {
      if (queued) return;
      queued = true;
      window.parent.requestAnimationFrame(function () {
        queued = false;
        update();
        updateButton();
      });
    }

    attach();

    // Re-attach whenever Streamlit throws our ring away. Cheap: the callback
    // only does a contains() check unless something is actually missing.
    let pending = false;
    const observer = new MutationObserver(function () {
      if (ring && doc.contains(ring) && doc.contains(toTop)) return;
      if (pending) return;
      pending = true;
      window.parent.requestAnimationFrame(function () {
        pending = false;
        if (!doc.contains(toTop)) doc.body.appendChild(toTop);
        if (!ring || !doc.contains(ring)) attach();
      });
    });
    observer.observe(doc.body, {childList: true, subtree: true});

    // And a few plain retries, for the case where the sidebar has not been
    // built yet when this runs and therefore never mutates afterwards.
    const timers = [120, 400, 1000].map(function (ms) {
      return window.parent.setTimeout(function () {
        if (!ring || !doc.contains(ring)) attach();
      }, ms);
    });

    window.parent.addEventListener("scroll", onScroll, true);
    window.parent.addEventListener("resize", onScroll);

    doc.__f1SpyCleanup = function () {
      window.parent.removeEventListener("scroll", onScroll, true);
      window.parent.removeEventListener("resize", onScroll);
      observer.disconnect();
      timers.forEach(function (t) { window.parent.clearTimeout(t); });
      if (ring && ring.parentNode) ring.parentNode.removeChild(ring);
      if (toTop.parentNode) toTop.parentNode.removeChild(toTop);
      restore();
    };

    updateButton();
  } catch (err) {
    // A wrong guess about Streamlit's DOM must not leave the sidebar with
    // markers dimmed and no ring drawn. Undo everything and say so in the
    // console; the page itself keeps working without the tracking.
    console.error("F1 section tracker failed:", err);
    doc.querySelectorAll("[data-f1-dot]").forEach(function (n) {
      if (n.parentNode) n.parentNode.removeChild(n);
    });
    const box = doc.querySelector('[data-testid="stSidebar"]');
    if (box) {
      box.querySelectorAll("label > *").forEach(function (n) {
        if (!n.style) return;
        if (n.style.opacity !== "") n.style.opacity = "";
        if (n.style.filter !== "") n.style.filter = "";
      });
    }
  }
})();
</script>
"""

components.html(
    _TRACKER_JS
    .replace("__PAIRS__", json.dumps(section_pairs))
    .replace("__SCROLL__", json.dumps(_scroll_to)),
    height=0,
)

render_footer()
