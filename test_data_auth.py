"""Tests for data.py, auth.py and hooks.py. Run: python3 -m pytest -q"""
from datetime import date, timedelta

import pytest

import auth
import core
import data
import hooks

TODAY = date(2026, 9, 28)


@pytest.fixture
def con():
    c = data.connect(":memory:")
    data.seed(c, TODAY)
    yield c
    c.close()


def uid(con, username):
    return auth.user_by_username(con, username)


# ----------------------------------------------------------------- tournaments

def test_add_tournament_rejects_end_before_start(con):
    with pytest.raises(ValueError):
        data.add_tournament(con, "X", "Cricket", "V", TODAY, TODAY - timedelta(days=1), 0, 0, [])


def test_update_tournament_rejects_end_before_start(con):
    tid = int(data.all_tournaments(con)["id"].iloc[0])
    with pytest.raises(ValueError):
        data.update_tournament(con, tid, end_date=date(2000, 1, 1))


def test_update_tournament_emits_changed_fields(con):
    seen = []
    hooks.on("tournament_updated", lambda c, p: seen.append(p))
    t = data.all_tournaments(con).iloc[0]
    changed = data.update_tournament(con, int(t["id"]), start_date=t["start_date"] + timedelta(days=1),
                                     end_date=t["end_date"] + timedelta(days=1))
    assert changed == ["start_date", "end_date"]
    assert seen[-1]["changed"] == changed and seen[-1]["tournament_id"] == int(t["id"])
    assert data.update_tournament(con, int(t["id"])) == []   # no-op emits nothing new
    assert len(seen) == 1


def test_same_named_tournaments_keep_separate_clashes(con):
    aid = int(data.athletes(con)["id"].iloc[0])
    t1 = data.add_tournament(con, "Dup", "Cricket", "A", TODAY + timedelta(days=3), TODAY + timedelta(days=3), 0, 0, [aid])
    t2 = data.add_tournament(con, "Dup", "Cricket", "B", TODAY + timedelta(days=4), TODAY + timedelta(days=4), 0, 0, [aid])
    tt, ev = data.timetable(con, aid), data.events(con, aid)
    tours = data.tournaments_for(con, aid)
    clash_map = {int(t["id"]): core.find_clashes(t.to_dict(), tt, ev) for _, t in tours.iterrows()}
    assert {c.date for c in clash_map[t1]} == {TODAY + timedelta(days=3)}
    assert {c.date for c in clash_map[t2]} == {TODAY + timedelta(days=4)}


def test_set_entries_reports_added_and_removed(con):
    seen = []
    hooks.on("entries_changed", lambda c, p: seen.append(p))
    tid = int(data.all_tournaments(con)["id"].iloc[0])
    old = data.entries(con, tid)
    new_aid = next(int(a) for a in data.athletes(con)["id"] if int(a) not in old)
    data.set_entries(con, tid, old[1:] + [new_aid])
    assert seen[-1]["added"] == [new_aid] and seen[-1]["removed"] == [old[0]]


def test_failing_hook_does_not_block_write(con):
    def boom(c, p):
        raise RuntimeError("feature bug")
    hooks.on("tournament_created", boom)
    tid = data.add_tournament(con, "Safe", "Cricket", "V", TODAY, TODAY, 0, 0, [])
    assert data.tournament(con, tid)["name"] == "Safe"
    assert any("feature bug" in e for e in hooks.last_errors)
    hooks._handlers["tournament_created"].remove(boom)


def test_add_events_bulk_skips_duplicates(con):
    aid = int(data.athletes(con)["id"].iloc[0])
    item = [(TODAY + timedelta(days=40), "CIE", "Maths CIE-9")]
    assert data.add_events_bulk(con, [aid], item) == 1
    assert data.add_events_bulk(con, [aid], item) == 0


# ----------------------------------------------------------------- reset / demo dates

def test_reset_reseeds_without_deleting_file(tmp_path):
    path = tmp_path / "p.db"
    c = data.connect(path)
    data.seed(c, TODAY)
    data.add_tournament(c, "Extra", "Cricket", "V", TODAY, TODAY, 0, 0, [])
    data.reset(c, TODAY)       # connection stays open: this is what failed on Windows before
    assert "Extra" not in set(data.all_tournaments(c)["name"])
    assert len(data.athletes(c)) == 10
    assert auth.authenticate(c, "admin", data.DEMO_PASSWORD) is not None
    c.close()


