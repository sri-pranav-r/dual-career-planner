# verification

Lets a proctor, class teacher or HoD check an exemption letter by scanning its QR code, then approve or reject it in one click. Also gives faculty a department view of athletes who will be away, with the CIE / SEE make-up tests to schedule.

- **QR on the letter.** `logic.letter_with_qr(con, docx_bytes, athlete_id, tournament_id)` stamps a QR code and link `<PLANNER_BASE_URL>/?verify=<token>` on the .docx. The token holds athlete, tournament and away window, HMAC-signed with a per-database secret, so an edited or invented letter fails.
- **Verify page** (public route `verify`). It shows the live, PED-confirmed tournament, dates and entry, what the student misses, and the letter status. It warns if the dates moved since printing, the athlete was dropped, or the PED hasn't signed.
- **Approve / reject.** Signed-in faculty in scope see the buttons only when it is their step. Approve calls `features.letters.logic.advance_as` (PED, then proctor or teacher, then HoD). Reject needs a reason and calls `letters.reject_as`, so the athlete is told and must redraft. Both are logged in `verification_decisions`.
- **Tabs.** "Letter approvals" (athlete, coach) lists faculty decisions with notes. "Upcoming absences" (faculty, admin) lists who is away in the look-ahead window and the make-up tests to plan, with a CSV download.

Tables: `verification_keys` (signing secret), `verification_decisions` (every approve / reject).
Handles: `athlete_deleted` (deletes that athlete's `verification_decisions` rows).
Emits: `verification_decided` {athlete_id, tournament_id, decision, status, reviewer, reviewer_title, note}.
Base URL: `settings.base_url(con)` when core provides it, else env `PLANNER_BASE_URL` (default `http://localhost:8501`; set to an address phones can reach, e.g. the laptop's LAN IP or the deployed URL). `PLANNER_SECRET` optionally overrides the stored secret.
Needs: `qrcode[pil]` (without it the letter gets the link but no QR image).

## What core needs to wire (app.py is core-owned)

1. Before the login gate, dispatch public routes: if `?verify=` is in `st.query_params`, call the route with `(con, value)` and `st.stop()`.
2. In the athlete's letter download: `letters.create_letter(con, aid, tid)`, then `docx = verification.logic.letter_with_qr(con, docx, aid, tid)`.
3. Add `qrcode[pil]` to requirements.txt.
4. Nice to have: after faculty log in from a `?verify=` link, keep the query param so they land back on the letter.

Tests: `python3 -m pytest -q features/verification` (18 tests, in-memory DB).
