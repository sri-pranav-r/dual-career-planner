# plan: Next 14 days + pilot logging

**Next 14 days** (athlete sees their own; coach and admin pick an athlete). One page that combines tests,
attendance risk, tournaments and travel, training load (ACWR), recovery against the athlete's own recent
wellness average, injuries, and the taper plan into a day-by-day training and study plan. Every suggestion
names the rule that produced it:

| Rule | What it does |
|---|---|
| P1 | Ease off (RPE 5 or less) the 2 days before a CIE/SEE; test day light |
| P2 | 60 min study block on light timetable days (3 classes or fewer, or Sunday); 90 min if a test is within 7 days |
| P3 | 90 min revision on each of the 3 days before a test |
| P4 | Travel days: rest, and light study only (30 min notes/flashcards) |
| P5 | 2 recovery days after a tournament, plus catch-up study for the classes missed |
| P6 | The taper plan from `features/records` for the week before leaving |
| P7 | Load spike (ACWR high risk: 3 light days; caution: 2 easy days) |
| P8 | Attendance near/below the floor in `settings` (from `features/squad`) |
| P9 | Out injured (from `features/records`) |
| P10 | Competition day: compete, no study |
| P11 | Recovery score (sleep + 6-soreness + 6-stress) 2+ points below the athlete's own average: 2 easy days |

When rules disagree about a day's training, the lighter one wins (competition days excepted). The numbers live
in `logic.RULES` and are placeholders to check with the PED and coaches. Tests missed for a tournament aren't
revised for (they need a make-up). The timetable has no class times, so "gaps" means light days.

**Pilot logging.** When an athlete opens the page, their suggestions are saved (`plan_suggestions`; past days
are frozen, and answered rows are never rewritten). Under the plan they answer Followed / Partly / Didn't follow
for the last 3 days, with an optional reason. **Pilot results** (coach, admin) shows answer rate and adherence
overall, by rule and by athlete, the reasons, and whether training suggestions agree with the training log
(an objective check that doesn't rely on answers). Download as CSV for the report.

Tables: `plan_suggestions`, `plan_followups`. Hooks: `athlete_deleted` (delete rows). Emits nothing.
Faculty don't get these tabs because the plan uses wellness and injury data.

Tests: `python3 -m pytest -q features/plan` (23 tests).