def test_demo_dates_roll_forward(con):
    before = data.all_tournaments(con)["start_date"].min()
    shifted = data.refresh_demo_dates(con, TODAY + timedelta(days=30))
    assert shifted == 30
    assert data.all_tournaments(con)["start_date"].min() == before + timedelta(days=30)
    assert data.refresh_demo_dates(con, TODAY + timedelta(days=30)) == 0


def test_demo_dates_frozen_once_real_data(con):
    data.set_meta(con, "demo", "0")
    assert data.refresh_demo_dates(con, TODAY + timedelta(days=30)) == 0


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("CREATE TABLE athletes (id INTEGER PRIMARY KEY, name TEXT, usn TEXT, dept TEXT, sem TEXT, "
                      "sport TEXT, proctor TEXT, phone TEXT);"
                      "CREATE TABLE tournaments (id INTEGER PRIMARY KEY, name TEXT, sport TEXT, venue TEXT, "
                      "start_date TEXT, end_date TEXT, travel_before INTEGER DEFAULT 0, travel_after INTEGER DEFAULT 0);")
    old.close()
    c = data.connect(path)
    cols = {r[1] for r in c.execute("PRAGMA table_info(athletes)")}
    assert {"section", "email"} <= cols
    c.close()


# ----------------------------------------------------------------- auth

def test_passwords_are_hashed_and_checked(con):
    row = con.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()
    assert data.DEMO_PASSWORD not in row[0]
    assert auth.authenticate(con, "admin", data.DEMO_PASSWORD).role == "admin"
    assert auth.authenticate(con, "admin", "wrong") is None
    assert auth.authenticate(con, "nobody", "x") is None


def test_athlete_login_by_usn_is_case_insensitive(con):
    u = auth.authenticate(con, "1rv25cs012", "1RV25CS012")
    assert u is not None and u.role == "athlete"
    assert data.athlete(con, u.athlete_id)["usn"] == "1RV25CS012"


def test_athlete_sees_only_self(con):
    u = uid(con, "1RV25CS012")
    assert auth.visible_athlete_ids(con, u) == [u.athlete_id]
    other = int(data.athlete_by_usn(con, "1RV25CS034")["id"])
    assert not auth.can_view_athlete(con, u, other)


def test_coach_sees_only_their_sport(con):
    u = uid(con, "coach.cricket")
    sports = {data.athlete(con, a)["sport"] for a in auth.visible_athlete_ids(con, u)}
    assert sports == {"Cricket"}


def test_ped_and_admin_see_everyone(con):
    for name in ("ped", "admin"):
        assert len(auth.visible_athlete_ids(con, uid(con, name))) == 10
    assert uid(con, "ped").is_ped and not uid(con, "coach.cricket").is_ped


def test_faculty_scoped_to_department(con):
    u = uid(con, "proctor.cse")
    depts = {data.athlete(con, a)["dept"] for a in auth.visible_athlete_ids(con, u)}
    assert depts == {"CSE"}
    auth.update_user(con, u.id, sem="5th")
    u = uid(con, "proctor.cse")
    assert {data.athlete(con, a)["sem"] for a in auth.visible_athlete_ids(con, u)} == {"5th"}


def test_permission_matrix(con):
    ath, coach, ped = uid(con, "1RV25CS012"), uid(con, "coach.cricket"), uid(con, "ped")
    fac, admin = uid(con, "hod.cse"), uid(con, "admin")
    assert auth.can(ath, "log_training") and not auth.can(ath, "view_squad")
    assert auth.can(coach, "manage_tournaments") and not auth.can(coach, "approve_letters")
    assert auth.can(ped, "sign_letters_ped") and not auth.can(coach, "sign_letters_ped")
    assert auth.can(fac, "approve_letters") and not auth.can(fac, "manage_tournaments")
    assert all(auth.can(admin, p) for p in ("manage_users", "import_roster", "approve_letters"))
    assert not auth.can(None, "view_own_data")
    with pytest.raises(PermissionError):
        auth.require(ath, "manage_users")


def test_set_password_clears_first_login_flag(con):
    u = uid(con, "coach.football")
    con.execute("UPDATE users SET must_change_password=1 WHERE id=?", (u.id,))
    auth.set_password(con, u.id, "newpass1")
    u = auth.authenticate(con, "coach.football", "newpass1")
    assert u and not u.must_change_password
    with pytest.raises(ValueError):
        auth.set_password(con, u.id, "123")


