"""
Saving imported timetables and exam calendars. No Streamlit here.

A department timetable and the exam calendar belong to a class, not to one
athlete, so both are kept per class in this feature's own tables and copied
into each athlete's core `timetable` / `events` rows. When the roster import
adds an athlete later, `on_roster_imported` gives them their class's copy.

A class is (dept, sem, section). section None means "every section of that
dept and sem" (a calendar usually covers all sections).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pandas as pd

import data

from .parsers import TIMETABLE_COLUMNS


def _iso(d) -> str:
    return d.isoformat() if isinstance(d, (date, datetime)) else str(pd.to_datetime(d).date())


def _section_key(section) -> str:
    """Stored form: '*' = all sections, '' = no section, else the section letter."""
    return "*" if section is None else str(section)


def classes(con) -> pd.DataFrame:
    """Every (dept, sem, section) that has athletes, with a head count."""
    return data.q(con, """SELECT dept, sem, COALESCE(section,'') AS section, COUNT(*) AS athletes
                          FROM athletes GROUP BY dept, sem, COALESCE(section,'')
                          ORDER BY dept, sem, section""")


def _athlete_ids(con, dept, sem, section) -> list[int]:
    df = data.athletes_in_class(con, dept, sem, section)
    return [int(i) for i in df["id"]] if not df.empty else []


def _ensure_log_columns(con):
    """imports_log gained athlete_id (for data deletion); add it to databases made before that."""
    have = {r[1] for r in con.execute("PRAGMA table_info(imports_log)")}
    if "athlete_id" not in have:
        con.execute("ALTER TABLE imports_log ADD COLUMN athlete_id INTEGER")


def _log(con, kind, dept, sem, section, source_name, n_rows, n_athletes, user_id, athlete_id=None):
    _ensure_log_columns(con)
    con.execute("""INSERT INTO imports_log(kind, dept, sem, section, source_name, rows, athletes,
                   imported_by, imported_at, athlete_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (kind, dept, sem, _section_key(section), source_name, n_rows, n_athletes,
                 user_id, datetime.now().isoformat(timespec="seconds"), athlete_id))
    con.commit()


def _clean_timetable(rows) -> list[dict]:
    df = pd.DataFrame(rows, columns=TIMETABLE_COLUMNS) if not isinstance(rows, pd.DataFrame) else rows
    out = []
    for r in df.itertuples(index=False):
        subject = str(r.subject).strip()
        if not subject or pd.isna(r.weekday):
            continue
        kind = "lab" if str(r.kind).strip().lower() == "lab" else "class"
        out.append({"weekday": int(r.weekday), "subject": subject, "kind": kind})
    # one row per slot is enough for clash detection; a 3-period lab is still one lab
    return [dict(t) for t in dict.fromkeys(tuple(r.items()) for r in out)]


# ---------------------------------------------------------------------------
# Timetable
# ---------------------------------------------------------------------------

def save_athlete_timetable(con, athlete_id: int, rows, source_name: str = "", user_id=None) -> int:
    """Replace one athlete's weekly timetable. Returns the number of slots saved."""
    clean = _clean_timetable(rows)
    data.set_timetable(con, athlete_id, clean)
    a = data.athlete(con, athlete_id)
    _log(con, "timetable", a.get("dept"), a.get("sem"), a.get("section") or "", source_name or "athlete upload",
         len(clean), 1, user_id, athlete_id=int(athlete_id))
    return len(clean)


def save_class_timetable(con, dept: str, sem: str, section, rows, source_name: str = "",
                         user_id=None) -> dict:
    """
    Store the class timetable and replace the timetable of every athlete in that class.
    Returns {"slots": n, "athletes": n}.
    """
    clean = _clean_timetable(rows)
    key = _section_key(section)
    con.execute("DELETE FROM imports_class_timetable WHERE dept=? AND sem=? AND section=?", (dept, sem, key))
    con.executemany("""INSERT INTO imports_class_timetable(dept, sem, section, weekday, subject, kind)
                       VALUES (?,?,?,?,?,?)""",
                    [(dept, sem, key, r["weekday"], r["subject"], r["kind"]) for r in clean])
    con.commit()
    ids = _athlete_ids(con, dept, sem, section)
    for aid in ids:
        data.set_timetable(con, aid, clean)
    _log(con, "timetable", dept, sem, section, source_name, len(clean), len(ids), user_id)
    return {"slots": len(clean), "athletes": len(ids)}


