--DATA PROFILING PHASE 2


-- 2) Should-be-INTEGER columns: what's actually stored?
SELECT typeof(meeting_key) AS t, COUNT(*) FROM meetings GROUP BY t;
SELECT typeof(country_key) AS t, COUNT(*) FROM meetings GROUP BY t;
SELECT typeof(circuit_key) AS t, COUNT(*) FROM meetings GROUP BY t;
SELECT typeof(year)        AS t, COUNT(*) FROM meetings GROUP BY t;


-- 3) Should-be-BOOLEAN
SELECT is_cancelled, COUNT(*) FROM meetings GROUP BY is_cancelled;

-- 4) Should-be-DATETIME: do they parse as ISO 8601?
SELECT
    SUM(datetime(date_start) IS NULL AND date_start IS NOT NULL) AS unparseable_starts,
    SUM(datetime(date_end)   IS NULL AND date_end   IS NOT NULL) AS unparseable_ends,
    MIN(date_start) AS earliest_start,
    MAX(date_end)   AS latest_end
FROM meetings;


SELECT date_start , date_end 
FROM meetings
WHERE date_start > date_end


-- 5) gmt_offset format check — is it "HH:MM:SS", seconds, or mixed?
SELECT gmt_offset, COUNT(*) FROM meetings GROUP BY gmt_offset ORDER BY 2 DESC;

SELECT DISTINCT meeting_name , meeting_official_name , "location" , country_code, country_name
FROM meetings



-- 6) Sample a few full rows to eyeball
SELECT * FROM meetings LIMIT 5



--===========================================================================
--SESSIONS TABLE
--===========================================================================

-- 2) Should-be-INTEGER: verify actual stored types
SELECT typeof(session_key) AS t, COUNT(*) FROM sessions GROUP BY t;
SELECT typeof(meeting_key) AS t, COUNT(*) FROM sessions GROUP BY t;
SELECT typeof(circuit_key) AS t, COUNT(*) FROM sessions GROUP BY t;
SELECT typeof(country_key) AS t, COUNT(*) FROM sessions GROUP BY t;
SELECT typeof(year)        AS t, COUNT(*) FROM sessions GROUP BY t;


-- 3) Enum-like columns: what values appear?
SELECT session_type, COUNT(*) FROM sessions GROUP BY session_type ORDER BY 2 DESC;
SELECT session_name, COUNT(*) FROM sessions GROUP BY session_name ORDER BY 2 DESC;


-- 4) Boolean check
SELECT is_cancelled, COUNT(*) FROM sessions GROUP BY is_cancelled;

-- 5) Dates parse cleanly?
SELECT
    SUM(datetime(date_start) IS NULL AND date_start IS NOT NULL) AS unparseable_starts,
    SUM(datetime(date_end)   IS NULL AND date_end   IS NOT NULL) AS unparseable_ends,
    MIN(date_start) AS earliest,
    MAX(date_end)   AS latest
FROM sessions;

SELECT DISTINCT session_type , session_name FROM sessions 
SELECT * FROM sessions LIMIT 5
--===========================================================================
--DRIVERS TABLE
--===========================================================================

-- 2) Should-be-INTEGER
SELECT typeof(meeting_key)   AS t, COUNT(*) FROM drivers GROUP BY t;
SELECT typeof(session_key)   AS t, COUNT(*) FROM drivers GROUP BY t;
SELECT typeof(driver_number) AS t, COUNT(*) FROM drivers GROUP BY t;

-- 3) Enum-ish check: how many distinct teams, how many distinct drivers?
SELECT COUNT(DISTINCT team_name) AS distinct_teams ,COUNT(DISTINCT driver_number) AS distinct_driver_numbers  ,COUNT(DISTINCT full_name) AS distinct_full_names 
FROM drivers;



-- 4) team_colour format check — should be a hex color like "3671C6"
SELECT team_colour, COUNT(*) FROM drivers GROUP BY team_colour ORDER BY 2 DESC LIMIT 20;


-- 5) name_acronym format — 3 letters?
SELECT LENGTH(name_acronym) AS len, COUNT(*) FROM drivers GROUP BY len;


-- 6) Sample rows
SELECT * FROM drivers LIMIT 5;


SELECT team_name, team_colour, COUNT(*) 
FROM drivers 
WHERE team_colour = '229971'
GROUP BY team_name;



