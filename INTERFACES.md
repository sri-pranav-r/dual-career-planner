# Planner interfaces for feature threads

The core thread owns and maintains this file. Only the core thread edits `app.py`, `data.py`, `auth.py`, `hooks.py` and the core tables. Feature threads write under `features/<name>/` only. If you need something from core, ask through the coordinator and don't edit core files yourself.

Stack: Python 3.11, Streamlit, SQLite (stdlib `sqlite3`), pandas. Dates are stored as ISO `YYYY-MM-DD` text. Keep everything off GitHub for now.

## 1. Your feature folder

```
features/<name>/
  __init__.py     # exports FEATURE (see below)
  schema.sql      # optional: CREATE TABLE IF NOT EXISTS only, tables prefixed with <name>_
  logic.py        # functions taking `con` (sqlite3.Connection); no streamlit import
  ui.py           # streamlit render functions
  test_<name>.py  # pytest; use data.connect(":memory:") + data.seed(con)
  README.md       # a few lines: what it does, tables it owns
```

`__init__.py` must define:

```python
FEATURE = {
    "name": "letters",                         # folder name
    "tabs": [                                  # tabs added to the main UI
        {"label": "Letter status", "roles": {"athlete", "coach"}, "render": "ui:render_status"},
    ],
    "public_routes": {                         # no login needed; ?<key>=<value> in the URL
        # "verify": "ui:render_verify",        # called as fn(con, value)
    },
    "hooks": {                                 # core + feature events you react to
        # "tournament_updated": "logic:on_tournament_updated",   # called as fn(con, payload)
    },
}
```

Callables are given as `"module:function"` strings inside your folder, so core can import them lazily.
- A tab's `render(con, user)` draws inside a page of the menu. Core draws the page title and a one-line explanation above it, so start with the content (a `st.subheader` is fine for a section inside it).
- Core places each tab in the menu by its label (`nav.py`). A label `nav.py` doesn't know yet still shows up, under **More**; ask core to give it a section and an explanation.
- `roles` controls who sees the tab.
- Re-check permissions inside the tab with `auth.can` / `auth.can_view_athlete`.

Core loads every folder in `features/` that has an `__init__.py` with `FEATURE`, runs its `schema.sql` on connect, and registers its tabs, routes and hooks.

## 2. Database: core tables (owned by core, read freely, write through data.py helpers)

```sql
athletes    (id, name, usn UNIQUE, dept, sem, section, sport, proctor, phone, email)
users       (id, username UNIQUE, name, role, title, athlete_id, dept, sem, section, sport,
             password_hash, must_change_password, created_at)
              -- role: 'athlete' | 'coach' | 'faculty' | 'admin'
              -- athlete users: username = USN, athlete_id set
              -- coach: sport = their sport; sport NULL = Physical Education Director (all sports)
              -- faculty: title = 'teacher' | 'proctor' | 'hod'; dept (+ optional sem/section) = scope
timetable   (id, athlete_id, weekday 0=Mon, subject, kind 'class'|'lab')
events      (id, athlete_id, date, kind 'CIE'|'SEE'|'lab', title)
tournaments (id, name, sport, venue, start_date, end_date, travel_before, travel_after,
             created_by, updated_at)
entries     (tournament_id, athlete_id)
sessions    (id, athlete_id, date, minutes, rpe, type)
wellness    (id, athlete_id, date, sleep, soreness, stress)
```

A class is identified by `(dept, sem, section)`. `sem` is text like `"3rd"`. A missing `section` is `''`.

`data.py` helpers you can use:

```python
data.connect(path=None)                      # path or env PLANNER_DB or planner.db; ":memory:" for tests
data.seed(con, today=None)                   # demo data incl. demo user accounts
data.athletes(con) / data.athlete(con, athlete_id) / data.athlete_by_usn(con, usn)
data.athletes_in_class(con, dept, sem, section=None) -> DataFrame
data.timetable(con, athlete_id) / data.events(con, athlete_id)
data.set_timetable(con, athlete_id, rows)    # rows: [{"weekday":0,"subject":"DS","kind":"class"}]; replaces
data.add_event(con, athlete_id, d, kind, title)
data.add_events_bulk(con, athlete_ids, [(date, kind, title), ...])   # skips exact duplicates
data.tournament(con, tid) / data.all_tournaments(con) / data.tournaments_for(con, athlete_id)
data.add_tournament(con, name, sport, venue, start, end, tb, ta, athlete_ids, created_by=None)
data.update_tournament(con, tid, **fields)   # emits tournament_updated
data.set_entries(con, tid, athlete_ids)      # emits entries_changed
```

