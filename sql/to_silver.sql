--THIS IS SCHEMA MODELING
--=================================================================
--MEETINGS
--=================================================================
DROP TABLE IF EXISTS silver_meetings;

CREATE TABLE silver_meetings (
    meeting_key           INTEGER PRIMARY KEY,
    meeting_name          TEXT    NOT NULL,
    meeting_official_name TEXT    NOT NULL,
    "location"            TEXT    NOT NULL,
    country_key           INTEGER NOT NULL,
    country_code          TEXT    NOT NULL,
    country_name          TEXT    NOT NULL,
    country_flag          TEXT    NOT NULL,
    circuit_key           INTEGER NOT NULL,
    circuit_short_name    TEXT    NOT NULL,
    circuit_type          TEXT    NOT NULL,
    circuit_info_url      TEXT    NOT NULL,
    circuit_image         TEXT    NOT NULL,
    gmt_offset            TEXT    NOT NULL,
    date_start            TEXT    NOT NULL,
    date_end              TEXT    NOT NULL,
    year                  INTEGER NOT NULL,
    is_cancelled          INTEGER NOT NULL CHECK (is_cancelled IN (0, 1))
)

INSERT INTO silver_meetings
SELECT
    CAST(meeting_key AS INTEGER),
    meeting_name,
    meeting_official_name,
    "location",
    CAST(country_key AS INTEGER),
    country_code,
    country_name,
    country_flag,
    CAST(circuit_key AS INTEGER),
    circuit_short_name,
    circuit_type,
    circuit_info_url,
    circuit_image,
    gmt_offset,
    date_start,
    date_end,
    CAST(year AS INTEGER),
    CASE is_cancelled WHEN 'True' THEN 1 WHEN 'False' THEN 0 END
FROM meetings


SELECT COUNT(*) FROM meetings;          
SELECT COUNT(*) FROM silver_meetings;   --100


--=================================================================
--SESSIONS
--=================================================================

DROP TABLE IF EXISTS silver_sessions;
CREATE TABLE silver_sessions (
    session_key         INTEGER PRIMARY KEY,
    session_type        TEXT    NOT NULL CHECK (session_type IN ('Practice', 'Race', 'Qualifying')),
    session_name        TEXT    NOT NULL,
    date_start          TEXT    NOT NULL,
    date_end            TEXT    NOT NULL,
    meeting_key         INTEGER NOT NULL,
    circuit_key         INTEGER NOT NULL,
    circuit_short_name  TEXT    NOT NULL,
    country_key         INTEGER NOT NULL,
    country_code        TEXT    NOT NULL,
    country_name        TEXT    NOT NULL,
    location            TEXT    NOT NULL,
    gmt_offset          TEXT    NOT NULL,
    year                INTEGER NOT NULL,
    is_cancelled        INTEGER NOT NULL CHECK (is_cancelled IN (0, 1)),
    FOREIGN KEY (meeting_key) REFERENCES silver_meetings(meeting_key)
);

INSERT INTO silver_sessions
SELECT
    CAST(session_key AS INTEGER),
    session_type,
    session_name,
    date_start,
    date_end,
    CAST(meeting_key AS INTEGER),
    CAST(circuit_key AS INTEGER),
    circuit_short_name,
    CAST(country_key AS INTEGER),
    country_code,
    country_name,
    location,
    gmt_offset,
    CAST(year AS INTEGER),
    CASE is_cancelled WHEN 'True' THEN 1 WHEN 'False' THEN 0 END
FROM sessions


SELECT COUNT(*) FROM sessions         
SELECT COUNT(*) FROM silver_sessions  -- 490


--=================================================================
--DRIVERS 
--=================================================================
DROP TABLE IF EXISTS silver_drivers;
CREATE TABLE silver_drivers (
    session_key     INTEGER NOT NULL,
    driver_number   INTEGER NOT NULL,
    meeting_key     INTEGER NOT NULL,
    broadcast_name  TEXT,
    full_name       TEXT,
    name_acronym    TEXT,
    team_name       TEXT,
    team_colour     TEXT,
    first_name      TEXT,
    last_name       TEXT,
    headshot_url    TEXT,
    country_code    TEXT,
    PRIMARY KEY (session_key, driver_number)
);