def class_timetable(con, dept, sem, section) -> pd.DataFrame:
    """The stored timetable for a class; falls back to the all-sections one."""
    for key in ([str(section or "")] if section is not None else []) + ["*"]:
        df = data.q(con, """SELECT weekday, subject, kind FROM imports_class_timetable
                            WHERE dept=? AND sem=? AND section=? ORDER BY weekday, id""", (dept, sem, key))
        if not df.empty:
            return df
    return pd.DataFrame(columns=TIMETABLE_COLUMNS)


# ---------------------------------------------------------------------------
# Exam calendar
# ---------------------------------------------------------------------------

def apply_exam_calendar(con, targets: list[tuple], rows, source_name: str = "", user_id=None) -> dict:
    """
    targets: [(dept, sem, section_or_None), ...]
    rows:    DataFrame / list with date, kind, title
    Adds the events to the class calendar and to every athlete in those classes.
    Exact duplicates (same athlete, date, kind, title) are skipped, so re-importing is safe.
    Returns {"events": n distinct events, "athletes": n athletes reached}.
    """
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    events = []
    for r in df.itertuples(index=False):
        kind = {"cie": "CIE", "see": "SEE", "lab": "lab"}.get(str(r.kind).strip().lower())
        title = str(r.title).strip()
        if not kind or not title or pd.isna(r.date):
            continue
        events.append((_iso(r.date), kind, title))
    events = list(dict.fromkeys(events))

    reached: set[int] = set()
    for dept, sem, section in targets:
        key = _section_key(section)
        con.executemany("""INSERT OR IGNORE INTO imports_class_events(dept, sem, section, date, kind, title)
                           VALUES (?,?,?,?,?,?)""", [(dept, sem, key, *e) for e in events])
        con.commit()
        ids = _athlete_ids(con, dept, sem, section)
        if ids and events:
            data.add_events_bulk(con, ids, [(date.fromisoformat(d), k, t) for d, k, t in events])
        reached.update(ids)
        _log(con, "exam_calendar", dept, sem, section, source_name, len(events), len(ids), user_id)
    return {"events": len(events), "athletes": len(reached)}


def class_events(con, dept, sem, section=None) -> pd.DataFrame:
    """Imported calendar events for a class, including all-section ones."""
    if section is None:
        df = data.q(con, """SELECT DISTINCT date, kind, title FROM imports_class_events
                            WHERE dept=? AND sem=? ORDER BY date""", (dept, sem))
    else:
        df = data.q(con, """SELECT DISTINCT date, kind, title FROM imports_class_events
                            WHERE dept=? AND sem=? AND section IN (?, '*') ORDER BY date""",
                    (dept, sem, str(section)))
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def import_history(con, limit: int = 20) -> pd.DataFrame:
    return data.q(con, "SELECT * FROM imports_log ORDER BY id DESC LIMIT ?", (limit,))


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

def on_roster_imported(con, payload: dict) -> None:
    """New or moved athletes get their class's calendar, and its timetable if they have none."""
    for aid in payload.get("athlete_ids", []):
        a = data.athlete(con, aid)
        if a is None or len(a) == 0:
            continue
        dept, sem, section = a.get("dept"), a.get("sem"), a.get("section") or ""
        ev = class_events(con, dept, sem, section)
        if not ev.empty:
            data.add_events_bulk(con, [aid], [(r.date, r.kind, r.title) for r in ev.itertuples(index=False)])
        if data.timetable(con, aid).empty:
            tt = class_timetable(con, dept, sem, section)
            if not tt.empty:
                data.set_timetable(con, aid, tt.to_dict("records"))


def on_athlete_deleted(con, payload: dict) -> None:
    """
    Data deletion: drop the log rows of this athlete's own timetable uploads (they carry the file name).
    Class timetables and class calendars stay: they belong to the class, not to one person.
    """
    aid = payload.get("athlete_id")
    if aid is None:
        return
    _ensure_log_columns(con)
    con.execute("DELETE FROM imports_log WHERE athlete_id=?", (int(aid),))
    # rows logged before athlete_id existed: the athlete's own login is recorded as imported_by
    user_ids = [r[0] for r in con.execute("SELECT id FROM users WHERE athlete_id=?", (int(aid),))]
    con.executemany("DELETE FROM imports_log WHERE athlete_id IS NULL AND athletes=1 AND imported_by=?",
                    [(u,) for u in user_ids])
    con.commit()