def test_unknown_role_rejected(con):
    with pytest.raises(ValueError):
        auth.create_user(con, "x", "X", "superuser", "password")


def test_password_rules(con):
    u = uid(con, "coach.cricket")
    for bad in ("short", "coach.cricket", "password", "rvce-demo"):
        with pytest.raises(ValueError):
            auth.set_password(con, u.id, bad)
    auth.set_password(con, u.id, "a-good-one-9")


# ----------------------------------------------------------------- remember-me sessions

def test_session_token_roundtrip_and_logout(con):
    u = uid(con, "1RV25CS012")
    tok = auth.create_session(con, u.id, days=1)
    assert auth.user_from_session(con, tok).id == u.id
    stored = con.execute("SELECT token_hash FROM auth_sessions").fetchall()
    assert all(tok not in r[0] for r in stored)          # only the hash is kept
    auth.end_session(con, tok)
    assert auth.user_from_session(con, tok) is None
    assert auth.user_from_session(con, "garbage") is None and auth.user_from_session(con, None) is None


def test_expired_session_rejected(con):
    u = uid(con, "admin")
    tok = auth.create_session(con, u.id, days=-1)
    assert auth.user_from_session(con, tok) is None


# ----------------------------------------------------------------- real-data switch, deletion

def test_start_fresh_removes_demo_logins_and_keeps_settings(con):
    import settings
    settings.set(con, "attendance_min_pct", 75)
    with pytest.raises(ValueError):
        data.start_fresh(con, "sports.office", "short")
    data.start_fresh(con, "sports.office", "Strong-pass-1")
    assert auth.authenticate(con, "admin", data.DEMO_PASSWORD) is None
    assert auth.authenticate(con, "1RV25CS012", "1RV25CS012") is None
    assert auth.authenticate(con, "sports.office", "Strong-pass-1").role == "admin"
    assert data.is_empty(con) and data.get_meta(con, "demo") == "0"
    assert settings.get(con, "attendance_min_pct") == 75
    assert data.refresh_demo_dates(con, TODAY + timedelta(days=30)) == 0


def test_delete_athlete_removes_everything_and_emits(con):
    seen = []
    hooks.on("athlete_deleted", lambda c, p: seen.append(p))
    a = data.athlete_by_usn(con, "1RV25CS012")
    aid = int(a["id"])
    auth.create_session(con, uid(con, "1RV25CS012").id)
    data.delete_athlete(con, aid)
    assert seen[-1] == {"athlete_id": aid}
    for table in ("timetable", "events", "entries", "sessions", "wellness"):
        assert con.execute(f"SELECT COUNT(*) FROM {table} WHERE athlete_id=?", (aid,)).fetchone()[0] == 0
    assert data.athlete(con, aid) is None and uid(con, "1RV25CS012") is None
    assert con.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0


# ----------------------------------------------------------------- settings and letters

def test_settings_defaults_types_and_validation(con):
    import settings
    assert settings.get(con, "attendance_min_pct") == 85
    settings.set(con, "attendance_min_pct", "75")
    assert settings.get(con, "attendance_min_pct") == 75
    with pytest.raises(ValueError):
        settings.set(con, "attendance_min_pct", 120)
    with pytest.raises(KeyError):
        settings.get(con, "nope")


def test_base_url_prefers_setting(con, monkeypatch):
    import settings
    monkeypatch.setattr(settings, "_USER_ENV_URL", None)
    assert settings.base_url(con).startswith("http://")
    settings.set(con, "base_url", "https://planner.example.app/")
    assert settings.base_url(con) == "https://planner.example.app"


def _docx_text(b):
    import io
    from docx import Document
    return "\n".join(p.text for p in Document(io.BytesIO(b)).paragraphs)


def test_letter_uses_admin_wording(con):
    import letterdoc
    import settings
    settings.set(con, "letter_to", "The Dean of Student Affairs, {dept}")
    settings.set(con, "letter_signatures", "Sports Officer | Dean")
    settings.set(con, "letter_request", "Please grant OD for {tournament}. {unknown}")
    a = data.athlete_by_usn(con, "1RV25CS012")
    t = data.tournaments_for(con, int(a["id"])).iloc[0].to_dict()
    text = _docx_text(letterdoc.make_letter(con, a, t))
    assert "The Dean of Student Affairs, CSE" in text
    assert "Sports Officer: ___" in text and "Head of Department:" not in text
    assert f"Please grant OD for {t['name']}. __________" in text