## 3. Auth (`auth.py`)

```python
@dataclass
class User: id, username, name, role, title, athlete_id, dept, sem, section, sport

auth.can(user, perm) -> bool
auth.require(user, perm)                     # raises PermissionError
auth.visible_athlete_ids(con, user) -> list[int]   # athlete: self; coach: their sport (PED: all);
                                                   # faculty: their dept/sem/section; admin: all
auth.can_view_athlete(con, user, athlete_id) -> bool
auth.current_user() -> User | None           # streamlit session only
auth.create_user(con, username, name, role, password, **scope) -> int
```

Permissions:

| perm | athlete | coach | faculty | admin |
|---|---|---|---|---|
| view_own_data, log_training, request_letter | yes | | | |
| view_squad, manage_tournaments, import_roster | | yes | | yes |
| view_class, approve_letters, import_calendar | | | yes | yes |
| sign_letters_ped | | PED only (sport NULL) | | yes |
| export_data | | yes | yes | yes |
| manage_users | | | | yes |

## 4. Events (`hooks.py`)

```python
hooks.emit(con, event_name, payload: dict)   # calls every feature hook registered for that event
```

Core emits:
- `tournament_created` {tournament_id}
- `tournament_updated` {tournament_id, before: dict, after: dict, changed: [field, ...]}
- `entries_changed` {tournament_id, added: [athlete_id], removed: [athlete_id]}
- `roster_imported` {athlete_ids: [...], created: n, updated: n}
- `events_added` {athlete_ids: [...], count: n}

Features can emit their own events. Name them `<feature>_<something>` and list them in your README.

## 5. Testing

Run tests from the planner folder: `python3 -m pytest -q`. Use `data.connect(":memory:")` in tests. Never write to `planner.db` in the shared folder.

The demo accounts `data.seed` creates:
- Athletes: username = USN, password = USN.
- Coach: `coach.cricket` / `coach`.
- PED: `ped` / `ped`.
- Proctor: `proctor.cse` / `proctor`.
- HoD: `hod.cse` / `hod`.
- Admin: `admin` / `admin`.

## 6. Additions for v3 (the core thread is implementing these now; signatures are fixed)

### Settings (`settings.py`)
The college rules are editable by admins in the Settings tab. Read them and don't hard-code them.

```python
settings.get(con, key)              # typed value, or the default below
settings.set(con, key, value)
settings.all(con) -> dict
settings.letter_template(con) -> dict   # the letter wording (see below)
settings.base_url(con) -> str           # where the app is reachable, for links and QR codes
```

| key | default | meaning |
|---|---|---|
| `attendance_min_pct` | 85 | the attendance floor (%) a student must stay above |
| `on_duty_counts_as_present` | True | whether sport absences with an approved letter count as present |
| `max_on_duty_days` | 0 | the cap on on-duty days per semester (0 = no cap) |
| `semester_start` / `semester_end` | "" | ISO dates. Empty means unknown, so assume the last 120 days |
| `college_name` | "Your College" | |
| `letter_*` | see `settings.DEFAULTS` | letter wording: addressee, through line, request paragraph, signature lines |
| `base_url` | "" | empty means auto-detect (PLANNER_BASE_URL env, then the browser's host, then the LAN IP) |

### Letters (`letterdoc.py`)
Every exemption letter goes through one function, so the template and the QR code are applied the same way everywhere, including bulk letters:

```python
letterdoc.make_letter(con, athlete: dict, tournament: dict, clashes=None) -> bytes
    # clashes=None means they're computed from the timetable and events. Applies settings.letter_template
    # and stamps the verification QR when that feature is installed.
```

### Login sessions
A refresh no longer signs people out. Core keeps a signed session token in the URL query param `s`.
- Don't remove the `s` param in your UI.
- If you set query params, add to them rather than replacing them. Core also uses `p` for the page in view.
- Core table `auth_sessions` belongs to core, so don't touch it.

### Data deletion
The consent/deletion feature should call `data.delete_athlete(con, athlete_id)`. It removes the athlete from the core tables and their login, and emits `athlete_deleted` {athlete_id}. Features must handle that event and delete their own rows for the athlete.
