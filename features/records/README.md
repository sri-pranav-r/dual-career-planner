# records

Athlete records beyond clashes and letters. Tabs:

| Tab | Who | What |
|---|---|---|
| Calendar | athlete, coach, faculty, admin | Month grid merging classes (from the weekly timetable, inside the semester dates), CIE/SEE/lab tests, logged training, tournaments with travel days, and days out injured (not shown to faculty). Icons only on phones; pick a day for detail. |
| Injuries | athlete, coach, admin | Log an injury (when, body part, what, days out). Each injury gets a return-to-play ramp as % of the athlete's pre-injury weekly load (e.g. 50 → 70 → 85 → 100% after 1-4 weeks out), with logged load compared to target. Coaches get a squad dashboard: who is out or returning, overshoots, injuries by sport and month, most common body parts. |
| Taper plan | athlete, coach, admin | Day-by-day plan for the 7 days before the athlete leaves, from their ACWR zone and trend: high risk = 50% volume and 2 rest days, caution = 60% and 2, sweet spot = 65% and 1, under-trained = 85% and 1. Coach view lists everyone leaving in the next 14 days. |
| Semester summary | athlete, coach, faculty, admin | Per-athlete summary for sports-quota renewal: tournaments, days away, classes and tests missed, letter status, training volume and consistency, ACWR at the end, injuries and wellness (not for faculty). Download as .docx; staff can download everyone as CSV. Dates come from `settings` semester_start/end. |
| Export | coach, faculty, admin | .zip with one CSV per table, filtered to the athletes the user can see. Never includes passwords, login sessions, signing keys or meta. Faculty get no wellness or injury data. |
| Privacy & data | athlete, admin | Consent notice (agree / withdraw), download my data, request deletion. Admin sees consent status for everyone and carries out deletions (type the USN to confirm), which calls `data.delete_athlete`. |

Tables (see `schema.sql`): `records_injuries`, `records_consent` (latest row per athlete wins), `records_deletion_requests` (kept after deletion with the reason wiped, as a record that it happened).

Hooks: handles `athlete_deleted` to remove its own rows. Emits nothing.

Tests: `python3 -m pytest -q features/records`
