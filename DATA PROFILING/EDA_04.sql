-- DATA PROFILING PPHASE 4 
-- Completness: How much is missing & why?
 

--===========================================================================
--MEETINGS TABLE 
--===========================================================================
PRAGMA table_info('meetings')

SELECT
    COUNT(*) AS total_rows,
    SUM(meeting_key           IS NULL) AS null_meeting_key,
    SUM(meeting_name          IS NULL) AS null_meeting_name,
    SUM(meeting_official_name IS NULL) AS null_meeting_official_name,
    SUM("location"            IS NULL) AS null_location,
    SUM(country_key           IS NULL) AS null_country_key,
    SUM(country_code          IS NULL) AS null_country_code,
    SUM(country_name          IS NULL) AS null_country_name,
    SUM(country_flag          IS NULL) AS null_country_flag,
    SUM(circuit_key           IS NULL) AS null_circuit_key,
    SUM(circuit_short_name    IS NULL) AS null_circuit_short_name,
    SUM(circuit_type          IS NULL) AS null_circuit_type,
    SUM(circuit_info_url      IS NULL) AS null_circuit_info_url,
    SUM(circuit_image         IS NULL) AS null_circuit_image,
    SUM(gmt_offset            IS NULL) AS null_gmt_offset,
    SUM(date_start            IS NULL) AS null_date_start,
    SUM(date_end              IS NULL) AS null_date_end,
    SUM(year                  IS NULL) AS null_year,
    SUM(is_cancelled          IS NULL) AS null_is_cancelled
FROM meetings 
-- meetings / silver_meetings
-- No nulls in any column.


--===========================================================================
--SESSIONS TABLE
--===========================================================================

PRAGMA table_info('sessions')

SELECT
    COUNT(*) AS total_rows,
    SUM(session_key         IS NULL) AS null_session_key,
    SUM(session_type        IS NULL) AS null_session_type,
    SUM(session_name        IS NULL) AS null_session_name,
    SUM(date_start          IS NULL) AS null_date_start,
    SUM(date_end            IS NULL) AS null_date_end,
    SUM(meeting_key         IS NULL) AS null_meeting_key,
    SUM(circuit_key         IS NULL) AS null_circuit_key,
    SUM(circuit_short_name  IS NULL) AS null_circuit_short_name,
    SUM(country_key         IS NULL) AS null_country_key,
    SUM(country_code        IS NULL) AS null_country_code,
    SUM(country_name        IS NULL) AS null_country_name,
    SUM("location"           IS NULL) AS null_location,
    SUM(gmt_offset          IS NULL) AS null_gmt_offset,
    SUM(year                IS NULL) AS null_year,
    SUM(is_cancelled        IS NULL) AS null_is_cancelled
FROM sessions
-- sessions / silver_sessions
-- No nulls in any column




--===========================================================================
--DRIVERS TABLE
--===========================================================================

PRAGMA table_info('drivers')

SELECT
    COUNT(*) AS total_rows,
    SUM(meeting_key     IS NULL) AS null_meeting_key,
    SUM(session_key     IS NULL) AS null_session_key,
    SUM(driver_number   IS NULL) AS null_driver_number,
    SUM(broadcast_name  IS NULL) AS null_broadcast_name,
    SUM(full_name       IS NULL) AS null_full_name,
    SUM(name_acronym    IS NULL) AS null_name_acronym,
    SUM(team_name       IS NULL) AS null_team_name,
    SUM(team_colour     IS NULL) AS null_team_colour,
    SUM(first_name      IS NULL) AS null_first_name,
    SUM(last_name       IS NULL) AS null_last_name,
    SUM(headshot_url    IS NULL) AS null_headshot_url,
    SUM(country_code    IS NULL) AS null_country_code
FROM drivers;-- / silver_drivers


-- Are the 14 nulls all the same rows? (Same rows null in every name column)
SELECT COUNT(*) 
FROM silver_drivers -- / drivers
WHERE team_name IS NULL 
  AND team_colour IS NULL 
  AND first_name IS NULL 
  AND last_name IS NULL 
  AND headshot_url IS NULL
  AND country_code IS NULL; -- 14

 -- Who are the 14 mystery driver entries?
