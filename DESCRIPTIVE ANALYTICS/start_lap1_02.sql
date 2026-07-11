-- The start, lap 1

-- What position did the driver hold after lap 1 vs their grid slot (gained/lost how many places)?
-- Was the driver involved in any lap-1 overtakes (as overtaker or overtaken)?
-- Did any race control flag/incident fire in the opening laps involving this driver?


WITH lap2_start AS (
    SELECT session_key, driver_number, date_start AS lap2_date
    FROM silver_laps
    WHERE lap_number = 2
),
position_after_lap1 AS (
    SELECT p.session_key, p.driver_number, p."position",
           ROW_NUMBER() OVER (
               PARTITION BY p.session_key, p.driver_number 
               ORDER BY p.date DESC
           ) AS rn
    FROM silver_position p
    JOIN lap2_start l2 
        ON p.session_key = l2.session_key 
        AND p.driver_number = l2.driver_number
    WHERE p.date <= l2.lap2_date
)
SELECT 
    s.year, m.meeting_name, s.session_name, d.full_name,
    g."position" AS grid_position,
    pl1."position" AS position_after_lap1,
    g."position" - pl1."position" AS places_gained
FROM position_after_lap1 pl1
JOIN silver_sessions s ON pl1.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d 
    ON pl1.session_key = d.session_key AND pl1.driver_number = d.driver_number
JOIN silver_starting_grid g 
    ON g.meeting_key = m.meeting_key AND g.driver_number = pl1.driver_number
JOIN silver_sessions gs 
    ON g.session_key = gs.session_key AND gs.session_name = 'Sprint Qualifying'-- 'Qualifying'
WHERE pl1.rn = 1
  AND s.session_name = 'Sprint' --'Race'
  AND s.year = 2023 -- 2024
ORDER BY m.date_start, position_after_lap1;


-- Was the driver involved in any lap-1 overtakes (as overtaker or overtaken)?


WITH lap2_start AS (
    SELECT session_key, driver_number, date_start AS lap2_date
    FROM silver_laps
    WHERE lap_number = 2
),
lap1_overtakes AS (
    -- this driver did the overtaking
    SELECT ot.session_key, ot.overtaking_driver_number AS driver_number, ot.overtaken_driver_number AS other_driver_number,'overtook' AS role, ot.date
    FROM silver_overtakes ot
    JOIN lap2_start l2 ON ot.session_key = l2.session_key AND ot.overtaking_driver_number = l2.driver_number
    WHERE ot.date <= l2.lap2_date

    UNION ALL

    -- this driver was overtaken
    SELECT ot.session_key, ot.overtaken_driver_number AS driver_number, ot.overtaking_driver_number AS other_driver_number,'was overtaken by' AS role, ot.date
    FROM silver_overtakes ot
    JOIN lap2_start l2 ON ot.session_key = l2.session_key AND ot.overtaken_driver_number = l2.driver_number
    WHERE ot.date <= l2.lap2_date
)
SELECT s.year, m.meeting_name, s.session_name,d.full_name AS driver, lo.role, d2.full_name AS other_driver
FROM lap1_overtakes lo
JOIN silver_sessions s ON lo.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON lo.session_key = d.session_key AND lo.driver_number = d.driver_number
JOIN silver_drivers d2 ON lo.session_key = d2.session_key AND lo.other_driver_number = d2.driver_number
WHERE s.session_name = 'Sprint'--Race
  AND s.year = 2023
ORDER BY m.date_start, lo.date;


--Did any race control message occur during a driver's lap 1, and if so, what kind?
WITH lap2_start AS (
    SELECT session_key, driver_number, date_start AS lap2_date
    FROM silver_laps
    WHERE lap_number = 2
)
SELECT s.year, m.meeting_name, s.session_name,d.full_name ,rc.category, rc.flag, rc.scope, rc.message, rc.date
FROM lap2_start l2
JOIN silver_sessions s ON s.session_key = l2.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d  ON l2.session_key = d.session_key AND l2.driver_number = d.driver_number
JOIN silver_race_control rc ON rc.session_key = l2.session_key
    AND rc.date <= l2.lap2_date
    AND (rc.driver_number = l2.driver_number OR rc.driver_number IS NULL)
WHERE s.session_name = 'Sprint'
  AND s.year = 2023
ORDER BY m.date_start, d.full_name, rc.date;



