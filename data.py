"""
SQLite storage + demo seed data.

Core tables (feature tables live in features/<name>/schema.sql):

  athletes    who (name, USN, dept, sem, section, sport, proctor)
  users       login accounts and roles (athlete, coach, faculty, admin)
  timetable   weekly class/lab slots per athlete (weekday 0=Mon)
  events      one-off CIE / SEE / lab dates per athlete
  tournaments tournament windows, with travel days
  entries     which athletes go to which tournament
  sessions    training log: date, minutes, RPE
  wellness    weekly check-in: sleep, soreness, stress (1-5)
  meta        key/value settings (demo flag, seed date, college settings)
  auth_sessions  remember-me login tokens (hashed)

See INTERFACES.md for the contract feature modules code against.
"""
from __future__ import annotations

import os
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

import hooks

DB_PATH = Path(__file__).with_name("planner.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS athletes (
  id INTEGER PRIMARY KEY, name TEXT, usn TEXT, dept TEXT, sem TEXT, sport TEXT, proctor TEXT, phone TEXT,
  section TEXT DEFAULT '', email TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT, role TEXT NOT NULL,
  title TEXT DEFAULT '', athlete_id INTEGER, dept TEXT, sem TEXT, section TEXT, sport TEXT,
  password_hash TEXT NOT NULL, must_change_password INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS timetable (
  id INTEGER PRIMARY KEY, athlete_id INTEGER, weekday INTEGER, subject TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, athlete_id INTEGER, date TEXT, kind TEXT, title TEXT);
CREATE TABLE IF NOT EXISTS tournaments (
  id INTEGER PRIMARY KEY, name TEXT, sport TEXT, venue TEXT, start_date TEXT, end_date TEXT,
  travel_before INTEGER DEFAULT 0, travel_after INTEGER DEFAULT 0, created_by INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS entries (tournament_id INTEGER, athlete_id INTEGER);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY, athlete_id INTEGER, date TEXT, minutes REAL, rpe REAL, type TEXT);
CREATE TABLE IF NOT EXISTS wellness (
  id INTEGER PRIMARY KEY, athlete_id INTEGER, date TEXT, sleep INTEGER, soreness INTEGER, stress INTEGER);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT, expires_at TEXT NOT NULL);