SELECT session_key, driver_number, team_name , team_colour , first_name , last_name , headshot_url, country_code
FROM drivers
WHERE team_name IS NULL --9223


-- What sessions are these from? Pre-season testing?
SELECT s.session_name, s.session_type, s.year, COUNT(*) AS n
FROM drivers d
JOIN sessions s ON d.session_key = s.session_key
WHERE d.team_name IS NULL
GROUP BY s.session_name, s.session_type, s.year;




-- Do headshot nulls concentrate on specific drivers?
SELECT team_name, COUNT(*) AS sessions_with_null_headshot
FROM drivers
WHERE headshot_url IS NULL
GROUP BY full_name
ORDER BY 2 DESC



-- What year/team pattern does the country_code nullness follow?
SELECT s.year, COUNT(*) AS total, SUM(d.country_code IS NULL) AS null_country
FROM drivers d
JOIN sessions s ON d.session_key = s.session_key
GROUP BY s.year;


-- Verdict: All three null patterns are systematic, not bugs.
-- (1) 14 name/team nulls = unregistered test drivers in 2023 FP1
-- (2) 347 headshot nulls = reserve/junior drivers without published photos.
-- (3) 5240 country_code nulls = OpenF1 stopped populating this field starting 2025.
-- None are analytical concerns. For nationality analysis across years,
-- build a separate driver : country mapping table instead of relying on this column.




--===========================================================================
--LAPS TABLE
--===========================================================================

PRAGMA table_info('laps')

SELECT
    COUNT(*) AS total_rows,
    SUM(meeting_key       IS NULL) AS null_meeting_key,
    SUM(session_key       IS NULL) AS null_session_key,
    SUM(driver_number     IS NULL) AS null_driver_number,
    SUM(lap_number        IS NULL) AS null_lap_number,
    SUM(date_start        IS NULL) AS null_date_start,
    SUM(duration_sector_1 IS NULL) AS null_s1,
    SUM(duration_sector_2 IS NULL) AS null_s2,
    SUM(duration_sector_3 IS NULL) AS null_s3,
    SUM(i1_speed          IS NULL) AS null_i1_speed,
    SUM(i2_speed          IS NULL) AS null_i2_speed,
    SUM(is_pit_out_lap    IS NULL) AS null_pit_out,
    SUM(lap_duration      IS NULL) AS null_lap_duration,
    SUM(segments_sector_1 IS NULL) AS null_seg1,
    SUM(segments_sector_2 IS NULL) AS null_seg2,
    SUM(segments_sector_3 IS NULL) AS null_seg3,
    SUM(st_speed          IS NULL) AS null_st_speed
FROM laps; -- / silver_laps

SELECT s.session_name, s.session_type, s.year,l.date_start , COUNT(*) AS n
FROM laps l
JOIN sessions s ON l.session_key = s.session_key
WHERE l.date_start IS NULL 
GROUP BY s.session_type

SELECT s.session_name, s.session_type, s.year, COUNT(*) AS n
FROM laps l
JOIN sessions s ON l.session_key = s.session_key
WHERE l.duration_sector_1 IS NULL AND l.duration_sector_2 IS NULL AND l.duration_sector_3 IS NULL
  AND l.i1_speed IS NULL AND l.i2_speed IS NULL AND l.st_speed IS NULL
  AND l.lap_duration IS NULL AND l.segments_sector_1 IS NULL
GROUP BY s.session_name, s.session_type, s.year
ORDER BY 4 DESC;

-- All null patterns explainable by F1 timing feed behavior. None are bugs.
-- For pace analysis, filter with `WHERE lap_duration IS NOT NULL` to exclude 
-- incomplete laps. For validity-independent analysis (raw speed traps), use 
-- iN_speed columns which have their own null pattern.





--===========================================================================
--STINTS TABLE
--===========================================================================

PRAGMA table_info('stints')

SELECT
    COUNT(*) AS total_rows,
    SUM(meeting_key        IS NULL) AS null_meeting_key,
    SUM(session_key        IS NULL) AS null_session_key,
    SUM(stint_number       IS NULL) AS null_stint_number,
    SUM(driver_number      IS NULL) AS null_driver_number,
    SUM(lap_start          IS NULL) AS null_lap_start,
    SUM(lap_end            IS NULL) AS null_lap_end,
    SUM(compound           IS NULL) AS null_compound,
    SUM(tyre_age_at_start  IS NULL) AS null_tyre_age
