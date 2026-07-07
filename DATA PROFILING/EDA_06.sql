-- DATA PROFILING PHASE 6
-- Cardinality Check
-- Compare distinct value counts to domain expectations.



-- ============================================================
-- silver_meetings
-- ============================================================

-- Meetings per year (should be ~24 races in modern F1, sometimes 22-24)
SELECT year, COUNT(*) AS meetings,
       SUM(CASE WHEN is_cancelled = 1 THEN 1 ELSE 0 END) AS cancelled
FROM silver_meetings
GROUP BY year
ORDER BY year;

-- Distinct circuits (should be ~24 real circuits, plus one for Sakhir testing)
SELECT COUNT(DISTINCT circuit_key) AS distinct_circuits,
       COUNT(DISTINCT circuit_short_name) AS distinct_circuit_names
FROM silver_meetings;

-- Distinct countries
SELECT COUNT(DISTINCT country_code) AS distinct_countries FROM silver_meetings;


-- ============================================================
-- silver_sessions
-- ============================================================

-- Sessions per year (should be ~5 per meeting × meetings + testing days)
SELECT year, session_type, COUNT(*) AS n
FROM silver_sessions
GROUP BY year, session_type
ORDER BY year, session_type;

-- Session names per year (helps spot naming drift like Sprint Shootout vs Sprint Qualifying)
SELECT year, session_name, COUNT(*) AS n
FROM silver_sessions
GROUP BY year, session_name
ORDER BY year, n DESC;


-- ============================================================
-- silver_drivers
-- ============================================================

-- Distinct drivers per year (should be ~22-28 real F1 drivers each year)
SELECT s.year, COUNT(DISTINCT d.driver_number) AS distinct_drivers,
       COUNT(DISTINCT d.full_name) AS distinct_names
FROM silver_drivers d
JOIN silver_sessions s ON d.session_key = s.session_key
GROUP BY s.year
ORDER BY s.year;


-- 2023: which driver_numbers have MULTIPLE distinct full_names?
SELECT d.driver_number, COUNT(DISTINCT d.full_name) AS n_names, 
       GROUP_CONCAT(DISTINCT d.full_name) AS names
FROM silver_drivers d
JOIN silver_sessions s ON d.session_key = s.session_key
WHERE s.year = 2023 AND d.full_name IS NOT NULL
GROUP BY d.driver_number
HAVING COUNT(DISTINCT d.full_name) > 1
ORDER BY 2 DESC;

-- 2024: which driver_numbers have NULL name in some sessions?
SELECT d.driver_number, COUNT(*) AS n_null_name_sessions
FROM silver_drivers d
JOIN silver_sessions s ON d.session_key = s.session_key
WHERE s.year = 2024 AND d.full_name IS NULL
GROUP BY d.driver_number;

-- Distinct teams per year (should be 10-11)
SELECT s.year, COUNT(DISTINCT d.team_name) AS distinct_teams
FROM silver_drivers d
JOIN silver_sessions s ON d.session_key = s.session_key
GROUP BY s.year
ORDER BY s.year;

-- All distinct teams across all years (should surface naming drift)
SELECT team_name, COUNT(DISTINCT s.year) AS years_active
FROM silver_drivers d
JOIN silver_sessions s ON d.session_key = s.session_key
GROUP BY team_name
ORDER BY 2 DESC, team_name;


-- ============================================================
-- silver_stints
-- ============================================================

-- All distinct tyre compounds (should be SOFT/MEDIUM/HARD/INTERMEDIATE/WET plus UNKNOWN/TEST_UNKNOWN)
SELECT compound, COUNT(*) AS n FROM silver_stints GROUP BY compound ORDER BY 2 DESC;


-- ============================================================
-- silver_race_control
-- ============================================================

-- Categories (should be 6: Flag, Other, SessionStatus, Drs, SafetyCar, CarEvent)
SELECT category, COUNT(*) FROM silver_race_control GROUP BY category ORDER BY 2 DESC;

-- Flag values
SELECT flag, COUNT(*) FROM silver_race_control GROUP BY flag ORDER BY 2 DESC;

-- Scope values (should be 3: Sector, Driver, Track)
SELECT scope, COUNT(*) FROM silver_race_control GROUP BY scope ORDER BY 2 DESC;


-- ============================================================
-- silver_car_data
-- ============================================================

-- Distinct DRS values (already saw these but worth documenting)
SELECT drs, COUNT(*) FROM silver_car_data GROUP BY drs ORDER BY 2 DESC;

-- Distinct gears (should be 0-8; anything else is the known corruption)
SELECT n_gear, COUNT(*) FROM silver_car_data 
GROUP BY n_gear
ORDER BY n_gear;


-- ============================================================
-- silver_championship_teams
-- ============================================================

-- All distinct team names across championship (should be same as drivers table minus one-off reserves)
SELECT team_name, COUNT(*) AS session_snapshots
FROM silver_championship_teams
GROUP BY team_name
ORDER BY 2 DESC;


-- Cardinality of drivers by year:
--   2023: 46 driver_numbers, 60 distinct names (mismatch by design, Rookie FP1
--         sessions have junior drivers using regular seat numbers).
--   2024: 35 driver_numbers, 33 names (small discrepancy , likely mid-year 
--         reassignments, not investigated).
--   2025: 36 numbers, 36 names are clean.
--   2026: 32 numbers, 31 names are clean (partial year).
--
-- Interpretation: F1's "Rookie FP1" rule requires teams to run junior drivers 
-- in FP1 twice per season. These rookies use the regular driver's car number
-- for that session, so driver_number alone does not uniquely identify a person
-- across sessions. For driver-level analysis, aggregate by 
-- (session_key, driver_number) and use full_name from silver_drivers.