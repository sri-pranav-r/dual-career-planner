# letters: exemption letter status tracker + tournament change alerts

**Letter status tab** (athlete, coach, faculty, admin). Each exemption letter moves
Drafted → PED signed → Proctor signed → HoD approved → Submitted, one step at a time,
with a history of who moved it and when.
- Athlete: sees each tournament's letter, what it is waiting on, marks it submitted.
- PED (coach with no sport) marks PED signed; faculty proctor/teacher marks Proctor signed;
  faculty HoD marks HoD approved; admin can do any step. Staff only see athletes
  `auth.visible_athlete_ids` allows. A "waiting on me" toggle filters to their queue.

**Notifications tab** (athlete). In-app inbox, no SMS/WhatsApp. Filled by hooks:
- `tournament_updated`: every entered athlete is told what changed (old → new). If dates or
  travel days moved, letters already drafted for it are flagged "needs redo" and can't collect
  more signatures until the athlete downloads a fresh one.
- `tournament_created` / `entries_changed`: athletes are told when they are entered or removed
  (deduplicated if core emits both for one save).

- `athlete_deleted`: all of that athlete's letters, history and notifications are deleted.

Tables: `letters_status`, `letters_history`, `letters_notifications`.
Emits: `letters_stage_changed` {letter_id, athlete_id, tournament_id, stage, by}.

For other features: `logic.notify(con, athlete_id, message, kind, tournament_id)` puts anything in
the inbox; `logic.advance_as(con, user, letter_id)` is the permission-checked way to sign/approve,
so a verification or one-click-approval page should call it rather than write the table.
`logic.reject_as(con, user, letter_id, reason)` sends a letter back (same permission as the pending
step); the athlete is notified and must redraft. `logic.status_label(row)` gives one display string.

Tests: `python3 -m pytest -q features/letters` from the planner folder.