--===========================================================================
--LAPS TABLE
--===========================================================================


-- 2) Should-be-INTEGER
SELECT typeof(session_key)   AS t, COUNT(*) FROM laps GROUP BY t;
SELECT typeof(driver_number) AS t, COUNT(*) FROM laps GROUP BY t;
SELECT typeof(lap_number)    AS t, COUNT(*) FROM laps GROUP BY t;
SELECT typeof(meeting_key)   AS t, COUNT(*) FROM laps GROUP BY t;


-- 3) Should-be-REAL (sector times, speeds)
SELECT typeof(lap_duration)      AS t, COUNT(*) FROM laps WHERE lap_duration IS NOT NULL GROUP BY t;
SELECT typeof(duration_sector_1) AS t, COUNT(*) FROM laps WHERE duration_sector_1 IS NOT NULL GROUP BY t;
SELECT typeof(i1_speed)          AS t, COUNT(*) FROM laps WHERE i1_speed IS NOT NULL GROUP BY t;

-- 4) Boolean check
SELECT is_pit_out_lap, COUNT(*) FROM laps GROUP BY is_pit_out_lap;


-- 5) Sanity ranges on numeric columns (are values plausible?)
SELECT 
    MIN(CAST(lap_duration AS REAL)) AS min_lap,
    MAX(CAST(lap_duration AS REAL)) AS max_lap,
    MIN(CAST(i1_speed AS REAL))     AS min_speed,
    MAX(CAST(st_speed AS REAL))     AS max_speed
FROM laps
WHERE lap_duration IS NOT NULL;

-- 6) date_start parseable?
SELECT
    SUM(datetime(date_start) IS NULL AND date_start IS NOT NULL) AS unparseable,
    MIN(date_start) AS earliest,
    MAX(date_start) AS latest
FROM laps
WHERE date_start IS NOT NULL;


-- 7) Sample a segments value to confirm it's JSON
SELECT segments_sector_1 FROM laps WHERE segments_sector_1 IS NOT NULL LIMIT 3;



--===========================================================================
--STINTS TABLE
--===========================================================================




-- 2) Integer casts
SELECT typeof(session_key)       AS t, COUNT(*) FROM stints GROUP BY t;
SELECT typeof(driver_number)     AS t, COUNT(*) FROM stints GROUP BY t;
SELECT typeof(stint_number)      AS t, COUNT(*) FROM stints GROUP BY t;
SELECT typeof(lap_start)         AS t, COUNT(*) FROM stints GROUP BY t;
SELECT typeof(lap_end)           AS t, COUNT(*) FROM stints GROUP BY t;
SELECT typeof(tyre_age_at_start) AS t, COUNT(*) FROM stints GROUP BY t;


-- 3) Compound is enum-like: what values appear?
SELECT compound, COUNT(*) FROM stints GROUP BY compound ORDER BY 2 DESC;


-- 4) Sanity: stint_number and tyre_age ranges
SELECT
    MIN(CAST(stint_number AS INTEGER)) AS min_stint,
    MAX(CAST(stint_number AS INTEGER)) AS max_stint,
    MIN(CAST(tyre_age_at_start AS INTEGER)) AS min_tyre_age,
    MAX(CAST(tyre_age_at_start AS INTEGER)) AS max_tyre_age
FROM stints;


-- 5) Logical check: lap_end >= lap_start?
SELECT COUNT(*) AS bad_stints
FROM stints
WHERE CAST(lap_end AS INTEGER) < CAST(lap_start AS INTEGER);



--===========================================================================
--PIT TABLE
--===========================================================================




-- 2) Integer casts
SELECT typeof(session_key)   AS t, COUNT(*) FROM pit GROUP BY t;
SELECT typeof(driver_number) AS t, COUNT(*) FROM pit GROUP BY t;
SELECT typeof(lap_number)    AS t, COUNT(*) FROM pit GROUP BY t;


-- 3) REAL casts
SELECT typeof(stop_duration) AS t, COUNT(*) FROM pit WHERE stop_duration IS NOT NULL GROUP BY t;
SELECT typeof(lane_duration) AS t, COUNT(*) FROM pit WHERE lane_duration IS NOT NULL GROUP BY t;
SELECT typeof(pit_duration)  AS t, COUNT(*) FROM pit WHERE pit_duration IS NOT NULL GROUP BY t;


