-- Pre-race, grid & setup

-- What grid position did the driver start from, and what lap time earned it?
-- Which drivers have a grid position but no recorded qualifying lap time? and why? 
-- Team-level: what was the combined grid position of both cars (front-row lockout vs split across the field)?

-- SPRINT QUALIFYING 
SELECT s.year ,m.meeting_name, s.session_name,
       g.driver_number, d.full_name, g."position", g.lap_duration
FROM silver_starting_grid g
INNER JOIN silver_drivers d 
    ON g.session_key = d.session_key AND g.driver_number = d.driver_number
JOIN silver_sessions s ON g.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
WHERE s.year = 2023 AND (s.session_name = 'Sprint Qualifying' OR s.session_name = 'Sprint Shootout') AND g.lap_duration IS NOT NULL -- not dns 
ORDER BY g.lap_duration ASC; 
-- Fastest Lap 70.622 first position in all meetings for sprint qualifying of 2023 done by Lando NORRIS for São Paulo Grand Prix


SELECT s.year, m.meeting_name, s.session_name,g.driver_number, d.full_name, g."position", g.lap_duration, sr.dns
FROM silver_starting_grid g
INNER JOIN silver_drivers d 
    ON g.session_key = d.session_key AND g.driver_number = d.driver_number
JOIN silver_sessions s ON g.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_session_result sr 
    ON s.session_key = sr.session_key AND g.driver_number = sr.driver_number
WHERE s.year = 2023 
  AND s.session_name = 'Sprint Qualifying' 
  --AND sr.dns = 0
  AND g.lap_duration IS NULL;
-- All these guys started the Sprint Qualifying but never record a lap duration , Fernando ALONSO dns which goes logical. 
-- because : he never legally completed a timed lap before the session was red-flagged. 
-- Lando NORRIS : Because he reached SQ3 but was unable to set a time, he automatically defaulted to 10th on the grid for the Saturday Sprint race. 


SELECT s.year, m.meeting_name, d.team_name,s.session_name,
       MIN(g.position) AS best_position,
       MAX(g.position) AS worst_position,
       MAX(g.position) - MIN(g.position) AS position_gap
FROM silver_starting_grid g
INNER JOIN silver_drivers d 
    ON g.session_key = d.session_key AND g.driver_number = d.driver_number
JOIN silver_sessions s ON g.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
WHERE s.year = 2023 
  AND s.session_name = 'Sprint Qualifying' 
GROUP BY s.session_key, d.team_name
ORDER BY  m.meeting_name, best_position;


-- RACE QUALIFYING 
SELECT s.year ,m.meeting_name, s.session_name,
       g.driver_number, d.full_name, g."position", g.lap_duration
FROM silver_starting_grid g
INNER JOIN silver_drivers d 
    ON g.session_key = d.session_key AND g.driver_number = d.driver_number
JOIN silver_sessions s ON g.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
WHERE s.year = 2023 AND s.session_name = 'Qualifying' AND g.lap_duration IS NOT NULL -- not dns 
ORDER BY g.lap_duration ASC; 
-- Fastest Lap 64.391 first position in all meetings for sprint qualifying of 2023 done by Max VERSTAPPEN for Austrian Grand Prix



SELECT s.year, m.meeting_name, s.session_name,g.driver_number, d.full_name, g."position", g.lap_duration, sr.dns
FROM silver_starting_grid g
INNER JOIN silver_drivers d 
    ON g.session_key = d.session_key AND g.driver_number = d.driver_number
JOIN silver_sessions s ON g.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_session_result sr 
    ON s.session_key = sr.session_key AND g.driver_number = sr.driver_number
WHERE s.year = 2023 
  AND s.session_name = 'Qualifying' 
  AND g.lap_duration IS NULL;
-- All these guys started the Sprint Qualifying but never record a lap duration , Valtteri BOTTAS dns 
-- because : finishing without a recorded time due to track limits
-- Nico HULKENBERG :  Solid in Q1 , Eliminated in Q2 , The Q3 Cutoff, his final flying lap was just 0.062 seconds too slow to beat out Lando Norris (1:31.381), meaning he was eliminated in 12th place





SELECT s.year, m.meeting_name, d.team_name,s.session_name,
       MIN(g.position) AS best_position,
       MAX(g.position) AS worst_position,
       MAX(g.position) - MIN(g.position) AS position_gap
FROM silver_starting_grid g
INNER JOIN silver_drivers d 
    ON g.session_key = d.session_key AND g.driver_number = d.driver_number
JOIN silver_sessions s ON g.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
WHERE s.year = 2023 
  AND s.session_name = 'Qualifying' 
GROUP BY s.session_key, d.team_name
ORDER BY  m.meeting_name, best_position;