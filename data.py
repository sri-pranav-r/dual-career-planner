"""
SQLite storage.

Core tables (feature tables live in features/<name>/schema.sql):

  athletes    who (name, USN, dept, sem, section, sport, proctor)
  users       login accounts and roles (athlete, coach, faculty, admin)
  timetable   weekly class/lab slots per athlete (weekday 0=Mon)
  events      one-off CIE / SEE / lab dates per athlete
  tournaments tournament windows, with travel days
  entries     which athletes go to which tournament
  sessions    training log: date, minutes, RPE
  wellness    weekly check-in: sleep, soreness, stress (1-5)
  meta        key/value settings (college settings, setup code hash)
  auth_sessions  sign-in tokens (hashed)
  login_failures failed sign-ins, for the lock-out after repeated wrong passwords

See INTERFACES.md for the contract feature modules code against.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
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
CREATE TABLE IF NOT EXISTS login_failures (username TEXT NOT NULL, at TEXT NOT NULL);
"""

# Columns added after the first prototype. Older planner.db files get them on connect.
_ADDED_COLUMNS = {
    "athletes": {"section": "TEXT DEFAULT ''", "email": "TEXT DEFAULT ''"},
    "tournaments": {"created_by": "INTEGER", "updated_at": "TEXT"},
}

def _migrate(con):
    for table, cols in _ADDED_COLUMNS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS athletes_usn ON athletes(usn)")


def db_path(path=None) -> str:
    """`path` beats PLANNER_DB (environment or Streamlit secrets) beats planner.db next to app.py."""
    import config
    return str(path or config.get("PLANNER_DB") or DB_PATH)


def connect(path=None) -> sqlite3.Connection:
    """Open the database (see db_path); ":memory:" for tests. Creates the folder if it is missing."""
    path = db_path(path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False, timeout=15)
    if path != ":memory:":
        con.execute("PRAGMA journal_mode=WAL")   # readers don't block the one writer
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


# ----------------------------------------------------------------- first run, backup and restore

def is_empty(con) -> bool:
    return con.execute("SELECT COUNT(*) FROM athletes").fetchone()[0] == 0


def has_admin(con) -> bool:
    return con.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone() is not None


def has_legacy_sample_data(con) -> bool:
    """True for a planner.db made by a version before v7, which filled it with made-up records and public logins."""
    return get_meta(con, "demo") == "1"


def wipe(con):
    """Delete every row in every table, settings included."""
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in tables:
        con.execute(f"DELETE FROM {t}")
    con.commit()


def backup_bytes(con) -> bytes:
    """A consistent copy of the whole database as an SQLite file (taken with SQLite's backup API)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "backup.db"
        out = sqlite3.connect(str(dest))
        with out:
            con.backup(out)
        out.close()
        return dest.read_bytes()


REQUIRED_TABLES = {"athletes", "users", "tournaments", "meta"}


def restore_bytes(con, raw: bytes) -> dict:
    """
    Replace everything in the open database with an uploaded backup. The file must be an SQLite
    database made by this app with at least one admin account. Returns counts of what came back.
    """
    import tempfile
    if not raw.startswith(b"SQLite format 3\x00"):
        raise ValueError("That file isn't a planner backup (it isn't an SQLite database).")
    with tempfile.TemporaryDirectory() as tmp:
        src_path = Path(tmp) / "restore.db"
        src_path.write_bytes(raw)
        src = sqlite3.connect(str(src_path))
        try:
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                raise ValueError("That file isn't a planner backup (missing " + ", ".join(sorted(missing)) + ").")
            if not src.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
                raise ValueError("That backup has no admin account, so nobody could sign in to it.")
            if src.execute("SELECT value FROM meta WHERE key='demo'").fetchone() == ("1",):
                raise ValueError("That backup holds the sample records and public logins of an older version.")
            src.backup(con)
        except sqlite3.DatabaseError as err:
            raise ValueError(f"That backup couldn't be read: {err}.") from err
        finally:
            src.close()
    # Bring the restored copy up to this version's schema.
    con.executescript(SCHEMA)
    _migrate(con)
    import features
    features.apply_schemas(con)
    con.execute("DELETE FROM auth_sessions")   # everyone signs in again
    con.commit()
    return {"athletes": con.execute("SELECT COUNT(*) FROM athletes").fetchone()[0],
            "users": con.execute("SELECT COUNT(*) FROM users").fetchone()[0]}