-- 4) Sanity ranges (stop_duration ~2-3s for a good stop, longer under issues)
SELECT
    MIN(CAST(stop_duration AS REAL)) AS min_stop,
    MAX(CAST(stop_duration AS REAL)) AS max_stop,
    MIN(CAST(lane_duration AS REAL)) AS min_lane,
    MAX(CAST(lane_duration AS REAL)) AS max_lane,
    MIN(CAST(pit_duration AS REAL))  AS min_pit,
    MAX(CAST(pit_duration AS REAL))  AS max_pit
FROM pit;


-- 5) date parseable?
SELECT
    SUM(datetime("date") IS NULL AND "date" IS NOT NULL) AS unparseable,
    MIN("date") AS earliest,
    MAX("date") AS latest
FROM pit
WHERE "date" IS NOT NULL;


--===========================================================================
--POSITION TABLE
--===========================================================================



-- 2) Integer casts
SELECT typeof(session_key)   AS t, COUNT(*) FROM position GROUP BY t;
SELECT typeof(driver_number) AS t, COUNT(*) FROM position GROUP BY t;
SELECT typeof("position")    AS t, COUNT(*) FROM position GROUP BY t;


-- 3) Position range sanity (should be 1-20 for F1)
SELECT
    MIN(CAST("position" AS INTEGER)) AS min_pos,
    MAX(CAST("position" AS INTEGER)) AS max_pos
FROM position;


-- 4) Confirm the PK-violation pattern
SELECT COUNT(*) AS duplicate_key_rows
FROM (
    SELECT session_key, driver_number, "date"
    FROM position
    GROUP BY session_key, driver_number, "date"
    HAVING COUNT(*) > 1
);


-- 5) date parseable?
SELECT
    SUM(datetime("date") IS NULL AND "date" IS NOT NULL) AS unparseable,
    MIN("date") AS earliest,
    MAX("date") AS latest
FROM position
WHERE "date" IS NOT NULL;


-- 6) Sample
SELECT * FROM position LIMIT 5;



--===========================================================================
--INTERVALS TABLE
--===========================================================================


-- 2) Integer casts
SELECT typeof(session_key)   AS t, COUNT(*) FROM intervals GROUP BY t;
SELECT typeof(driver_number) AS t, COUNT(*) FROM intervals GROUP BY t;


-- 3) REAL casts — but first check for non-numeric values
--    Anything with letters like "LAP" won't CAST cleanly.
SELECT "interval", COUNT(*)
FROM intervals
WHERE "interval" LIKE '%LAP%' OR "interval" LIKE '%L%'
GROUP BY "interval"
ORDER BY 2 DESC
LIMIT 20;

SELECT gap_to_leader, COUNT(*)
FROM intervals
WHERE gap_to_leader LIKE '%LAP%' OR gap_to_leader LIKE '%L%'
GROUP BY gap_to_leader
ORDER BY 2 DESC
LIMIT 20;


-- 4) Sanity ranges on numeric values only
SELECT
    MIN(CAST("interval" AS REAL))    AS min_interval,
    MAX(CAST("interval" AS REAL))    AS max_interval,
    MIN(CAST(gap_to_leader AS REAL)) AS min_gap,
    MAX(CAST(gap_to_leader AS REAL)) AS max_gap
FROM intervals
WHERE "interval" NOT LIKE '%L%' AND gap_to_leader NOT LIKE '%L%';


-- 5) date parseable?
SELECT
    SUM(datetime("date") IS NULL AND "date" IS NOT NULL) AS unparseable,
    MIN("date") AS earliest,
    MAX("date") AS latest
FROM intervals
WHERE "date" IS NOT NULL;



--===========================================================================
--OVERTAKES TABLE
--===========================================================================


-- 2) Integer casts
SELECT typeof(session_key)              AS t, COUNT(*) FROM overtakes GROUP BY t;
SELECT typeof(overtaking_driver_number) AS t, COUNT(*) FROM overtakes GROUP BY t;
SELECT typeof(overtaken_driver_number)  AS t, COUNT(*) FROM overtakes GROUP BY t;
SELECT typeof("position")               AS t, COUNT(*) FROM overtakes GROUP BY t;


