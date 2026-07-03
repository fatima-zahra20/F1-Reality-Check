--THIS IS DATA PROFILING PHASE 1
--P-keys
SELECT COUNT(*) AS c , meeting_key
FROM meetings
GROUP BY meeting_key
HAVING c>1 -- meeting_key is the p-key for meetings 

-------------------------------------

SELECT COUNT(*) AS c ,  session_key ,  driver_number
FROM championship_drivers
GROUP BY   session_key ,  driver_number
HAVING c>1  --  session_key ,  driver_number is the composite p-key for championship_drivers 

-------------------------------------

SELECT COUNT(*) AS c  , session_key , team_name
FROM championship_teams
GROUP BY    session_key , team_name
HAVING c>1 --  session_key , team_name is the composite p-key for championship_teams 

-------------------------------------

SELECT COUNT(*) AS c  , session_key , driver_number
FROM drivers
GROUP BY  session_key , driver_number
HAVING c>1 --  session_key , driver_number is the composite p-key for drivers

-------------------------------------

SELECT COUNT(*) AS c, session_key, driver_number, "date"
FROM intervals 
GROUP BY session_key, driver_number, "date" 
HAVING c > 1 -- session_key, driver_number, "date" is the composite key for intervals

-------------------------------------

SELECT COUNT(*) AS c , driver_number , session_key , lap_number
FROM laps
GROUP BY  driver_number ,session_key , lap_number
HAVING c>1 --  session_key , driver_number , lap_number is the composite p-key for laps

-------------------------------------

SELECT COUNT(*) FROM (
    SELECT session_key, "date", overtaking_driver_number, overtaken_driver_number
    FROM overtakes
    GROUP BY session_key, "date", overtaking_driver_number, overtaken_driver_number
    HAVING COUNT(*) > 1
); -- session_key, "date", overtaking_driver_number, overtaken_driver_number is the composite PK.


-------------------------------------

SELECT COUNT(*) AS c , session_key , driver_number , lap_number
FROM pit 
GROUP BY   session_key , driver_number, lap_number
HAVING c>1  -- session_key , driver_number , lap_number is the composite p-key for pit 

-------------------------------------
SELECT COUNT(*) AS c, session_key, driver_number, "date"
FROM position
GROUP BY session_key, driver_number, "date"
HAVING c > 1  -- Not empty 
-- As expected c>2 is an empty table. 

-------------------------------------

SELECT COUNT(*) AS c , "date"
FROM race_control 
GROUP BY   "date"
HAVING c> 1 -- what identifies a race is a date and a race_control have dupes because controls can happen many times in one race 

-------------------------------------

SELECT COUNT(*) AS c , session_key, driver_number
FROM session_result 
GROUP BY  session_key , driver_number
HAVING c> 1 -- session_key, driver_number is the composite p-key for session_result

-------------------------------------

SELECT COUNT(*) AS c , session_key
FROM sessions 
GROUP BY  session_key 
HAVING c> 1 -- session_key is the p-key for sessions

-------------------------------------

SELECT COUNT(*) AS c , session_key, driver_number
FROM starting_grid 
GROUP BY  session_key , driver_number
HAVING c> 1 --  session_key, driver_number is the composite p-key for starting_grid

-------------------------------------

SELECT COUNT(*) AS c , session_key, driver_number , stint_number , lap_start
FROM stints 
GROUP BY  session_key , driver_number , stint_number , lap_start
HAVING c> 1  -- session_key, driver_number , stint_number , lap_start or end is what identifies a stint 

-------------------------------------

SELECT COUNT(*) AS c, session_key, driver_number, "date"
FROM team_radio
GROUP BY session_key, driver_number, "date"
HAVING c > 1  -- recording_url is unique for team_radio session_key, driver_number, date is the composite PK.

-------------------------------------

SELECT COUNT(*) AS c, session_key, "date"
FROM weather
GROUP BY session_key, "date"
HAVING c > 2  -- c> 2 is emty  finally what identifies a weather is the date (of a count of 2)

-------------------------------------

SELECT COUNT(*) AS c ,session_key, driver_number, "date"
FROM car_data
GROUP BY session_key, driver_number, "date"
HAVING c > 1 -- session_key, driver_number, "date" are the composite pkey for car_data

-------------------------------------

SELECT COUNT(*) AS c ,session_key, driver_number, "date"
FROM location
GROUP BY session_key, driver_number, "date"
HAVING c > 1  -- session_key, driver_number, "date" are the composite pkey for location