FROM stints; -- silver_stints

SELECT COUNT(*) FROM stints WHERE lap_start IS NULL AND lap_end IS NULL;
-- 106 same rows , lap never started and never ends 
SELECT COUNT(*) FROM stints WHERE tyre_age_at_start IS NULL AND lap_start IS NULL;
-- 0 



--===========================================================================
--PIT TABLE
--===========================================================================


PRAGMA table_info('pit')

SELECT
    COUNT(*) AS total_rows,
    SUM("date"        IS NULL) AS null_date,
    SUM(session_key   IS NULL) AS null_session_key,
    SUM(meeting_key   IS NULL) AS null_meeting_key,
    SUM(driver_number IS NULL) AS null_driver_number,
    SUM(lap_number    IS NULL) AS null_lap_number,
    SUM(stop_duration IS NULL) AS null_stop_duration,
    SUM(lane_duration IS NULL) AS null_lane_duration,
    SUM(pit_duration  IS NULL) AS null_pit_duration
FROM pit;
-- pit_duration should be removed 
-- Practical guidance: for pit stop timing analysis, prefer lane_duration.
-- stop_duration The stationary pit stop time, in seconds. This field is only available from the 2024 US GP onwards.




--===========================================================================
--POSITION TABLE
--===========================================================================

PRAGMA table_info('position')

SELECT
    COUNT(*) AS total_rows,
    SUM("date"        IS NULL) AS null_date,
    SUM(session_key   IS NULL) AS null_session_key,
    SUM(meeting_key   IS NULL) AS null_meeting_key,
    SUM(driver_number IS NULL) AS null_driver_number,
    SUM("position"    IS NULL) AS null_position
FROM silver_position; -- position
-- No nulls 



--===========================================================================
--INTERVALS TABLE
--===========================================================================

PRAGMA table_info('intervals')

SELECT
    COUNT(*) AS total_rows,
    SUM("date"        IS NULL) AS null_date,
    SUM(session_key   IS NULL) AS null_session_key,
    SUM(meeting_key   IS NULL) AS null_meeting_key,
    SUM(driver_number IS NULL) AS null_driver_number,
    SUM("interval"    IS NULL) AS null_interval,
    SUM(gap_to_leader IS NULL) AS null_gap_to_leader
FROM intervals;


-- Broader test: for ALL race sessions, count distinct null-interval drivers
SELECT si.session_key, s.session_name, s.year,
       COUNT(DISTINCT si.driver_number) AS n_null_drivers
FROM silver_intervals si
JOIN silver_sessions s ON si.session_key = s.session_key
WHERE si.interval_seconds IS NULL AND si.interval_laps IS NULL
  AND s.session_type = 'Race' AND s.session_name = 'Race'
GROUP BY si.session_key, s.session_name, s.year
ORDER BY n_null_drivers DESC
LIMIT 20;