-- 3) Position range
SELECT MIN(CAST("position" AS INTEGER)) AS min_pos,
       MAX(CAST("position" AS INTEGER)) AS max_pos
FROM overtakes;


-- 4) Sanity: overtaking != overtaken (you can't overtake yourself)
SELECT COUNT(*) AS self_overtakes
FROM overtakes
WHERE overtaking_driver_number = overtaken_driver_number;


-- 5) date parseable?
SELECT
    SUM(datetime("date") IS NULL AND "date" IS NOT NULL) AS unparseable,
    MIN("date") AS earliest,
    MAX("date") AS latest
FROM overtakes
WHERE "date" IS NOT NULL;



--===========================================================================
--RACE_CONTROL TABLE
--===========================================================================



-- 2) Integer casts (keys + lap/sector/driver)
SELECT typeof(session_key)   AS t, COUNT(*) FROM race_control GROUP BY t;
SELECT typeof(driver_number) AS t, COUNT(*) FROM race_control WHERE driver_number IS NOT NULL GROUP BY t;
SELECT typeof(lap_number)    AS t, COUNT(*) FROM race_control WHERE lap_number IS NOT NULL GROUP BY t;
SELECT typeof(sector)        AS t, COUNT(*) FROM race_control WHERE sector IS NOT NULL GROUP BY t;


-- 3) Enum-like columns: distinct values
SELECT category,         COUNT(*) FROM race_control GROUP BY category ORDER BY 2 DESC;
SELECT flag,             COUNT(*) FROM race_control GROUP BY flag ORDER BY 2 DESC;
SELECT scope,            COUNT(*) FROM race_control GROUP BY scope ORDER BY 2 DESC;
SELECT qualifying_phase, COUNT(*) FROM race_control GROUP BY qualifying_phase ORDER BY 2 DESC;


-- 4) Confirm no natural PK: even (session_key, date, category, driver_number, sector) may not be unique
SELECT COUNT(*) FROM (
    SELECT session_key, "date", category, driver_number, sector
    FROM race_control
    GROUP BY session_key, "date", category, driver_number, sector
    HAVING COUNT(*) > 1
);


-- 5) date parseable?
SELECT
    SUM(datetime("date") IS NULL AND "date" IS NOT NULL) AS unparseable,
    MIN("date") AS earliest,
    MAX("date") AS latest
FROM race_control
WHERE "date" IS NOT NULL;


--=================================================================
--SESSION_RESULT TABLE
--=================================================================


-- 2) Boolean check
SELECT dnf, COUNT(*) FROM session_result GROUP BY dnf;
SELECT dns, COUNT(*) FROM session_result GROUP BY dns;
SELECT dsq, COUNT(*) FROM session_result GROUP BY dsq;


-- 3) Are duration/gap_to_leader arrays or scalars?
SELECT duration      FROM session_result WHERE duration      IS NOT NULL LIMIT 5;
SELECT gap_to_leader FROM session_result WHERE gap_to_leader IS NOT NULL LIMIT 5;


-- 4) Check for "+N LAP" strings in gap_to_leader
SELECT gap_to_leader, COUNT(*) FROM session_result
WHERE gap_to_leader LIKE '%LAP%'
GROUP BY gap_to_leader
ORDER BY 2 DESC LIMIT 10;


-- 5) Position range
SELECT MIN(CAST("position" AS INTEGER)) AS min_pos,
       MAX(CAST("position" AS INTEGER)) AS max_pos
FROM session_result;


-- 6) Points sanity — should be non-negative
SELECT MIN(CAST(points AS REAL)) AS min_pts, MAX(CAST(points AS REAL)) AS max_pts
FROM session_result WHERE points IS NOT NULL;

SELECT typeof(points) AS t, COUNT(*) FROM session_result WHERE points IS NOT NULL GROUP BY t;

--=================================================================
--STARTING_GRID TABLE
--=================================================================



-- 2) Integer casts
SELECT typeof(session_key)   AS t, COUNT(*) FROM starting_grid GROUP BY t;
SELECT typeof(driver_number) AS t, COUNT(*) FROM starting_grid GROUP BY t;
SELECT typeof("position")    AS t, COUNT(*) FROM starting_grid GROUP BY t;