INSERT INTO silver_drivers
SELECT
    CAST(session_key AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(meeting_key AS INTEGER),
    broadcast_name, full_name, name_acronym,
    team_name, team_colour, first_name, last_name,
    headshot_url, country_code
FROM drivers;

SELECT COUNT(*) FROM silver_drivers;



--=================================================================
--LAPS 
--=================================================================


DROP TABLE IF EXISTS silver_laps;

CREATE TABLE silver_laps (
    session_key         INTEGER NOT NULL,
    driver_number       INTEGER NOT NULL,
    lap_number          INTEGER NOT NULL,
    meeting_key         INTEGER NOT NULL,
    date_start          TEXT,
    duration_sector_1   REAL,
    duration_sector_2   REAL,
    duration_sector_3   REAL,
    i1_speed            REAL,
    i2_speed            REAL,
    st_speed            REAL,
    lap_duration        REAL,
    is_pit_out_lap      INTEGER CHECK (is_pit_out_lap IN (0, 1) OR is_pit_out_lap IS NULL),
    segments_sector_1   TEXT,
    segments_sector_2   TEXT,
    segments_sector_3   TEXT,
    PRIMARY KEY (session_key, driver_number, lap_number)
);

INSERT INTO silver_laps
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(lap_number    AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    date_start,
    CAST(duration_sector_1 AS REAL),
    CAST(duration_sector_2 AS REAL),
    CAST(duration_sector_3 AS REAL),
    CAST(i1_speed AS REAL),
    CAST(i2_speed AS REAL),
    CAST(st_speed AS REAL),
    CAST(lap_duration AS REAL),
    CASE is_pit_out_lap WHEN 'True' THEN 1 WHEN 'False' THEN 0 ELSE NULL END,
    segments_sector_1,
    segments_sector_2,
    segments_sector_3
FROM laps;

-- 217692
SELECT COUNT(*) FROM silver_laps;


--=================================================================
--STINTS 
--=================================================================


DROP TABLE IF EXISTS silver_stints;

CREATE TABLE silver_stints (
    session_key         INTEGER NOT NULL,
    driver_number       INTEGER NOT NULL,
    stint_number        INTEGER NOT NULL,
    meeting_key         INTEGER NOT NULL,
    lap_start           INTEGER,
    lap_end             INTEGER,
    compound            TEXT,
    tyre_age_at_start   INTEGER,
    PRIMARY KEY (session_key, driver_number, stint_number)
);

INSERT INTO silver_stints
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(stint_number  AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    CAST(lap_start AS INTEGER),
    CAST(lap_end   AS INTEGER),
    compound,
    CAST(tyre_age_at_start AS INTEGER)
FROM stints;

-- 31033
SELECT COUNT(*) FROM silver_stints;


--=================================================================
--PIT 
--=================================================================

DROP TABLE IF EXISTS silver_pit;

CREATE TABLE silver_pit (
    session_key    INTEGER NOT NULL,
    driver_number  INTEGER NOT NULL,
    lap_number     INTEGER NOT NULL,
    meeting_key    INTEGER NOT NULL,
    "date"         TEXT    NOT NULL,
    stop_duration  REAL,
    lane_duration  REAL,
    pit_duration   REAL,
    PRIMARY KEY (session_key, driver_number, lap_number)
);

INSERT INTO silver_pit
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(lap_number    AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    "date",
    CAST(stop_duration AS REAL),
    CAST(lane_duration AS REAL),
    CAST(pit_duration  AS REAL)
FROM pit;

-- 26791
SELECT COUNT(*) FROM silver_pit;



--=================================================================
--POSITION 
--=================================================================

DROP TABLE IF EXISTS silver_position;

CREATE TABLE silver_position (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key    INTEGER NOT NULL,
    driver_number  INTEGER NOT NULL,
    meeting_key    INTEGER NOT NULL,
    "date"         TEXT    NOT NULL,
    "position"     INTEGER NOT NULL
);

CREATE INDEX idx_position_session_driver_date
    ON silver_position (session_key, driver_number, "date");

INSERT INTO silver_position (session_key, driver_number, meeting_key, "date", "position")
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    "date",
    CAST("position"    AS INTEGER)
FROM position;

-- 281801
SELECT COUNT(*) FROM silver_position;


--=================================================================
--INTERVALS 
--=================================================================


DROP TABLE IF EXISTS silver_intervals;

CREATE TABLE silver_intervals (
    session_key            INTEGER NOT NULL,
    driver_number          INTEGER NOT NULL,
    "date"                 TEXT    NOT NULL,
    meeting_key            INTEGER NOT NULL,
    interval_seconds       REAL,     -- NULL if driver is lapped by car ahead
    interval_laps          INTEGER,  -- NULL if not lapped by car ahead
    gap_to_leader_seconds  REAL,     -- NULL if driver is lapped by leader
    gap_to_leader_laps     INTEGER,  -- NULL if not lapped by leader
    PRIMARY KEY (session_key, driver_number, "date")
);

INSERT INTO silver_intervals
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    "date",
    CAST(meeting_key   AS INTEGER),
    CASE
        WHEN "interval" LIKE '%LAP%' THEN NULL
        ELSE CAST("interval" AS REAL)
    END AS interval_seconds,
    CASE
        WHEN "interval" LIKE '%LAP%'
        THEN CAST(REPLACE(REPLACE(REPLACE("interval", '+', ''), ' LAPS', ''), ' LAP', '') AS INTEGER)
        ELSE NULL
    END AS interval_laps,
    CASE
        WHEN gap_to_leader LIKE '%LAP%' THEN NULL
        ELSE CAST(gap_to_leader AS REAL)
    END AS gap_to_leader_seconds,
    CASE
        WHEN gap_to_leader LIKE '%LAP%'
        THEN CAST(REPLACE(REPLACE(REPLACE(gap_to_leader, '+', ''), ' LAPS', ''), ' LAP', '') AS INTEGER)
        ELSE NULL
    END AS gap_to_leader_laps
FROM intervals;

--  1.875M rows : 1875432
SELECT COUNT(*) FROM silver_intervals;


SELECT interval_laps, COUNT(*) FROM silver_intervals WHERE interval_laps IS NOT NULL GROUP BY interval_laps ORDER BY 1;
SELECT gap_to_leader_laps, COUNT(*) FROM silver_intervals WHERE gap_to_leader_laps IS NOT NULL GROUP BY gap_to_leader_laps ORDER BY 1;



--=================================================================
--OVERTAKES 
--=================================================================
DROP TABLE IF EXISTS silver_overtakes;

CREATE TABLE silver_overtakes (
    session_key              INTEGER NOT NULL,
    "date"                   TEXT    NOT NULL,
    overtaking_driver_number INTEGER NOT NULL,
    overtaken_driver_number  INTEGER NOT NULL,
    meeting_key              INTEGER NOT NULL,
    "position"               INTEGER NOT NULL,
    PRIMARY KEY (session_key, "date", overtaking_driver_number, overtaken_driver_number),
    CHECK (overtaking_driver_number != overtaken_driver_number)
);

INSERT INTO silver_overtakes
SELECT
    CAST(session_key              AS INTEGER),
    "date",
    CAST(overtaking_driver_number AS INTEGER),
    CAST(overtaken_driver_number  AS INTEGER),
    CAST(meeting_key              AS INTEGER),
    CAST("position"               AS INTEGER)
FROM overtakes;

--  20065
SELECT COUNT(*) FROM silver_overtakes;


--=================================================================
--RACE_CONTROL  
--=================================================================



DROP TABLE IF EXISTS silver_race_control;

CREATE TABLE silver_race_control (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key       INTEGER NOT NULL,
    meeting_key       INTEGER NOT NULL,
    "date"            TEXT    NOT NULL,
    driver_number     INTEGER,
    lap_number        INTEGER,
    category          TEXT    NOT NULL CHECK (category IN
                          ('Flag', 'Other', 'SessionStatus', 'Drs', 'SafetyCar', 'CarEvent')),
    flag              TEXT,
    scope             TEXT    CHECK (scope IS NULL OR scope IN ('Sector', 'Driver', 'Track')),
    sector            INTEGER,
    qualifying_phase  INTEGER CHECK (qualifying_phase IS NULL OR qualifying_phase IN (1, 2, 3)),
    message           TEXT    NOT NULL
);

CREATE INDEX idx_race_control_session_date
    ON silver_race_control (session_key, "date");

INSERT INTO silver_race_control (
    session_key, 
	meeting_key, 
	"date", 
	driver_number, 
	lap_number,
    category, 
	flag, 
	scope, 
	sector, 
	qualifying_phase, 
	message
)
SELECT
    CAST(session_key   AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    "date",
    CAST(driver_number AS INTEGER),
    CAST(lap_number    AS INTEGER),
    category,
    flag,
    scope,
    CAST(sector AS INTEGER),
    CAST(qualifying_phase AS INTEGER),
    message
FROM race_control;

-- 19807
SELECT COUNT(*) FROM silver_race_control;


--=================================================================
--SESSION_RESULT  
--=================================================================

DROP TABLE IF EXISTS silver_session_result;

CREATE TABLE silver_session_result (
    session_key            INTEGER NOT NULL,
    driver_number          INTEGER NOT NULL,
    meeting_key            INTEGER NOT NULL,
    "position"             INTEGER,
    number_of_laps         INTEGER,
    dnf                    INTEGER NOT NULL CHECK (dnf IN (0, 1)),
    dns                    INTEGER NOT NULL CHECK (dns IN (0, 1)),
    dsq                    INTEGER NOT NULL CHECK (dsq IN (0, 1)),
    duration               REAL,
    gap_to_leader_seconds  REAL,
    gap_to_leader_laps     INTEGER,
    points                 REAL,
    PRIMARY KEY (session_key, driver_number)
);

INSERT INTO silver_session_result
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    CAST("position"    AS INTEGER),
    CAST(number_of_laps AS INTEGER),
    CASE dnf WHEN 'True' THEN 1 WHEN 'False' THEN 0 END,
    CASE dns WHEN 'True' THEN 1 WHEN 'False' THEN 0 END,
    CASE dsq WHEN 'True' THEN 1 WHEN 'False' THEN 0 END,
    CAST(duration AS REAL),
    CASE
        WHEN gap_to_leader LIKE '%LAP%' THEN NULL
        ELSE CAST(gap_to_leader AS REAL)
    END,
    CASE
        WHEN gap_to_leader LIKE '%LAP%'
        THEN CAST(REPLACE(REPLACE(REPLACE(gap_to_leader, '+', ''), ' LAPS', ''), ' LAP', '') AS INTEGER)
        ELSE NULL
    END,
    CAST(points AS REAL)
FROM session_result;

-- 7660
SELECT COUNT(*) FROM silver_session_result;



--=================================================================
--STARTING_GRID
--=================================================================


DROP TABLE IF EXISTS silver_starting_grid;

CREATE TABLE silver_starting_grid (
    session_key    INTEGER NOT NULL,
    driver_number  INTEGER NOT NULL,
    meeting_key    INTEGER NOT NULL,
    "position"     INTEGER NOT NULL,
    lap_duration   REAL,
    PRIMARY KEY (session_key, driver_number)
);

INSERT INTO silver_starting_grid
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    CAST(meeting_key   AS INTEGER),
    CAST("position"    AS INTEGER),
    CAST(lap_duration  AS REAL)
FROM starting_grid;

--  1814
SELECT COUNT(*) FROM silver_starting_grid;


--=================================================================
--TEAM_RADIO
--=================================================================


DROP TABLE IF EXISTS silver_team_radio;

CREATE TABLE silver_team_radio (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key    INTEGER NOT NULL,
    driver_number  INTEGER NOT NULL,
    "date"         TEXT    NOT NULL,
    meeting_key    INTEGER NOT NULL,
    recording_url  TEXT    NOT NULL,
    UNIQUE (session_key, driver_number, "date")
);

CREATE INDEX idx_team_radio_session_driver
    ON silver_team_radio (session_key, driver_number);

INSERT INTO silver_team_radio (session_key, driver_number, "date", meeting_key, recording_url)
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    "date",
    CAST(meeting_key   AS INTEGER),
    recording_url
FROM team_radio;

-- 15575
SELECT COUNT(*) FROM silver_team_radio;




--=================================================================
--WEATHER
--=================================================================

DROP TABLE IF EXISTS silver_weather;

CREATE TABLE silver_weather (
    session_key         INTEGER NOT NULL,
    "date"              TEXT    NOT NULL,
    meeting_key         INTEGER NOT NULL,
    humidity            REAL    NOT NULL,
    pressure            REAL    NOT NULL,
    rainfall            INTEGER NOT NULL CHECK (rainfall IN (0, 1)),
    track_temperature   REAL    NOT NULL,
    air_temperature     REAL    NOT NULL,
    wind_speed          REAL    NOT NULL,
    wind_direction      INTEGER NOT NULL CHECK (wind_direction BETWEEN 0 AND 360),
    PRIMARY KEY (session_key, "date")
);

INSERT INTO silver_weather
SELECT DISTINCT
    CAST(session_key AS INTEGER),
    "date",
    CAST(meeting_key AS INTEGER),
    CAST(humidity          AS REAL),
    CAST(pressure          AS REAL),
    CAST(rainfall          AS INTEGER),
    CAST(track_temperature AS REAL),
    CAST(air_temperature   AS REAL),
    CAST(wind_speed        AS REAL),
    CAST(wind_direction    AS INTEGER)
FROM weather;

-- 42915
SELECT COUNT(*) FROM silver_weather;


--=================================================================
--CHAMPIONSHIP_DRIVERS 
--=================================================================


DROP TABLE IF EXISTS silver_championship_drivers;

CREATE TABLE silver_championship_drivers (
    session_key       INTEGER NOT NULL,
    driver_number     INTEGER NOT NULL,
    meeting_key       INTEGER NOT NULL,
    position_start    INTEGER,
    position_current  INTEGER NOT NULL,
    points_start      REAL    NOT NULL,
    points_current    REAL    NOT NULL,
    PRIMARY KEY (session_key, driver_number)
);

INSERT INTO silver_championship_drivers
SELECT
    CAST(session_key      AS INTEGER),
    CAST(driver_number    AS INTEGER),
    CAST(meeting_key      AS INTEGER),
    CAST(position_start   AS INTEGER),
    CAST(position_current AS INTEGER),
    CAST(points_start     AS REAL),
    CAST(points_current   AS REAL)
FROM championship_drivers;

-- 2098
SELECT COUNT(*) FROM silver_championship_drivers;



--=================================================================
--CHAMPIONSHIP_TEAMS
--=================================================================

DROP TABLE IF EXISTS silver_championship_teams;

CREATE TABLE silver_championship_teams (
    session_key       INTEGER NOT NULL,
    team_name         TEXT    NOT NULL,
    meeting_key       INTEGER NOT NULL,
    position_start    INTEGER,
    position_current  INTEGER NOT NULL,
    points_start      REAL    NOT NULL,
    points_current    REAL    NOT NULL,
    PRIMARY KEY (session_key, team_name)
);

INSERT INTO silver_championship_teams
SELECT
    CAST(session_key      AS INTEGER),
    team_name,
    CAST(meeting_key      AS INTEGER),
    CAST(position_start   AS INTEGER),
    CAST(position_current AS INTEGER),
    CAST(points_start     AS REAL),
    CAST(points_current   AS REAL)
FROM championship_teams;

-- 1001
SELECT COUNT(*) FROM silver_championship_teams;



--=================================================================
--CAR_DATA TABLE
--=================================================================



DROP TABLE IF EXISTS silver_car_data;

CREATE TABLE silver_car_data (
    session_key    INTEGER NOT NULL,
    driver_number  INTEGER NOT NULL,
    "date"         TEXT    NOT NULL,
    meeting_key    INTEGER NOT NULL,
    throttle       INTEGER NOT NULL,
    brake          INTEGER NOT NULL,
    rpm            INTEGER NOT NULL,
    speed          INTEGER NOT NULL,
    n_gear         INTEGER NOT NULL,
    drs            INTEGER,
    PRIMARY KEY (session_key, driver_number, "date")
);

INSERT INTO silver_car_data
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    "date",
    CAST(meeting_key   AS INTEGER),
    CAST(throttle AS INTEGER),
    CAST(brake    AS INTEGER),
    CAST(rpm      AS INTEGER),
    CAST(speed    AS INTEGER),
    CAST(n_gear   AS INTEGER),
    CAST(drs      AS INTEGER)
FROM car_data;

-- 9M rows :9365942
SELECT COUNT(*) FROM silver_car_data;




--=================================================================
--LOCATION TABLE
--=================================================================





DROP TABLE IF EXISTS silver_location;

CREATE TABLE silver_location (
    session_key    INTEGER NOT NULL,
    driver_number  INTEGER NOT NULL,
    "date"         TEXT    NOT NULL,
    meeting_key    INTEGER NOT NULL,
    x              INTEGER NOT NULL,
    y              INTEGER NOT NULL,
    z              INTEGER NOT NULL,
    PRIMARY KEY (session_key, driver_number, "date")
);

INSERT INTO silver_location
SELECT
    CAST(session_key   AS INTEGER),
    CAST(driver_number AS INTEGER),
    "date",
    CAST(meeting_key   AS INTEGER),
    CAST(x AS INTEGER),
    CAST(y AS INTEGER),
    CAST(z AS INTEGER)
FROM location;

--  25849231
SELECT COUNT(*) FROM silver_location;