-- For one race, per driver: total snapshots vs null-snapshot fraction
SELECT session_key, driver_number,
       COUNT(*) AS total_snapshots,
       SUM(CASE WHEN interval_seconds IS NULL AND interval_laps IS NULL THEN 1 ELSE 0 END) AS nulls,
       ROUND(100.0 * SUM(CASE WHEN interval_seconds IS NULL AND interval_laps IS NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS null_pct
FROM silver_intervals
WHERE session_key = 11253    -- one of the 2026 races
GROUP BY session_key, driver_number
ORDER BY null_pct DESC;


-- Verdict on intervals null patterns:
--
-- interval_seconds/laps and gap_to_leader_seconds/laps have ~1-2% null rate per
-- driver, evenly distributed across all drivers in a race. NOT a leadership
-- signal (leaders would show much higher null rates if the docs' claim were the
-- only cause). Nulls appear to be OpenF1 API sampling artifacts ,the gap
-- computation occasionally fails at a given 4Hz snapshot for reasons internal
-- to the timing feed pipeline.
--
-- Practical guidance: filter `WHERE interval_seconds IS NOT NULL` for gap
-- analysis. Don't over-interpret nulls as meaningful race events. The 1-2% loss
-- rate does not meaningfully distort aggregate gap statistics.
--
-- Note: OpenF1 docs claim "null for the race leader" but empirical inspection
-- shows nulls affect the whole grid, not just leaders. Docs are incomplete on
-- this point.





--===========================================================================
--OVERTAKES TABLE
--===========================================================================




PRAGMA table_info('overtakes')

SELECT
    COUNT(*) AS total_rows,
    SUM(session_key              IS NULL) AS null_session_key,
    SUM(meeting_key              IS NULL) AS null_meeting_key,
    SUM(overtaking_driver_number IS NULL) AS null_overtaking,
    SUM(overtaken_driver_number  IS NULL) AS null_overtaken,
    SUM("date"                   IS NULL) AS null_date,
    SUM("position"               IS NULL) AS null_position
FROM silver_overtakes; -- overtakes

-- 0 Nulls 

--===========================================================================
--RACE_CONTROL TABLE
--===========================================================================

PRAGMA table_info('race_control')


SELECT
    COUNT(*) AS total_rows,
    SUM("date"           IS NULL) AS null_date,
    SUM(session_key      IS NULL) AS null_session_key,
    SUM(meeting_key      IS NULL) AS null_meeting_key,
    SUM(driver_number    IS NULL) AS null_driver_number,
    SUM(lap_number       IS NULL) AS null_lap_number,
    SUM(category         IS NULL) AS null_category,
    SUM(flag             IS NULL) AS null_flag,
    SUM(scope            IS NULL) AS null_scope,
    SUM(sector           IS NULL) AS null_sector,
    SUM(qualifying_phase IS NULL) AS null_qualifying_phase,
    SUM(message          IS NULL) AS null_message
FROM race_control;

-- Confirm:  ALL flag nulls also scope nulls
SELECT COUNT(*) FROM race_control WHERE flag IS NULL AND scope IS NOT NULL;

SELECT COUNT(*) FROM race_control WHERE flag IS NOT NULL AND scope IS NULL;
-- 0 (if flag is populated, scope should be too)





--=================================================================
--SESSION_RESULT TABLE
--=================================================================

PRAGMA table_info('session_result')

SELECT
    COUNT(*) AS total_rows,
    SUM(session_key    IS NULL) AS null_session_key,
    SUM(meeting_key    IS NULL) AS null_meeting_key,
    SUM(driver_number  IS NULL) AS null_driver_number,
    SUM("position"     IS NULL) AS null_position,
    SUM(number_of_laps IS NULL) AS null_number_of_laps,
    SUM(dnf            IS NULL) AS null_dnf,
    SUM(dns            IS NULL) AS null_dns,
    SUM(dsq            IS NULL) AS null_dsq,
    SUM(duration       IS NULL) AS null_duration,
    SUM(gap_to_leader  IS NULL) AS null_gap_to_leader,
    SUM(points         IS NULL) AS null_points
FROM session_result;

-- Are the 267 position nulls all DNS drivers?
SELECT COUNT(*) FROM session_result WHERE "position" IS NULL AND dns = 'True';
-- 214 21 dns 193 dnf 

-- What are the 53 mystery position-null rows?
SELECT r.*, s.session_type, s.session_name, s.year
FROM session_result r
JOIN sessions s ON r.session_key = s.session_key
WHERE r."position" IS NULL AND r.dns = 'False' AND r.dnf = 'False'
LIMIT 20;


-- Null pattern breakdown:
--
-- (1) position (267 null):
--     - 214 DNS drivers (didn't start → no finishing position)
--     - ~10 DSQ drivers (disqualified → position voided)
--     - ~40 mystery rows: mostly Practice sessions (no formal classification),
--       plus 4 NC (Not Classified) drivers who finished the race but didn't 
--       complete 90%+ of race distance (session 9213 = 2023 race with Hamilton 
--       and Leclerc unclassified; sessions 11234 & 11307 in 2026 with drivers 
--       15+ laps down).
--
-- (2) number_of_laps (8 null): DSQ drivers whose lap count was voided.
--
-- (3) duration_race_seconds (~2500 null): non-race rows + DNS/DNF drivers.
--
-- (4) duration_quali_json (~5828 null): all non-qualifying rows.
--
-- (5) gap_to_leader_seconds (~2500 null): race leaders (no gap to themselves)
--     + non-race rows + lapped drivers (recorded in gap_to_leader_laps instead).
--
-- (6) gap_to_leader_laps (~7300 null): only 348 populated — lapped Race finishers.
--
-- (7) gap_to_leader_quali_json (~5828 null): all non-qualifying rows.
--
-- (8) points (5801 null, 76%): only Race and Sprint sessions award points.
--     Practice and Qualifying rows are NULL by design.





--=================================================================
--STARTING_GRID TABLE
--=================================================================

PRAGMA table_info('starting_grid')

SELECT
    COUNT(*) AS total_rows,
    SUM(session_key   IS NULL) AS null_session_key,
    SUM(meeting_key   IS NULL) AS null_meeting_key,
    SUM(driver_number IS NULL) AS null_driver_number,
    SUM("position"    IS NULL) AS null_position,
    SUM(lap_duration  IS NULL) AS null_lap_duration
FROM starting_grid;
-- What positions do the 70 lap_duration nulls occupy?
SELECT "position", COUNT(*) AS n
FROM starting_grid
WHERE lap_duration IS NULL
GROUP BY "position"
ORDER BY 1;

-- Only 1 nullable column: lap_duration (70 nulls, ~4%).
--
-- The lap_duration is the qualifying lap time that earned the driver their 
-- grid slot. 

-- Not a data quality issue, reflects real F1 qualifying complexity.
-- For analyses using lap_duration, filter WHERE lap_duration IS NOT NULL,
-- but note that this excludes some legitimate front-row starters.





--=================================================================
--TEAM_RADIO TABLE
--=================================================================


PRAGMA table_info('team_radio')

SELECT
    COUNT(*) AS total_rows,
    SUM(session_key   IS NULL) AS null_session_key,
    SUM(meeting_key   IS NULL) AS null_meeting_key,
    SUM(driver_number IS NULL) AS null_driver_number,
    SUM("date"        IS NULL) AS null_date,
    SUM(recording_url IS NULL) AS null_recording_url
FROM team_radio;
--0 nulls 




--=================================================================
--WEATHER TABLE
--=================================================================

PRAGMA table_info('weather')

-- 1) Row count + nulls
SELECT
    COUNT(*) AS total_rows,
    SUM("date"            IS NULL) AS null_date,
    SUM(session_key       IS NULL) AS null_session_key,
    SUM(meeting_key       IS NULL) AS null_meeting_key,
    SUM(humidity          IS NULL) AS null_humidity,
    SUM(pressure          IS NULL) AS null_pressure,
    SUM(rainfall          IS NULL) AS null_rainfall,
    SUM(track_temperature IS NULL) AS null_track_temp,
    SUM(air_temperature   IS NULL) AS null_air_temp,
    SUM(wind_speed        IS NULL) AS null_wind_speed,
    SUM(wind_direction    IS NULL) AS null_wind_direction
FROM weather;
-- 0 nulls


--=================================================================
--CHAMPIONSHIP_DRIVERS TABLE
--=================================================================

PRAGMA table_info('championship_drivers')

-- 1) Row count + nulls
SELECT
    COUNT(*) AS total_rows,
    SUM(session_key      IS NULL) AS null_session_key,
    SUM(meeting_key      IS NULL) AS null_meeting_key,
    SUM(driver_number    IS NULL) AS null_driver_number,
    SUM(position_current IS NULL) AS null_pos_current,
    SUM(position_start   IS NULL) AS null_pos_start,
    SUM(points_current   IS NULL) AS null_pts_current,
    SUM(points_start     IS NULL) AS null_pts_start
FROM championship_drivers;


-- Where are the 63 nulls concentrated?
SELECT s.year, s.session_name, s.session_type, COUNT(*) AS n
FROM championship_drivers c
JOIN sessions s ON c.session_key = s.session_key
WHERE c.position_start IS NULL
GROUP BY s.year, s.session_name, s.session_type
ORDER BY s.year, 4 DESC;

SELECT s.session_type, s.session_name, COUNT(*) AS n
FROM championship_drivers c
JOIN sessions s ON c.session_key = s.session_key
GROUP BY s.session_type, s.session_name
ORDER BY 3 DESC;

-- The 63 nulls in position_start are all season-opener rows: no "before"
-- standings existed at the very first race of each year.
--   - 20 nulls in 2023 (season opener: Bahrain)
--   - 20 nulls in 2024 (Bahrain)
--   - 20 nulls in 2025 (Australia)
--   - 3 nulls in 2026 (partial: rookies/reserves without prior season standings)





--=================================================================
--CHAMPIONSHIP_TEAMS TABLE
--=================================================================



PRAGMA table_info('championship_teams')


-- 1) Row count + nulls
SELECT
    COUNT(*) AS total_rows,
    SUM(session_key      IS NULL) AS null_session_key,
    SUM(meeting_key      IS NULL) AS null_meeting_key,
    SUM(team_name        IS NULL) AS null_team_name,
    SUM(position_start   IS NULL) AS null_pos_start,
    SUM(position_current IS NULL) AS null_pos_current,
    SUM(points_start     IS NULL) AS null_pts_start,
    SUM(points_current   IS NULL) AS null_pts_current