-- 3) REAL cast for lap_duration
SELECT typeof(lap_duration) AS t, COUNT(*) FROM starting_grid WHERE lap_duration IS NOT NULL GROUP BY t;


-- 4) Position + lap_duration ranges
SELECT
    MIN(CAST("position" AS INTEGER)) AS min_pos,
    MAX(CAST("position" AS INTEGER)) AS max_pos,
    MIN(CAST(lap_duration AS REAL)) AS min_lap,
    MAX(CAST(lap_duration AS REAL)) AS max_lap
FROM starting_grid;


--=================================================================
--TEAM_RADIO TABLE
--=================================================================



-- 2) Integer casts
SELECT typeof(session_key)   AS t, COUNT(*) FROM team_radio GROUP BY t;
SELECT typeof(driver_number) AS t, COUNT(*) FROM team_radio GROUP BY t;


-- 3) date parseable?
SELECT
    SUM(datetime("date") IS NULL AND "date" IS NOT NULL) AS unparseable,
    MIN("date") AS earliest,
    MAX("date") AS latest
FROM team_radio;


-- 4) recording_url uniqueness 
SELECT COUNT(*) - COUNT(DISTINCT recording_url) AS duplicate_urls FROM team_radio;




--=================================================================
--WEATHER TABLE
--=================================================================



-- 2) Integer + REAL casts
SELECT typeof(session_key)       AS t, COUNT(*) FROM weather GROUP BY t;
SELECT typeof(humidity)          AS t, COUNT(*) FROM weather WHERE humidity IS NOT NULL GROUP BY t;
SELECT typeof(pressure)          AS t, COUNT(*) FROM weather WHERE pressure IS NOT NULL GROUP BY t;
SELECT typeof(track_temperature) AS t, COUNT(*) FROM weather WHERE track_temperature IS NOT NULL GROUP BY t;
SELECT typeof(wind_direction)    AS t, COUNT(*) FROM weather WHERE wind_direction IS NOT NULL GROUP BY t;


-- 3) Rainfall values (is it a boolean flag or mm?)
SELECT rainfall, COUNT(*) FROM weather GROUP BY rainfall ORDER BY 2 DESC LIMIT 10;


-- 4) Sanity ranges on numeric columns
SELECT
    MIN(CAST(humidity          AS REAL)) AS min_hum,     MAX(CAST(humidity          AS REAL)) AS max_hum,
    MIN(CAST(pressure          AS REAL)) AS min_pres,    MAX(CAST(pressure          AS REAL)) AS max_pres,
    MIN(CAST(track_temperature AS REAL)) AS min_tt,      MAX(CAST(track_temperature AS REAL)) AS max_tt,
    MIN(CAST(air_temperature   AS REAL)) AS min_at,      MAX(CAST(air_temperature   AS REAL)) AS max_at,
    MIN(CAST(wind_speed        AS REAL)) AS min_ws,      MAX(CAST(wind_speed        AS REAL)) AS max_ws,
    MIN(CAST(wind_direction    AS INTEGER)) AS min_wd,   MAX(CAST(wind_direction    AS INTEGER)) AS max_wd
FROM weather;


-- 5) date parseable?
SELECT
    SUM(datetime("date") IS NULL AND "date" IS NOT NULL) AS unparseable,
    MIN("date") AS earliest,
    MAX("date") AS latest
FROM weather;


--=================================================================
--CHAMPIONSHIP_DRIVERS TABLE
--=================================================================



-- 2) Integer casts
SELECT typeof(session_key)   AS t, COUNT(*) FROM championship_drivers GROUP BY t;
SELECT typeof(driver_number) AS t, COUNT(*) FROM championship_drivers GROUP BY t;
SELECT typeof(position_current) AS t, COUNT(*) FROM championship_drivers WHERE position_current IS NOT NULL GROUP BY t;


-- 3) REAL casts for points
SELECT typeof(points_current) AS t, COUNT(*) FROM championship_drivers WHERE points_current IS NOT NULL GROUP BY t;


-- 4) Sanity ranges
SELECT
    MIN(CAST(position_current AS INTEGER)) AS min_pos, MAX(CAST(position_current AS INTEGER)) AS max_pos,
    MIN(CAST(points_current AS REAL))      AS min_pts, MAX(CAST(points_current AS REAL))      AS max_pts
