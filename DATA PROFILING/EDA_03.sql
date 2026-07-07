-- DATA PROFILING PAHSE 3 
-- Referential Integrity

-- Laps: does every lap belong to a driver actually registered in that session?
SELECT COUNT(*) AS orphan_laps_driver
FROM laps l
LEFT JOIN drivers d ON l.session_key = d.session_key AND l.driver_number = d.driver_number
WHERE d.session_key IS NULL;

-- Stints
SELECT COUNT(*) AS orphan_stints
FROM stints s
LEFT JOIN drivers d ON s.session_key = d.session_key AND s.driver_number = d.driver_number
WHERE d.session_key IS NULL;
--38
-- Pit stops
SELECT COUNT(*) AS orphan_pit
FROM pit p
LEFT JOIN drivers d ON p.session_key = d.session_key AND p.driver_number = d.driver_number
WHERE d.session_key IS NULL;
--8
-- Position snapshots
SELECT COUNT(*) AS orphan_position
FROM position p
LEFT JOIN drivers d ON p.session_key = d.session_key AND p.driver_number = d.driver_number
WHERE d.session_key IS NULL;
--9
-- Intervals
SELECT COUNT(*) AS orphan_intervals
FROM intervals i
LEFT JOIN drivers d ON i.session_key = d.session_key AND i.driver_number = d.driver_number
WHERE d.session_key IS NULL;

-- Overtakes: check BOTH sides
SELECT COUNT(*) AS orphan_overtakers
FROM overtakes o
LEFT JOIN drivers d ON o.session_key = d.session_key AND o.overtaking_driver_number = d.driver_number
WHERE d.session_key IS NULL;

SELECT COUNT(*) AS orphan_overtaken
FROM overtakes o
LEFT JOIN drivers d ON o.session_key = d.session_key AND o.overtaken_driver_number = d.driver_number
WHERE d.session_key IS NULL;

-- Race control (only rows that specify a driver)
SELECT COUNT(*) AS orphan_race_control
FROM race_control r
LEFT JOIN drivers d ON r.session_key = d.session_key AND r.driver_number = d.driver_number
WHERE r.driver_number IS NOT NULL AND d.session_key IS NULL;
--2

-- Starting grid
SELECT COUNT(*) AS orphan_starting_grid
FROM starting_grid g
LEFT JOIN drivers d ON g.session_key = d.session_key AND g.driver_number = d.driver_number
WHERE d.session_key IS NULL;

-- Team radio
SELECT COUNT(*) AS orphan_team_radio
FROM team_radio t
LEFT JOIN drivers d ON t.session_key = d.session_key AND t.driver_number = d.driver_number
WHERE d.session_key IS NULL;

-- Championship drivers
SELECT COUNT(*) AS orphan_champ_drivers
FROM championship_drivers c
LEFT JOIN drivers d ON c.session_key = d.session_key AND c.driver_number = d.driver_number
WHERE d.session_key IS NULL;
--101

-- The 101 orphans in championship_drivers break down as:
--
-- (1) Session 9086 alone contributes 20 orphans (the entire grid). This is the 
--     2023 Imola GP,a real F1 race that was CANCELLED (flooding). The 
--     sessions table correctly marks is_cancelled=True. Championship_drivers 
--     retained the pre-race standings snapshot as if the race would proceed, 
--     but no drivers rows were created since no session actually ran.
--
-- (2) Remaining 81 orphans are spread across ~35 Race and Sprint sessions in 
--     2024-2025, mostly 1-3 per session. These correspond to real F1 
--     mid-season driver absences (Sainz appendicitis, Sargeant dropped for 
--     Colapinto, Magnussen suspended, Perez replaced by Lawson, etc.). 
--     Championship persists standings for absent drivers; drivers table only 
--     lists actual participants.

-- Car data
SELECT COUNT(*) AS orphan_car_data
FROM car_data cd
LEFT JOIN drivers d ON cd.session_key = d.session_key AND cd.driver_number = d.driver_number
WHERE d.session_key IS NULL;
--15392
-- Location
SELECT COUNT(*) AS orphan_location
FROM location l
LEFT JOIN drivers d ON l.session_key = d.session_key AND l.driver_number = d.driver_number
WHERE d.session_key IS NULL;
--25924

-- car_data: 15,392 orphans (0.16% of 9.4M rows)
-- location: 25,924 orphans (0.10% of 25.8M rows)
--
-- Concentrated in Practice and Sprint Qualifying sessions across all seasons.
-- These are telemetry samples for cars driven by test drivers, reserves, or 
-- rookies whose entries in the drivers table were incomplete for that session.
-- OpenF1 telemetry endpoints capture any car on track regardless of official 
-- session registration; the drivers endpoint uses only officially registered 
-- drivers.
--
-- Not a data quality bug. When joining telemetry to drivers for team/name 
-- attribution, use LEFT JOIN and expect ~0.1% of samples to have no team info.


-- ============================================================
-- Referential Integrity  team_name consistency
-- Championship_teams' team_name should match a team present in drivers
-- for the same session. Otherwise the standings reference teams that
-- don't have any driver entries.
-- ============================================================

SELECT COUNT(*) AS orphan_champ_team_names
FROM championship_teams c
LEFT JOIN drivers d ON c.session_key = d.session_key AND c.team_name = d.team_name
WHERE d.session_key IS NULL;

-- Also: session-level checks for tables we haven't yet linked to sessions
SELECT COUNT(*) AS orphan_weather_session
FROM weather w
LEFT JOIN sessions s ON w.session_key = s.session_key
WHERE s.session_key IS NULL;

SELECT COUNT(*) AS orphan_car_data_session
FROM car_data cd
LEFT JOIN sessions s ON cd.session_key = s.session_key
WHERE s.session_key IS NULL;

SELECT COUNT(*) AS orphan_location_session
FROM location l
LEFT JOIN sessions s ON l.session_key = s.session_key
WHERE s.session_key IS NULL;