FROM championship_teams;


SELECT s.year, s.session_name, COUNT(*) AS n
FROM championship_teams c
JOIN sessions s ON c.session_key = s.session_key
WHERE c.position_start IS NULL
GROUP BY s.year, s.session_name
ORDER BY s.year;

-- Same semantic as championship_drivers: standings snapshots only exist for
-- Race and Sprint sessions. The 30 nulls in position_start are the very first
-- Race of each of 3 completed seasons (2023, 2024, 2025) × 10 teams = 30 rows.





PRAGMA table_info('car_data')


-- 1) Row count + nulls (this will take a moment)
SELECT
    COUNT(*) AS total_rows,
    SUM("date"        IS NULL) AS null_date,
    SUM(session_key   IS NULL) AS null_session_key,
    SUM(meeting_key   IS NULL) AS null_meeting_key,
    SUM(driver_number IS NULL) AS null_driver_number,
    SUM(throttle      IS NULL) AS null_throttle,
    SUM(brake         IS NULL) AS null_brake,
    SUM(rpm           IS NULL) AS null_rpm,
    SUM(speed         IS NULL) AS null_speed,
    SUM(n_gear        IS NULL) AS null_n_gear,
    SUM(drs           IS NULL) AS null_drs
FROM car_data;

-- Fraction of null-DRS rows where the car was stationary
SELECT 
    CASE WHEN CAST(speed AS INTEGER) = 0 THEN 'stationary' ELSE 'moving' END AS state,
    COUNT(*) AS n
