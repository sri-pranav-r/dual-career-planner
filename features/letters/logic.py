"""
Letter status tracker + tournament change alerts. No Streamlit here, so every
function can be unit-tested on its own (see test_letters.py).

  1. Letter tracker  - each exemption letter moves through
                       drafted -> PED signed -> proctor signed -> HoD approved -> submitted,
                       one step at a time, with a history row for every step.
  2. Alerts          - reacting to core events: when a tournament's dates, travel days,
                       venue or name change, every entered athlete gets an in-app
                       notification, and any letter already drafted for it is flagged
                       "needs redo" because its dates are now wrong. Athletes added to or
                       removed from a tournament are told too.

Tables (schema.sql): letters_status, letters_history, letters_notifications.
Emits: letters_stage_changed {letter_id, athlete_id, tournament_id, stage, by}  (stage may be "rejected").
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rows(cur) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _emit(con, event, payload):
    try:
        import hooks
    except ImportError:  # running outside the planner (e.g. a bare unit test)
        return
    hooks.emit(con, event, payload)


# ---------------------------------------------------------------------------
# 1. Letter status tracker
# ---------------------------------------------------------------------------

STAGES = ["drafted", "ped_signed", "proctor_signed", "hod_approved", "submitted"]
STAGE_LABELS = {
    "drafted": "Drafted",
    "ped_signed": "PED signed",
    "proctor_signed": "Proctor signed",
    "hod_approved": "HoD approved",
    "submitted": "Submitted",
}


class LetterError(ValueError):
    pass


def next_stage(stage: str) -> str | None:
    i = STAGES.index(stage)
    return STAGES[i + 1] if i + 1 < len(STAGES) else None


def progress(stage: str | None) -> float:
    """0.0 for not started / drafted .. 1.0 for submitted. Handy for st.progress."""
    if stage is None:
        return 0.0
    return STAGES.index(stage) / (len(STAGES) - 1)


def get_letter(con, letter_id: int) -> dict:
    cur = con.execute("SELECT * FROM letters_status WHERE id=?", (letter_id,))
    rows = _rows(cur)
    if not rows:
        raise LetterError(f"No letter with id {letter_id}")
    return rows[0]


def find_letter(con, athlete_id: int, tournament_id: int) -> dict | None:
    rows = _rows(con.execute("SELECT * FROM letters_status WHERE athlete_id=? AND tournament_id=?",
                             (athlete_id, tournament_id)))
    return rows[0] if rows else None


def _log(con, letter_id, stage, by, note, when=None):
    con.execute("INSERT INTO letters_history(letter_id,stage,changed_at,changed_by,note) VALUES (?,?,?,?,?)",
                (letter_id, stage, when or _now(), by, note))


def create_letter(con, athlete_id: int, tournament_id: int, by: str = "athlete") -> int:
    """
    Record that the athlete drafted (downloaded) their letter. Core can call this from the
    existing "Download letter" button. Idempotent: calling it again returns the same letter,
    except that a letter flagged needs_redo or rejected is reset to drafted (signatures start over).
    """
    existing = find_letter(con, athlete_id, tournament_id)
    now = _now()
    if existing:
        if existing["needs_redo"] or existing["rejected"]:
            why = "Redrafted with new tournament dates" if existing["needs_redo"] else "Redrafted after rejection"
            con.execute("UPDATE letters_status SET stage='drafted', needs_redo=0, rejected=0, updated_at=? WHERE id=?",
                        (now, existing["id"]))
            _log(con, existing["id"], "drafted", by, why, now)
            con.commit()
            _emit(con, "letters_stage_changed", {"letter_id": existing["id"], "athlete_id": athlete_id,
                                                 "tournament_id": tournament_id, "stage": "drafted", "by": by})
        return existing["id"]
    cur = con.execute("INSERT INTO letters_status(athlete_id,tournament_id,stage,created_at,updated_at) "
                      "VALUES (?,?,?,?,?)", (athlete_id, tournament_id, "drafted", now, now))
    lid = cur.lastrowid
    _log(con, lid, "drafted", by, None, now)
    con.commit()
    _emit(con, "letters_stage_changed", {"letter_id": lid, "athlete_id": athlete_id,
                                         "tournament_id": tournament_id, "stage": "drafted", "by": by})
    return lid


def advance_letter(con, letter_id: int, by: str = "", note: str | None = None) -> str:
    """Move the letter one stage forward. Returns the new stage."""
    letter = get_letter(con, letter_id)
    if letter["needs_redo"]:
        raise LetterError("The tournament dates changed. Download a fresh letter before collecting signatures.")
    if letter["rejected"]:
        raise LetterError("This letter was rejected. The athlete must redraft it first.")
    nxt = next_stage(letter["stage"])
    if nxt is None:
        raise LetterError("This letter is already submitted.")
    now = _now()
    con.execute("UPDATE letters_status SET stage=?, updated_at=? WHERE id=?", (nxt, now, letter_id))
    _log(con, letter_id, nxt, by, note, now)
    con.commit()
    _emit(con, "letters_stage_changed", {"letter_id": letter_id, "athlete_id": letter["athlete_id"],
                                         "tournament_id": letter["tournament_id"], "stage": nxt, "by": by})
    return nxt


def undo_last_step(con, letter_id: int, by: str = "") -> str:
    """Step back one stage, for a mis-click. Returns the new stage."""
    letter = get_letter(con, letter_id)
    i = STAGES.index(letter["stage"])
    if i == 0:
        raise LetterError("This letter is already at the first stage.")
    prev = STAGES[i - 1]
    now = _now()
    con.execute("UPDATE letters_status SET stage=?, updated_at=? WHERE id=?", (prev, now, letter_id))
    _log(con, letter_id, prev, by, "Undo", now)
    con.commit()
    return prev


def letters_for_athlete(con, athlete_id: int) -> list[dict]:
    """Every tournament the athlete is entered in, with its letter (stage None = not drafted yet)."""
    return _rows(con.execute("""
        SELECT t.id AS tournament_id, t.name AS tournament, t.start_date, t.end_date,
               l.id AS letter_id, l.stage, COALESCE(l.needs_redo, 0) AS needs_redo, COALESCE(l.rejected, 0) AS rejected, l.updated_at
        FROM entries e JOIN tournaments t ON t.id = e.tournament_id
        LEFT JOIN letters_status l ON l.tournament_id = t.id AND l.athlete_id = e.athlete_id
        WHERE e.athlete_id = ? ORDER BY t.start_date""", (athlete_id,)))


def letters_for_tournament(con, tournament_id: int, athlete_ids: list[int] | None = None) -> list[dict]:
    """Board for staff: every entered athlete and where their letter is. Pass athlete_ids to restrict."""
    rows = _rows(con.execute("""
        SELECT a.id AS athlete_id, a.name, a.usn, a.dept, a.sem, a.proctor,
               l.id AS letter_id, l.stage, COALESCE(l.needs_redo, 0) AS needs_redo, COALESCE(l.rejected, 0) AS rejected, l.updated_at
        FROM entries e JOIN athletes a ON a.id = e.athlete_id
        LEFT JOIN letters_status l ON l.athlete_id = a.id AND l.tournament_id = e.tournament_id
        WHERE e.tournament_id = ? ORDER BY a.name""", (tournament_id,)))
    if athlete_ids is not None:
        allowed = set(athlete_ids)
        rows = [r for r in rows if r["athlete_id"] in allowed]
    return rows


def letter_history(con, letter_id: int) -> list[dict]:
    return _rows(con.execute("SELECT stage, changed_at, changed_by, note FROM letters_history "
                             "WHERE letter_id=? ORDER BY id", (letter_id,)))


# Who may move a letter INTO each stage. Checked against the auth.User fields, so logic
# stays free of Streamlit. Admin can do every step.
def can_advance_to(user, stage: str, athlete_id: int, con=None) -> bool:
    role = getattr(user, "role", None)
    if role == "admin":
        return True
    if stage in ("drafted", "submitted"):
        return role == "athlete" and getattr(user, "athlete_id", None) == athlete_id
    if stage == "ped_signed":
        return role == "coach" and not getattr(user, "sport", None)   # coach with no sport = PED
    if role != "faculty":
        return False
    title = getattr(user, "title", None)
    if stage == "proctor_signed" and title not in ("proctor", "teacher"):
        return False
    if stage == "hod_approved" and title != "hod":
        return False
    if con is not None:
        import auth
        return auth.can_view_athlete(con, user, athlete_id)
    return True


def advance_as(con, user, letter_id: int, note: str | None = None) -> str:
    """advance_letter with the permission check. Raises PermissionError if not allowed."""
    letter = get_letter(con, letter_id)
    nxt = next_stage(letter["stage"])
    if nxt is None:
        raise LetterError("This letter is already submitted.")
    if not can_advance_to(user, nxt, letter["athlete_id"], con):
        raise PermissionError(f"{getattr(user, 'name', 'This user')} cannot mark a letter {STAGE_LABELS[nxt]}.")
    return advance_letter(con, letter_id, by=getattr(user, "name", ""), note=note)


def reject_as(con, user, letter_id: int, reason: str) -> None:
    """
    The person whose signature is pending (or an admin) sends the letter back. The athlete is
    notified with the reason and must redraft (create_letter) before signatures start again.
    """
    letter = get_letter(con, letter_id)
    nxt = next_stage(letter["stage"])
    if nxt is None or nxt == "submitted":
        raise LetterError("Only a letter still waiting for a signature or approval can be rejected.")
    if letter["rejected"]:
        raise LetterError("This letter is already rejected.")
    if not can_advance_to(user, nxt, letter["athlete_id"], con):
        raise PermissionError(f"{getattr(user, 'name', 'This user')} cannot act on this letter at its current stage.")
    if not (reason or "").strip():
        raise LetterError("Give a reason so the athlete knows what to fix.")
    by, now = getattr(user, "name", ""), _now()
    con.execute("UPDATE letters_status SET rejected=1, updated_at=? WHERE id=?", (now, letter_id))
    _log(con, letter_id, letter["stage"], by, f"Rejected: {reason.strip()}", now)
    t = _tournament(con, letter["tournament_id"]) or {"name": "your tournament"}
    con.execute("INSERT INTO letters_notifications(athlete_id,tournament_id,kind,message,created_at) VALUES (?,?,?,?,?)",
                (letter["athlete_id"], letter["tournament_id"], "letter_rejected",
                 f"Your exemption letter for {t['name']} was rejected by {by}: {reason.strip()} "
                 f"Fix it, download a fresh letter and start signatures again.", now))
    con.commit()
    _emit(con, "letters_stage_changed", {"letter_id": letter_id, "athlete_id": letter["athlete_id"],
                                         "tournament_id": letter["tournament_id"], "stage": "rejected", "by": by})


def status_label(letter: dict | None) -> str:
    """One display string for a letter row: 'Not drafted', 'Rejected', 'Needs redo', or the stage label."""
    if not letter or letter.get("stage") is None:
        return "Not drafted"
    if letter.get("rejected"):
        return "Rejected"
    if letter.get("needs_redo"):
        return "Needs redo"
    return STAGE_LABELS[letter["stage"]]


# ---------------------------------------------------------------------------
# 2. Notifications
# ---------------------------------------------------------------------------

def notify(con, athlete_id: int, message: str, kind: str = "info", tournament_id: int | None = None) -> int:
    """General-purpose inbox entry. Other features (reminders, approvals) can reuse it."""
    cur = con.execute("INSERT INTO letters_notifications(athlete_id,tournament_id,kind,message,created_at) "
                      "VALUES (?,?,?,?,?)", (athlete_id, tournament_id, kind, message, _now()))
    con.commit()
    return cur.lastrowid


def notifications(con, athlete_id: int, unread_only: bool = False, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM letters_notifications WHERE athlete_id=?"
    if unread_only:
        sql += " AND read_at IS NULL"
    return _rows(con.execute(sql + " ORDER BY id DESC LIMIT ?", (athlete_id, limit)))


def unread_count(con, athlete_id: int) -> int:
    return con.execute("SELECT COUNT(*) FROM letters_notifications WHERE athlete_id=? AND read_at IS NULL",
                       (athlete_id,)).fetchone()[0]


def mark_read(con, notification_id: int) -> None:
    con.execute("UPDATE letters_notifications SET read_at=? WHERE id=? AND read_at IS NULL",
                (_now(), notification_id))
    con.commit()


def mark_all_read(con, athlete_id: int) -> int:
    cur = con.execute("UPDATE letters_notifications SET read_at=? WHERE athlete_id=? AND read_at IS NULL",
                      (_now(), athlete_id))
    con.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# 3. Hooks: react to core tournament events
# ---------------------------------------------------------------------------

FIELD_LABELS = {
    "name": "Name",
    "venue": "Venue",
    "start_date": "Start",
    "end_date": "End",
    "travel_before": "Travel days before",
    "travel_after": "Travel days after",
}
# Changing any of these moves the away window, so drafted letters carry wrong dates.
DATE_FIELDS = {"start_date", "end_date", "travel_before", "travel_after"}


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if hasattr(v, "date") and callable(v.date):  # pandas Timestamp
        return v.date().isoformat()
    return str(v)[:10]


def _show(field, v) -> str:
    if field in ("start_date", "end_date") and v is not None:
        return date.fromisoformat(_iso(v)).strftime("%d %b")
    if v is None or v == "":
        return "none"
    if field in ("travel_before", "travel_after"):
        return str(int(v))
    return str(v)


def _same(field, a, b) -> bool:
    if field in ("start_date", "end_date"):
        return _iso(a) == _iso(b)
    if field in ("travel_before", "travel_after"):
        return int(a or 0) == int(b or 0)
    return (a or "") == (b or "")


def _tournament(con, tid) -> dict | None:
    rows = _rows(con.execute("SELECT * FROM tournaments WHERE id=?", (tid,)))
    return rows[0] if rows else None


def _window(t: dict) -> str:
    return f"{_show('start_date', t['start_date'])} to {_show('end_date', t['end_date'])}"


def _entered(con, tid) -> list[int]:
    return [r[0] for r in con.execute("SELECT athlete_id FROM entries WHERE tournament_id=?", (tid,))]


def on_tournament_updated(con, payload: dict) -> dict:
    """
    payload: {tournament_id, before: dict, after: dict, changed: [field, ...]}
    Sends one notification per entered athlete and flags their letters if dates moved.
    Returns {"notified": [...], "letters_flagged": [...]} (handy for tests).
    """
    tid = payload["tournament_id"]
    before, after = payload.get("before") or {}, payload.get("after") or {}
    changed = [f for f in (payload.get("changed") or after.keys())
               if f in FIELD_LABELS and not _same(f, before.get(f), after.get(f))]
    result = {"notified": [], "letters_flagged": []}
    if not changed:
        return result

    dates_moved = bool(DATE_FIELDS & set(changed))
    now = _now()
    if dates_moved:
        for lid, stage in con.execute("SELECT id, stage FROM letters_status WHERE tournament_id=? AND needs_redo=0",
                                      (tid,)).fetchall():
            con.execute("UPDATE letters_status SET needs_redo=1, updated_at=? WHERE id=?", (now, lid))
            _log(con, lid, stage, "system", "Tournament dates changed; letter needs redoing", now)
            result["letters_flagged"].append(lid)

    name = after.get("name") or before.get("name") or (_tournament(con, tid) or {}).get("name", "Tournament")
    parts = [f"{FIELD_LABELS[f]} {_show(f, before.get(f))} → {_show(f, after.get(f))}" for f in changed]
    base = f"{name} has changed: " + "; ".join(parts) + "."
    with_letter = {r[0] for r in con.execute("SELECT athlete_id FROM letters_status WHERE tournament_id=?", (tid,))}
    for aid in _entered(con, tid):
        msg = base
        if dates_moved:
            msg += " Check the Clashes tab for what you now miss."
            if aid in with_letter:
                msg += " Your exemption letter has the old dates, so download a fresh one and collect signatures again."
        con.execute("INSERT INTO letters_notifications(athlete_id,tournament_id,kind,message,created_at) "
                    "VALUES (?,?,?,?,?)", (aid, tid, "tournament_changed", msg, now))
        result["notified"].append(aid)
    con.commit()
    return result


def _already_told(con, aid, tid, kind) -> bool:
    return con.execute("SELECT 1 FROM letters_notifications WHERE athlete_id=? AND tournament_id=? AND kind=? "
                       "AND id > COALESCE((SELECT MAX(id) FROM letters_notifications WHERE athlete_id=? "
                       "AND tournament_id=? AND kind='removed_from_tournament'), 0)",
                       (aid, tid, kind, aid, tid)).fetchone() is not None


def _tell_entered(con, tid, athlete_ids) -> list[int]:
    t = _tournament(con, tid)
    if t is None:
        return []
    told = []
    for aid in athlete_ids:
        if _already_told(con, aid, tid, "entered_in_tournament"):
            continue  # core may emit both tournament_created and entries_changed for one save
        notify(con, aid, f"You have been entered in {t['name']} ({_window(t)}). "
                         f"Check the Clashes tab and download your exemption letter early.",
               kind="entered_in_tournament", tournament_id=tid)
        told.append(aid)
    return told


def on_tournament_created(con, payload: dict) -> list[int]:
    tid = payload["tournament_id"]
    return _tell_entered(con, tid, _entered(con, tid))


def on_entries_changed(con, payload: dict) -> dict:
    tid = payload["tournament_id"]
    added = _tell_entered(con, tid, payload.get("added") or [])
    t = _tournament(con, tid) or {"name": "a tournament"}
    removed = []
    for aid in payload.get("removed") or []:
        notify(con, aid, f"You are no longer entered in {t['name']}. Any exemption letter for it is not needed.",
               kind="removed_from_tournament", tournament_id=tid)
        removed.append(aid)
    return {"added": added, "removed": removed}


def on_athlete_deleted(con, payload: dict) -> int:
    """Data deletion: remove every letter, history row and notification for the athlete. Returns letters removed."""
    aid = int(payload["athlete_id"])
    ids = [r[0] for r in con.execute("SELECT id FROM letters_status WHERE athlete_id=?", (aid,))]
    con.executemany("DELETE FROM letters_history WHERE letter_id=?", [(i,) for i in ids])
    con.execute("DELETE FROM letters_status WHERE athlete_id=?", (aid,))
    con.execute("DELETE FROM letters_notifications WHERE athlete_id=?", (aid,))
    con.commit()
    return len(ids)
