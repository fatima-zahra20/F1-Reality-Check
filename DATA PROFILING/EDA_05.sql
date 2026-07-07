-- DATA PROFILING PAHSE 5 
-- Domain Integrity Check
-- For every numeric column, verify values fall within plausible ranges.



-- ============================================================
-- silver_meetings
-- ============================================================

-- Year should be in a known F1 range (2023-2026 for this project)
SELECT MIN(year) AS min_yr, MAX(year) AS max_yr FROM silver_meetings;
-- Yes
-- Meeting_key, country_key, circuit_key should be positive integers
SELECT MIN(meeting_key), MIN(country_key), MIN(circuit_key) FROM silver_meetings;
-- Yes

-- ============================================================
-- silver_sessions
-- ============================================================

-- Year, session_key range
SELECT MIN(year), MAX(year), MIN(session_key), MAX(session_key) FROM silver_sessions;

-- Every session should belong to a meeting
SELECT COUNT(*) AS sessions_missing_meeting_ref
FROM silver_sessions WHERE meeting_key IS NULL;
--0

-- ============================================================
-- silver_drivers
-- ============================================================

-- Driver numbers should be 1-99 (F1 rule) plus 0 for reserves
SELECT MIN(driver_number), MAX(driver_number), COUNT(DISTINCT driver_number) 
FROM silver_drivers;
-- Yes
 
-- Are there any driver_numbers >= 100?
SELECT driver_number, COUNT(*) 
FROM silver_drivers 
WHERE driver_number >= 100 OR driver_number < 0
GROUP BY driver_number;
--Nope

-- ============================================================
-- silver_laps
-- ============================================================

-- Lap number range (F1 races rarely exceed 78 laps, sessions can go higher)
SELECT MIN(lap_number), MAX(lap_number) FROM silver_laps;
-- Max 165


-- Sector times: expected range 15-60s per sector normally, up to 200s under safety car
SELECT MIN(duration_sector_1), MAX(duration_sector_1),
       MIN(duration_sector_2), MAX(duration_sector_2),
       MIN(duration_sector_3), MAX(duration_sector_3)
FROM silver_laps;
--  16.251	121.725	16.915	99.769	15.941	99.74


-- Speeds: 0-380 km/h expected
SELECT MIN(i1_speed), MAX(i1_speed),
       MIN(i2_speed), MAX(i2_speed),
       MIN(st_speed), MAX(st_speed)
FROM silver_laps;

-- Lap duration: min ~60s (Monza qualifying), max is capped by real events (red flag ~3500s)
SELECT MIN(lap_duration), MAX(lap_duration) FROM silver_laps;
-- 60.351	3510.471

-- Consistency: sector sum should approximately equal lap_duration when all present
SELECT COUNT(*) AS mismatch_rows
FROM silver_laps
WHERE duration_sector_1 IS NOT NULL 
  AND duration_sector_2 IS NOT NULL 
  AND duration_sector_3 IS NOT NULL
  AND lap_duration IS NOT NULL
  AND ABS((duration_sector_1 + duration_sector_2 + duration_sector_3) - lap_duration) > 0.5;
-- Small tolerance (0.5s) allows for rounding. Large count = data inconsistency.
--143

-- ============================================================
-- silver_stints
-- ============================================================

-- Stint_number range
SELECT MIN(stint_number), MAX(stint_number) FROM silver_stints;
-- Max 33
-- lap_start / lap_end plausibility
SELECT MIN(lap_start), MAX(lap_start), MIN(lap_end), MAX(lap_end) FROM silver_stints;

-- Tyre age: expected 0-50 laps typically
SELECT MIN(tyre_age_at_start), MAX(tyre_age_at_start) FROM silver_stints;

-- Stints where lap_end < lap_start (already known: 24 rows)
SELECT COUNT(*) FROM silver_stints WHERE lap_end < lap_start;


-- ============================================================
-- silver_pit
-- ============================================================

-- Stop / lane / pit durations. Extreme max values are red flag artifacts.
SELECT MIN(stop_duration), MAX(stop_duration),
       MIN(lane_duration), MAX(lane_duration),
       MIN(pit_duration), MAX(pit_duration)
FROM silver_pit;

-- Any negative durations? Should be impossible.
SELECT COUNT(*) FROM silver_pit
WHERE stop_duration < 0 OR lane_duration < 0 OR pit_duration < 0;