FROM car_data
WHERE drs IS NULL
GROUP BY state;

-- Speed distribution of moving-but-null-DRS rows
SELECT 
    CASE 
        WHEN CAST(speed AS INTEGER) < 60 THEN 'pit_speed (0-60 km/h)'
        WHEN CAST(speed AS INTEGER) < 150 THEN 'slow (60-150)'
        WHEN CAST(speed AS INTEGER) < 250 THEN 'medium (150-250)'
        ELSE 'racing_speed (250+)'
    END AS speed_band,
    COUNT(*) AS n
FROM car_data
WHERE drs IS NULL AND CAST(speed AS INTEGER) > 0
GROUP BY speed_band
ORDER BY n DESC;


-- DRS is USED in Sprint Qualifying (F1 rules allow it, drivers activate it 
-- on straights) — so nulls do NOT mean "DRS wasn't in use." Nulls reflect 
-- an inconsistency in OpenF1's telemetry ingestion for this specific session 
-- type. Sprint Qualifying is a newer F1 format (introduced 2023, format 
-- changed multiple times); the DRS-column feed for these sessions is 
-- unreliable at OpenF1's source.


--=================================================================
--LOCATION TABLE
--=================================================================


PRAGMA table_info('location')


-- 1) Row count + nulls
SELECT
    COUNT(*) AS total_rows,
    SUM("date"        IS NULL) AS null_date,
    SUM(session_key   IS NULL) AS null_session_key,
    SUM(meeting_key   IS NULL) AS null_meeting_key,
    SUM(driver_number IS NULL) AS null_driver_number,
    SUM(x IS NULL) AS null_x,
    SUM(y IS NULL) AS null_y,
    SUM(z IS NULL) AS null_z
FROM location;

-- 0 nulls 