"""

# Columns added after the first prototype. Older planner.db files get them on connect.
_ADDED_COLUMNS = {
    "athletes": {"section": "TEXT DEFAULT ''", "email": "TEXT DEFAULT ''"},
    "tournaments": {"created_by": "INTEGER", "updated_at": "TEXT"},
}

# (table, column) pairs holding ISO dates; used to roll demo data forward.
_DATE_COLUMNS = [("events", "date"), ("tournaments", "start_date"), ("tournaments", "end_date"),
                 ("sessions", "date"), ("wellness", "date")]


def _migrate(con):
    for table, cols in _ADDED_COLUMNS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS athletes_usn ON athletes(usn)")


def connect(path=None) -> sqlite3.Connection:
    """Open the database. `path` beats env PLANNER_DB beats planner.db; ":memory:" for tests."""
    path = path or os.environ.get("PLANNER_DB") or DB_PATH
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.executescript(SCHEMA)
    _migrate(con)
    import features  # late import: feature modules may import data
    features.apply_schemas(con)
    features.register_hooks()
    con.commit()
    return con


def q(con, sql, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con, params=params)


def _dates(df, *cols):
    for c in cols:
        df[c] = pd.to_datetime(df[c]).dt.date
    return df


# ----------------------------------------------------------------- meta

def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, key, value):
    con.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))
    con.commit()


# ----------------------------------------------------------------- readers

def athletes(con):
    return q(con, "SELECT * FROM athletes ORDER BY name")


def athlete(con, athlete_id) -> dict | None:
    df = q(con, "SELECT * FROM athletes WHERE id=?", (int(athlete_id),))
    return None if df.empty else df.iloc[0].to_dict()


def athlete_by_usn(con, usn) -> dict | None:
    df = q(con, "SELECT * FROM athletes WHERE upper(usn)=upper(?)", (str(usn).strip(),))
    return None if df.empty else df.iloc[0].to_dict()


def athletes_in_class(con, dept, sem, section=None):
    if section in (None, ""):
        return q(con, "SELECT * FROM athletes WHERE dept=? AND sem=? ORDER BY name", (dept, sem))
    return q(con, "SELECT * FROM athletes WHERE dept=? AND sem=? AND section=? ORDER BY name", (dept, sem, section))


def timetable(con, athlete_id):
    return q(con, "SELECT weekday, subject, kind FROM timetable WHERE athlete_id=? ORDER BY weekday", (int(athlete_id),))


def events(con, athlete_id):
    df = q(con, "SELECT date, kind, title FROM events WHERE athlete_id=? ORDER BY date", (int(athlete_id),))
    return _dates(df, "date")


def tournament(con, tid) -> dict | None:
    df = _dates(q(con, "SELECT * FROM tournaments WHERE id=?", (int(tid),)), "start_date", "end_date")
    return None if df.empty else df.iloc[0].to_dict()


def tournaments_for(con, athlete_id):
    df = q(con, """SELECT t.* FROM tournaments t JOIN entries e ON e.tournament_id=t.id
                   WHERE e.athlete_id=? ORDER BY start_date""", (int(athlete_id),))
    return _dates(df, "start_date", "end_date")


def all_tournaments(con):
    return _dates(q(con, "SELECT * FROM tournaments ORDER BY start_date"), "start_date", "end_date")


def entries(con, tid) -> list[int]:
    return [r[0] for r in con.execute("SELECT athlete_id FROM entries WHERE tournament_id=?", (int(tid),))]


def sessions(con, athlete_id):
    return q(con, "SELECT date, minutes, rpe, type FROM sessions WHERE athlete_id=? ORDER BY date", (int(athlete_id),))


def wellness(con, athlete_id):
    return q(con, "SELECT date, sleep, soreness, stress FROM wellness WHERE athlete_id=? ORDER BY date", (int(athlete_id),))


# ----------------------------------------------------------------- writers

def add_session(con, athlete_id, d: date, minutes, rpe, stype):
    con.execute("INSERT INTO sessions(athlete_id,date,minutes,rpe,type) VALUES (?,?,?,?,?)",
                (int(athlete_id), d.isoformat(), minutes, rpe, stype))
    con.commit()


def add_wellness(con, athlete_id, d: date, sleep, soreness, stress):
    con.execute("INSERT INTO wellness(athlete_id,date,sleep,soreness,stress) VALUES (?,?,?,?,?)",
                (int(athlete_id), d.isoformat(), sleep, soreness, stress))
    con.commit()


def add_event(con, athlete_id, d: date, kind, title):
    add_events_bulk(con, [athlete_id], [(d, kind, title)])


def add_events_bulk(con, athlete_ids, items) -> int:
    """items: [(date, kind, title)]. Skips rows that already exist. Returns rows inserted."""
    n = 0
    for aid in athlete_ids:
        for d, kind, title in items:
            ds = d.isoformat() if hasattr(d, "isoformat") else str(d)
            if con.execute("SELECT 1 FROM events WHERE athlete_id=? AND date=? AND kind=? AND title=?",
                           (int(aid), ds, kind, title)).fetchone():
                continue
            con.execute("INSERT INTO events(athlete_id,date,kind,title) VALUES (?,?,?,?)", (int(aid), ds, kind, title))
            n += 1
    con.commit()
    if n:
        hooks.emit(con, "events_added", {"athlete_ids": [int(a) for a in athlete_ids], "count": n})
    return n


def set_timetable(con, athlete_id, rows):
    con.execute("DELETE FROM timetable WHERE athlete_id=?", (int(athlete_id),))
    con.executemany("INSERT INTO timetable(athlete_id,weekday,subject,kind) VALUES (?,?,?,?)",
                    [(int(athlete_id), int(r["weekday"]), r["subject"], r.get("kind", "class")) for r in rows])
    con.commit()


def _check_window(start, end, tb, ta):
    if end < start:
        raise ValueError("End date is before the start date.")
    if tb < 0 or ta < 0:
        raise ValueError("Travel days cannot be negative.")


def add_tournament(con, name, sport, venue, start, end, tb, ta, athlete_ids, created_by=None):
    _check_window(start, end, int(tb), int(ta))
    cur = con.execute(
        "INSERT INTO tournaments(name,sport,venue,start_date,end_date,travel_before,travel_after,created_by,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (name, sport, venue, start.isoformat(), end.isoformat(), int(tb), int(ta), created_by,
         datetime.now().isoformat(timespec="seconds")))
    tid = cur.lastrowid
    con.executemany("INSERT INTO entries(tournament_id,athlete_id) VALUES (?,?)", [(tid, int(a)) for a in athlete_ids])
    con.commit()
    hooks.emit(con, "tournament_created", {"tournament_id": tid})
    if athlete_ids:
        hooks.emit(con, "entries_changed", {"tournament_id": tid, "added": [int(a) for a in athlete_ids], "removed": []})
    return tid


_TOURNAMENT_FIELDS = ("name", "sport", "venue", "start_date", "end_date", "travel_before", "travel_after")


def update_tournament(con, tid, **fields) -> list[str]:
    """Change tournament fields; returns the fields that changed and emits tournament_updated."""
    before = tournament(con, tid)
    if before is None:
        raise KeyError(f"No tournament {tid}")
    unknown = set(fields) - set(_TOURNAMENT_FIELDS)
    if unknown:
        raise ValueError(f"Unknown tournament fields: {sorted(unknown)}")
    before = {k: (int(v) if k in ("id", "travel_before", "travel_after") else v) for k, v in before.items()}
    after = {**before, **fields}
    for k in ("travel_before", "travel_after"):
        after[k] = int(after[k])
    _check_window(after["start_date"], after["end_date"], after["travel_before"], after["travel_after"])
    changed = [k for k in _TOURNAMENT_FIELDS if after[k] != before[k]]
    if not changed:
        return []
    sets = ", ".join(f"{k}=?" for k in changed)
    vals = [after[k].isoformat() if isinstance(after[k], date) else after[k] for k in changed]
    con.execute(f"UPDATE tournaments SET {sets}, updated_at=? WHERE id=?",
                (*vals, datetime.now().isoformat(timespec="seconds"), int(tid)))
    con.commit()
    hooks.emit(con, "tournament_updated", {"tournament_id": int(tid), "before": before, "after": after, "changed": changed})
    return changed


def set_entries(con, tid, athlete_ids):
    old, new = set(entries(con, tid)), {int(a) for a in athlete_ids}
    added, removed = sorted(new - old), sorted(old - new)
    con.execute("DELETE FROM entries WHERE tournament_id=?", (int(tid),))
    con.executemany("INSERT INTO entries(tournament_id,athlete_id) VALUES (?,?)", [(int(tid), a) for a in sorted(new)])
    con.commit()
    if added or removed:
        hooks.emit(con, "entries_changed", {"tournament_id": int(tid), "added": added, "removed": removed})


ATHLETE_FIELDS = ("name", "dept", "sem", "section", "sport", "proctor", "phone", "email")


def upsert_athlete(con, rec: dict) -> tuple[int, bool]:
    """Insert or update by USN (no commit). Blank fields never wipe stored values. Returns (id, created)."""
    usn = str(rec["usn"]).strip().upper()
    vals = {c: ("" if rec.get(c) is None else str(rec.get(c)).strip()) for c in ATHLETE_FIELDS}
    existing = athlete_by_usn(con, usn)
    if existing:
        keep = [vals[c] if vals[c] != "" else (existing.get(c) or "") for c in ATHLETE_FIELDS]
        con.execute(f"UPDATE athletes SET {', '.join(f'{c}=?' for c in ATHLETE_FIELDS)} WHERE id=?",
                    (*keep, int(existing["id"])))
        return int(existing["id"]), False
    cur = con.execute(f"INSERT INTO athletes(usn,{','.join(ATHLETE_FIELDS)}) VALUES (?{',?' * len(ATHLETE_FIELDS)})",
                      (usn, *vals.values()))
    return cur.lastrowid, True


def delete_athlete(con, athlete_id):
    """Remove an athlete everywhere core owns, plus their login. Features clean up via athlete_deleted."""
    aid = int(athlete_id)
    hooks.emit(con, "athlete_deleted", {"athlete_id": aid})
    for table in ("timetable", "events", "entries", "sessions", "wellness"):
        con.execute(f"DELETE FROM {table} WHERE athlete_id=?", (aid,))
    uids = [r[0] for r in con.execute("SELECT id FROM users WHERE athlete_id=?", (aid,))]
    for uid in uids:
        con.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))
    con.execute("DELETE FROM users WHERE athlete_id=?", (aid,))
    con.execute("DELETE FROM athletes WHERE id=?", (aid,))
    con.commit()


# ----------------------------------------------------------------- reset / seed

def is_empty(con) -> bool:
    return con.execute("SELECT COUNT(*) FROM athletes").fetchone()[0] == 0


def _wipe(con, keep_settings=False):
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in tables:
        if t == "meta" and keep_settings:
            con.execute("DELETE FROM meta WHERE key NOT LIKE 'setting.%'")
        else:
            con.execute(f"DELETE FROM {t}")
    con.commit()


def reset(con, today: date | None = None):
    """Wipe every table (core and feature) and reseed. Unlike deleting the file, this works on Windows."""
    _wipe(con)
    seed(con, today)


def start_fresh(con, admin_username: str, admin_password: str, admin_name: str = "Sports Office Admin"):
    """
    Leave demo mode for real use: delete all demo data and demo logins (their passwords are
    public), keep the college settings, and create one admin with a password the admin chose.
    Everyone added afterwards must change their first password.
    """
    import auth
    auth.check_password_strength(admin_password)
    _wipe(con, keep_settings=True)
    set_meta(con, "demo", "0")
    return auth.create_user(con, admin_username, admin_name, "admin", admin_password)


def refresh_demo_dates(con, today: date | None = None) -> int:
    """
    Demo data is seeded relative to the day it was created. Roll every date
    forward so the demo still has upcoming tournaments weeks later.
    Returns days shifted (0 when not a demo DB or already current).
    """
    today = today or date.today()
    if get_meta(con, "demo") != "1":
        return 0
    seeded = date.fromisoformat(get_meta(con, "seeded_on", today.isoformat()))
    shift = (today - seeded).days
    if shift <= 0:
        return 0
    for table, col in _DATE_COLUMNS:
        con.execute(f"UPDATE {table} SET {col}=date({col}, ?) WHERE {col} IS NOT NULL", (f"+{shift} days",))
    set_meta(con, "seeded_on", today.isoformat())
    return shift


# Demo logins only exist in demo mode; "Switch to real data" (data.start_fresh) deletes them.
DEMO_PASSWORD = "rvce-demo"
DEMO_STAFF = [
    # username, name, role, title, dept, sport
    ("admin", "Sports Office Admin", "admin", "", None, None),
    ("ped", "Dr. Prakash (PED)", "coach", "ped", None, None),
    ("coach.cricket", "Coach Ramesh", "coach", "coach", None, "Cricket"),
    ("coach.football", "Coach Joseph", "coach", "coach", None, "Football"),
    ("proctor.cse", "Dr. Meena R", "faculty", "proctor", "CSE", None),
    ("hod.cse", "Dr. Ramakanth (HoD CSE)", "faculty", "hod", "CSE", None),
]


def seed(con, today: date | None = None):
    """Ten demo athletes, six weeks of logs, five tournaments, a few clashes, demo logins."""
    import auth  # late import: auth imports data

    today = today or date.today()
    rng = random.Random(42)

    people = [
        ("Aarav Kulkarni", "1RV25CS012", "CSE", "3rd", "Cricket", "Dr. Meena R"),
        ("Diya Shetty", "1RV25CS034", "CSE", "3rd", "Basketball", "Dr. Meena R"),
        ("Rohan Hegde", "1RV25EC077", "ECE", "3rd", "Football", "Prof. Anil K"),
        ("Sneha Patil", "1RV25ME021", "ME", "3rd", "Athletics", "Prof. Ravi S"),
        ("Karthik Nair", "1RV24CS110", "CSE", "5th", "Cricket", "Dr. Suresh B"),
        ("Ananya Rao", "1RV25IS045", "ISE", "3rd", "Badminton", "Prof. Latha M"),
        ("Vikram Gowda", "1RV24CV019", "CV", "5th", "Football", "Prof. Ravi S"),
        ("Priya Menon", "1RV25CS088", "CSE", "3rd", "Basketball", "Dr. Meena R"),
        ("Arjun Reddy", "1RV24EE003", "EEE", "5th", "Athletics", "Prof. Anil K"),
        ("Meghana S", "1RV25CS101", "CSE", "3rd", "Badminton", "Dr. Meena R"),
    ]
    for p in people:
        con.execute("INSERT INTO athletes(name,usn,dept,sem,sport,proctor,phone,section) VALUES (?,?,?,?,?,?,?,?)",
                    (*p, "98XXXXXXXX", "A"))
    ids = [r[0] for r in con.execute("SELECT id FROM athletes ORDER BY id")]

    # Weekly timetable: 3rd-sem CSE-ish subjects; two labs a week
    subjects = ["Data Structures", "Digital Logic & CO", "Operating Systems", "Quantum Computing", "Linear Algebra"]
    for aid in ids:
        for wd in range(6):  # Mon-Sat
            for sub in rng.sample(subjects, 3):
                con.execute("INSERT INTO timetable(athlete_id,weekday,subject,kind) VALUES (?,?,?,?)", (aid, wd, sub, "class"))
        con.execute("INSERT INTO timetable(athlete_id,weekday,subject,kind) VALUES (?,?,?,?)", (aid, 1, "Data Structures Lab", "lab"))
        con.execute("INSERT INTO timetable(athlete_id,weekday,subject,kind) VALUES (?,?,?,?)", (aid, 3, "DLCO Lab", "lab"))

    # CIE dates: CIE-1 in ~2 weeks, CIE-2 in ~6 weeks, SEE in ~10 weeks
    cie1 = today + timedelta(days=(7 - today.weekday()) % 7 + 14)   # a Monday
    cie2 = cie1 + timedelta(days=28)
    see = cie1 + timedelta(days=56)
    for aid in ids:
        for i, sub in enumerate(subjects[:3]):
            con.execute("INSERT INTO events(athlete_id,date,kind,title) VALUES (?,?,?,?)", (aid, (cie1 + timedelta(days=i)).isoformat(), "CIE", f"{sub} CIE-1"))
            con.execute("INSERT INTO events(athlete_id,date,kind,title) VALUES (?,?,?,?)", (aid, (cie2 + timedelta(days=i)).isoformat(), "CIE", f"{sub} CIE-2"))
            con.execute("INSERT INTO events(athlete_id,date,kind,title) VALUES (?,?,?,?)", (aid, (see + timedelta(days=i * 2)).isoformat(), "SEE", f"{sub} SEE"))

    # Tournaments: one lands on CIE-1 week, one clean, one during SEE
    add_tournament(con, "VTU Inter-Collegiate Cricket (Zonal)", "Cricket", "Mysuru",
                   cie1 - timedelta(days=1), cie1 + timedelta(days=2), 1, 1, [ids[0], ids[4]])
    add_tournament(con, "Inter-Engineering Basketball Cup", "Basketball", "PES University, Bengaluru",
                   today + timedelta(days=9), today + timedelta(days=10), 0, 0, [ids[1], ids[7]])
    add_tournament(con, "AIU South Zone Football", "Football", "Kochi",
                   see + timedelta(days=1), see + timedelta(days=5), 2, 1, [ids[2], ids[6]])
    add_tournament(con, "Khelo India University Games - Athletics Trials", "Athletics", "Sree Kanteerava Stadium",
                   today + timedelta(days=21), today + timedelta(days=22), 0, 0, [ids[3], ids[8]])
    add_tournament(con, "RVCE Open Badminton", "Badminton", "RVCE Sports Complex",
                   today + timedelta(days=5), today + timedelta(days=5), 0, 0, [ids[5], ids[9]])

    # Six weeks of training logs. Athlete 0 spikes in the last week (ACWR > 1.5).
    for k, aid in enumerate(ids):
        base = rng.choice([60, 75, 90])
        for back in range(42, -1, -1):
            d = today - timedelta(days=back)
            if d.weekday() == 6 and rng.random() < 0.7:
                continue  # most Sundays off
            minutes = base + rng.randint(-15, 15)
            rpe = rng.randint(4, 7)
            if k == 0 and back <= 6:          # pre-tournament overload
                minutes, rpe = base + 60, rng.randint(8, 10)
            if k == 3 and 14 <= back <= 24:   # injury layoff then return
                continue
            con.execute("INSERT INTO sessions(athlete_id,date,minutes,rpe,type) VALUES (?,?,?,?,?)",
                        (aid, d.isoformat(), minutes, rpe, rng.choice(["Skills", "Conditioning", "Match practice"])))
        # Weekly wellness for 6 weeks
        for w in range(6, -1, -1):
            d = today - timedelta(days=7 * w)
            sleep = rng.randint(2, 5)
            soreness = rng.randint(1, 4) + (1 if (k == 0 and w == 0) else 0)
            stress = rng.randint(1, 4) + (1 if w <= 1 else 0)
            con.execute("INSERT INTO wellness(athlete_id,date,sleep,soreness,stress) VALUES (?,?,?,?,?)",
                        (aid, d.isoformat(), sleep, min(soreness, 5), min(stress, 5)))

    # Logins. Demo athletes use their USN as the password.
    for aid, p in zip(ids, people):
        auth.create_user(con, p[1], p[0], "athlete", p[1], athlete_id=aid, dept=p[2], sem=p[3], section="A", sport=p[4])
    for username, name, role, title, dept, sport in DEMO_STAFF:
        auth.create_user(con, username, name, role, DEMO_PASSWORD, title=title, dept=dept, sport=sport)

    set_meta(con, "demo", "1")
    set_meta(con, "seeded_on", today.isoformat())
    con.commit()