-- ============================================================
-- silver_position
-- ============================================================

-- Position range: should be 1-23 (we saw 23 for testing sessions)
SELECT MIN("position"), MAX("position") FROM silver_position;

-- Any impossible positions (0, negative, absurdly high)?
SELECT COUNT(*) FROM silver_position 
WHERE "position" < 1 OR "position" > 30;
--0

-- ============================================================
-- silver_intervals
-- ============================================================

-- Gap seconds: negative would mean you're ahead of the car ahead (impossible)
SELECT MIN(interval_seconds), MAX(interval_seconds),
       MIN(gap_to_leader_seconds), MAX(gap_to_leader_seconds)
FROM silver_intervals;

-- Any negative gaps?
SELECT COUNT(*) FROM silver_intervals 
WHERE interval_seconds < 0 OR gap_to_leader_seconds < 0;
-- Nope
-- Lap deficits: should be 1+
SELECT MIN(interval_laps), MAX(interval_laps),
       MIN(gap_to_leader_laps), MAX(gap_to_leader_laps)
FROM silver_intervals;


-- ============================================================
-- silver_overtakes
-- ============================================================

-- Position gained by overtaker
SELECT MIN("position"), MAX("position") FROM silver_overtakes;


-- ============================================================
-- silver_starting_grid
-- ============================================================

-- Grid position 1-22
SELECT MIN("position"), MAX("position") FROM silver_starting_grid;

-- Qualifying lap durations
SELECT MIN(lap_duration), MAX(lap_duration) FROM silver_starting_grid;


-- ============================================================
-- silver_session_result
-- ============================================================

-- Position, laps, points
SELECT MIN("position"), MAX("position"), 
       MIN(number_of_laps), MAX(number_of_laps),
       MIN(points), MAX(points)
FROM silver_session_result;

-- Duration seconds: race lengths (5000-8000s typical)
SELECT MIN(duration_race_seconds), MAX(duration_race_seconds) FROM silver_session_result;

-- Any negative points, laps, positions?
SELECT COUNT(*) FROM silver_session_result 
WHERE points < 0 OR number_of_laps < 0 OR "position" < 1;
-- Nope

-- ============================================================
-- silver_weather
-- ============================================================

-- Weather ranges (already CHECK-constrained for rainfall and wind_direction)
SELECT MIN(humidity), MAX(humidity),
       MIN(pressure), MAX(pressure),
       MIN(track_temperature), MAX(track_temperature),
       MIN(air_temperature), MAX(air_temperature),
       MIN(wind_speed), MAX(wind_speed)
FROM silver_weather;


-- ============================================================
-- silver_championship_drivers / teams
-- ============================================================

SELECT MIN(position_current), MAX(position_current),
       MIN(points_current), MAX(points_current)
FROM silver_championship_drivers;

SELECT MIN(position_current), MAX(position_current),
       MIN(points_current), MAX(points_current)
FROM silver_championship_teams;


-- ============================================================
-- silver_car_data
-- ============================================================

-- Throttle: 0-104 known
SELECT MIN(throttle), MAX(throttle) FROM silver_car_data;

-- Brake: 0/100/104 expected
SELECT DISTINCT brake FROM silver_car_data ORDER BY 1;
-- Yes
-- RPM: 0-15000 plausible for F1 engines
SELECT MIN(rpm), MAX(rpm) FROM silver_car_data;

-- Speed: 0-400 km/h
SELECT MIN(speed), MAX(speed) FROM silver_car_data;

-- Gear: 0-8 normally, ~600 rows have 9-128 (known corruption)
SELECT n_gear, COUNT(*) FROM silver_car_data 
WHERE n_gear > 8 OR n_gear < 0
GROUP BY n_gear ORDER BY 2 DESC LIMIT 20;

-- DRS values: 0/1/8/9/10/12/14 known plus nulls
SELECT drs, COUNT(*) FROM silver_car_data GROUP BY drs ORDER BY 2 DESC;


-- ============================================================
-- silver_location
-- ============================================================

-- Coordinates in millimeters. Track widths max ~20m = ±10000mm reasonable.
SELECT MIN(x), MAX(x), MIN(y), MAX(y), MIN(z), MAX(z) FROM silver_location;