FROM championship_drivers;


-- 5) PK confirmation
SELECT COUNT(*) FROM (
    SELECT session_key, driver_number
    FROM championship_drivers
    GROUP BY session_key, driver_number
    HAVING COUNT(*) > 1
);


-- 6) Sample
SELECT * FROM championship_drivers LIMIT 5;



--=================================================================
--CHAMPIONSHIP_TEAMS TABLE
--=================================================================


-- 2) Integer/REAL casts
SELECT typeof(session_key) AS t, COUNT(*) FROM championship_teams GROUP BY t;
SELECT typeof(position_current) AS t, COUNT(*) FROM championship_teams WHERE position_current IS NOT NULL GROUP BY t;
SELECT typeof(points_current) AS t, COUNT(*) FROM championship_teams WHERE points_current IS NOT NULL GROUP BY t;


-- 3) Sanity ranges
SELECT
    MIN(CAST(position_current AS INTEGER)) AS min_pos, MAX(CAST(position_current AS INTEGER)) AS max_pos,
    MIN(CAST(points_current AS REAL))      AS min_pts, MAX(CAST(points_current AS REAL))      AS max_pts
FROM championship_teams;


-- 4) PK confirmation
SELECT COUNT(*) FROM (
    SELECT session_key, team_name
    FROM championship_teams
    GROUP BY session_key, team_name
    HAVING COUNT(*) > 1
);


-- 5) Distinct team_name count (should be ~10-11)
SELECT COUNT(DISTINCT team_name) FROM championship_teams;



--=================================================================
--CAR_DATA TABLE
--=================================================================




-- 2) Sanity ranges on a sample (LIMIT keeps this fast)
SELECT
    MIN(CAST(throttle AS INTEGER)) AS min_thr, MAX(CAST(throttle AS INTEGER)) AS max_thr,
    MIN(CAST(brake AS INTEGER))    AS min_brk, MAX(CAST(brake AS INTEGER))    AS max_brk,
    MIN(CAST(rpm AS INTEGER))      AS min_rpm, MAX(CAST(rpm AS INTEGER))      AS max_rpm,
    MIN(CAST(speed AS INTEGER))    AS min_spd, MAX(CAST(speed AS INTEGER))    AS max_spd,
    MIN(CAST(n_gear AS INTEGER))   AS min_g,   MAX(CAST(n_gear AS INTEGER))   AS max_g,
    MIN(CAST(drs AS INTEGER))      AS min_drs, MAX(CAST(drs AS INTEGER))      AS max_drs
FROM car_data;


-- 3) DRS values (should be enum-like: 0=off, 1=off, some 10-14 codes for enabled states)
SELECT drs, COUNT(*) FROM car_data GROUP BY drs ORDER BY 2 DESC;


-- 4) Brake values (0/1 or 0-100?)
SELECT brake, COUNT(*) FROM car_data GROUP BY brake ORDER BY 2 DESC LIMIT 10;


-- 5) PK confirmation on a natural composite
SELECT COUNT(*) FROM (
    SELECT session_key, driver_number, "date"
    FROM car_data
    GROUP BY session_key, driver_number, "date"
    HAVING COUNT(*) > 1
);
-- The 128 gear value  how many rows?
SELECT n_gear, COUNT(*) FROM car_data GROUP BY n_gear ORDER BY 2 DESC;

-- The 104 throttle/brake  are these all one specific value or a range?
SELECT throttle, COUNT(*) FROM car_data WHERE throttle > 100 GROUP BY throttle ORDER BY 2 DESC LIMIT 10;



--=================================================================
--LOCATION TABLE
--=================================================================



-- 2) Coordinate ranges
SELECT
    MIN(CAST(x AS INTEGER)) AS min_x, MAX(CAST(x AS INTEGER)) AS max_x,
    MIN(CAST(y AS INTEGER)) AS min_y, MAX(CAST(y AS INTEGER)) AS max_y,
    MIN(CAST(z AS INTEGER)) AS min_z, MAX(CAST(z AS INTEGER)) AS max_z
FROM location;


-- 3) PK check
SELECT COUNT(*) FROM (
    SELECT session_key, driver_number, "date"
    FROM location
    GROUP BY session_key, driver_number, "date"
    HAVING COUNT(*) > 1
);









