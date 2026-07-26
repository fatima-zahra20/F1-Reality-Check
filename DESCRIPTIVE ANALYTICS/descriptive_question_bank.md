# F1 Reality Check: Question Bank
 
## Data Maturity Model
 
This project's analysis follows the four stages of the analytics maturity model:
 
The descriptive layer is pure facts about what happened in a race,no explanations. 
The diagnostic layer below reuses that same "story of a race" structure, but every question now asks *why*, and leans on statistics (correlation, regression, t-tests, ANOVA, chi-square) rather than a single query returning a fact. Parenthetical notes mark which statistical tool a question is expected to need  kept in as a memory aid, not a rigid prescription.
 
 
## Scope: The Story of a Race, Driver & Team Level
 
Goal: reconstruct the full chronological story of a **race** , once at the **driver** level and once at the **team** level (comparing teammates).
 
### Pre-race, grid & setup
- [x] What grid position did the driver start from, and what lap time earned it?
- [x] Which drivers have a grid position but no recorded qualifying lap time, and how many of those are genuine DNS cases vs. started-but-crashed/red-flagged/deleted-lap cases?
- [x] Team-level: what was the combined grid position of both cars (front-row lockout vs split across the field)?
### The start, lap 1
- [x] How did each driver's position change from their qualifying grid slot to the end of lap 1, and how many places did they gain or lose?
- [x] Was the driver involved in any lap-1 overtakes (as overtaker or overtaken)?
- [x] Did any race control flag/incident fire in the opening laps involving this driver?
### Race pace, lap by lap
- [x] What was the driver's lap time trend across the race (improving, degrading, flat)?
- [x] How does the driver's pace compare to their teammate, lap by lap?
- [x] Were there specific laps with anomalous times (red flag, traffic, mistake), and where do they fall in the race?
- [x] What were the driver's sector strengths/weaknesses (which sector were they consistently fastest/slowest in)?
- [x] What was the driver's fastest lap of the race, and on which lap number/tyre compound did it occur?
### Tyre strategy
- [x] How many stints did the driver run, on which compounds, and for how many laps each?
- [x] What was the driver's tyre age at the start of each stint?
- [x] Team-level: did both cars run the same strategy (compound sequence) or diverge?
### Pit stops
- [x] How many pit stops did the driver make, on which laps?
- [x] What was the stop duration and total lane duration for each stop?
- [x] Did any stop go unusually long (a "disaster stop")?
- [x] Team-level: how does average pit stop duration compare between the two drivers/cars?
### Position dynamics across the race
- [x] How did the driver's position evolve over the full race distance (a position-vs-lap trace)?
- [x] How many total overtakes did the driver make, and how many suffered?
- [x] At what points in the race did the biggest position swings happen (start, pit cycles, restarts)?
### Gaps & race context
- [x] How did the driver's gap to the leader evolve over the race?
- [x] How did the driver's gap to the car ahead/behind (interval) evolve, fighting, isolated, or lapped?
- [x] Was the driver lapped by the leader at any point, and when?
### Incidents & external context
- [x] What race control events (flags, safety car, DRS status) occurred during the driver's race, and did any coincide with their pit stops or position changes?
- [x] Was the driver specifically named in any race control message (penalty, investigation, warning)?
- [x] What were the weather conditions during the race, and did they change mid-race (rain arriving, track drying)?
### Team radio
- [x] How many radio messages were sent for this driver during the race, and at what points do they cluster (may signal key moments even without transcription)?
### Finish & outcome
- [x] What was the final classified position, and how does it compare to grid position (net gain/loss)?
- [x] Did the driver finish, DNF, DNS, or get DSQ'd, and if DNF, at what lap?
- [x] How many points did the driver score?
- [x] What was the gap to the winner at the finish?
- [x] Team-level: combined points haul for the race, and how it moved the constructor standings (`points_start` -> `points_current`).
### Driver vs teammate (team-level lens)
- [x] Who out-qualified whom, and by how much?
- [x] Who scored more points, and by how much?
- [x] Whose race had more incidents/pit stops/lost time?
- [x] Did the two cars' strategies converge or split, and which paid off?



 