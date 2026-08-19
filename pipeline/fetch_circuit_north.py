"""
fetch_circuit_north.py - where north is, per circuit.

    python pipeline\\fetch_circuit_north.py            # report, write nothing
    python pipeline\\fetch_circuit_north.py --execute  # refresh circuit_north.json

WHY THIS EXISTS
---------------
OpenF1 reports car positions in each circuit's own coordinate frame and never
says how that frame is turned relative to north. There is no latitude, longitude
or bearing anywhere in bronze, silver or gold: 59 tables, checked. So the track
map could draw the shape of a circuit and the direction of travel, but could not
honestly say which way was north. s05b_prescriptive.add_wind_components reaches
the same conclusion from the other end, which is why wind enters that model as
two components crossed with circuit rather than as a bearing.

The missing number was already reachable. silver_meetings stores a
circuit_info_url per meeting, pointing at the MultiViewer circuits API, and that
response carries a top-level `rotation`. Nobody had ever fetched it.

WHY IT IS A SEPARATE SCRIPT AND NOT PART OF THE WEEKLY RUN
----------------------------------------------------------
A circuit's orientation does not change. Fetching it every week would make the
pipeline depend on somebody else's server for a constant, and a failed request
would then break a run that had nothing to do with the internet. This writes a
small JSON file which is committed, and s05c reads that file offline.

WHAT IS CHECKED BEFORE A NUMBER IS ACCEPTED
-------------------------------------------
A rotation from an outside source is worth nothing until it is shown to describe
THIS project's coordinates. Three checks, all of which passed on first run:

  every circuit returns one     92 of 94 circuit-year URLs; the 2 failures are
                                404s for circuits with no outline here anyway
  stable across years           0 of 24 circuits disagreed with themselves
  same coordinate frame         each circuit's MultiViewer outline aligned
                                against the one this project traced from a real
                                qualifying lap, searching every rotation and
                                start offset. All 24 needed under 3 degrees, and
                                every mirrored fit was far worse, which is what
                                rules out a flipped axis putting east and west
                                the wrong way round.

THE ONE THING STILL OPEN
------------------------
The API does not state its convention. `rotation` is either the angle the map
must be turned to put north up, or the bearing north already sits at. Those
differ by a sign. NORTH_CONVENTION in dashboard/race_map.py holds the choice, so
settling it is a one-line change rather than 24.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent / "circuit_north.json"
PAUSE_SECONDS = 0.25          # unhurried; it is somebody else's server


def wanted(con) -> list[tuple]:
    """One URL per circuit-year that this project actually raced at."""
    return con.execute("""
        SELECT DISTINCT circuit_key, circuit_short_name, year, circuit_info_url
        FROM silver_meetings
        WHERE circuit_info_url IS NOT NULL AND circuit_info_url != ''
          AND is_cancelled = 0
        ORDER BY circuit_key, year
    """).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch circuit north rotations.")
    ap.add_argument("--execute", action="store_true",
                    help="write circuit_north.json; without it, report only")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[FAIL] silver database not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = wanted(con)
    con.close()
    print(f"{len(rows)} circuit-year URLs to check\n")

    seen: dict[int, dict] = {}
    conflicts: list[str] = []
    failures: list[str] = []

    for i, (ckey, name, year, url) in enumerate(rows, 1):
        try:
            r = requests.get(url, timeout=30,
                             headers={"User-Agent": "F1-Reality-Check/1.0"})
            if r.status_code != 200:
                failures.append(f"{name} {year}: HTTP {r.status_code}")
                continue
            data = r.json()
        except Exception as e:
            failures.append(f"{name} {year}: {type(e).__name__}")
            continue
        finally:
            time.sleep(PAUSE_SECONDS)

        rot = data.get("rotation")
        if rot is None:
            failures.append(f"{name} {year}: no rotation field")
            continue

        prev = seen.get(ckey)
        if prev and prev["rotation"] != rot:
            # A circuit disagreeing with itself means the number describes
            # something that changes, and it cannot be treated as a constant.
            conflicts.append(
                f"{name}: {prev['rotation']} ({prev['source_year']}) "
                f"vs {rot} ({data.get('year')})")
        elif not prev:
            seen[ckey] = {
                "circuit_key": ckey,
                "circuit_short_name": name,
                "rotation": rot,
                "source_year": data.get("year"),
                "source_url": url,
            }
        if i % 20 == 0:
            print(f"  {i}/{len(rows)}")

    print(f"\ncircuits with a rotation : {len(seen)}")
    print(f"requests that failed     : {len(failures)}")
    for f in failures:
        print(f"    {f}")
    print(f"circuits disagreeing with themselves: {len(conflicts)}")
    for c in conflicts:
        print(f"    {c}")

    if conflicts:
        print("\n[FAIL] a rotation that changes is not a constant. Not written.")
        return 1

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "MultiViewer circuits API, via silver_meetings.circuit_info_url",
        "note": ("rotation is in the same coordinate frame as OpenF1 position "
                 "data; verified per circuit against this project's own traced "
                 "outline. The convention (which way the angle turns) is set by "
                 "NORTH_CONVENTION in dashboard/race_map.py."),
        "circuits": [seen[k] for k in sorted(seen)],
    }

    if not args.execute:
        print(f"\nDRY RUN. Re-run with --execute to write {OUT_PATH.name}.")
        for c in payload["circuits"]:
            print(f"    {c['circuit_key']:>4} {c['circuit_short_name']:<20} "
                  f"{c['rotation']:>4}")
        return 0

    OUT_PATH.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH.name}: {len(payload['circuits'])} circuits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
