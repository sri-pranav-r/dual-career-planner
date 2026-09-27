# squad: make-up tests, bulk letters, squad selection, attendance risk

**Make-up tests** (athlete, coach, faculty, admin). Each CIE / SEE a tournament makes an athlete miss gets a
"Request make-up" button. The student's proctor, class teacher or HoD (in scope) schedules it with a date and
room, reschedules, rejects with a reason, or marks it taken; admin can do all of it. The athlete is told through
the letters inbox (`letters.notify`). Each request downloads as a .docx addressed with the letter template in
`settings` and stamped with the verification QR (`verification.letter_with_qr`). Coaches see a read-only list.
If the tournament moves so the test no longer clashes, or the athlete is dropped, open requests are withdrawn
and the athlete told.

**Squad selection** (coach, admin). Pick an upcoming tournament, or enter proposed dates for a new one, and see
every candidate of that sport with SEE / CIE / lab / class counts, the tests missed, other tournaments that
overlap, load zone, and worst-subject attendance after the trip (no letter assumed). Tick and finalise:
`data.set_entries` for an existing tournament (athletes outside the coach's scope are kept), or
`data.add_tournament` for a new one. Core's events then notify the athletes.

**Bulk letters** (coach, admin). One .zip with every entered athlete's exemption letter from
`letterdoc.make_letter` (falls back to `core.build_letter_docx` + QR until core ships letterdoc) and an
`index.csv`. Optionally records each letter as drafted with `letters.create_letter`, so the PED can sign in
the Letter status tab.

**Attendance risk** (coach, faculty, admin; athletes see their own). Projected end-of-semester attendance per
timetable subject after sport days. Uses `settings`: `attendance_min_pct`, `on_duty_counts_as_present`
(days with an HoD-approved or submitted letter are given back), `max_on_duty_days` (earliest first) and
`semester_start` / `semester_end` (a missing end is start + 120 days; with neither set, a 120-day semester
centred on today is assumed and the tab says so). Sliders add a margin for "near the floor" and an allowance
for non-sport absences. Shows the percentage with all letters approved, and what to do.

Tables: `squad_makeup_requests`, `squad_makeup_history`.
Hooks: `tournament_updated`, `entries_changed` (withdraw stale requests), `athlete_deleted` (delete rows).
Emits: `squad_makeup_changed` {request_id, athlete_id, tournament_id, status, by}.

Tests: `python3 -m pytest -q features/squad` (24 tests, in-memory DB).
