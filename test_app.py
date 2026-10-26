"""Headless UI tests: first-run setup on an empty database, then sign in as each role (fixture records)
and open every page in its menu without errors. Run: python3 -m pytest -q test_app.py"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import auth
import data
import testkit

APP = str(Path(__file__).with_name("app.py"))
PW = testkit.PASSWORD
CODE = "ui-setup-code"

ACCOUNTS = [
    ("1RV25CS012", "Hi Aarav"),
    ("coach.cricket", "Needs your attention"),
    ("ped", "Needs your attention"),
    ("proctor.cse", "Needs your attention"),
    ("hod.cse", "Needs your attention"),
    ("admin", "Needs your attention"),
]


@pytest.fixture
def app(tmp_path, monkeypatch):
    """The app on a database holding the test fixtures."""
    monkeypatch.setenv("PLANNER_DB", testkit.seeded_db(tmp_path / "ui.db"))
    return AppTest.from_file(APP, default_timeout=120)


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """The app on a brand-new, empty database."""
    monkeypatch.setenv("PLANNER_DB", str(tmp_path / "fresh.db"))
    monkeypatch.setenv("PLANNER_SETUP_CODE", CODE)
    return AppTest.from_file(APP, default_timeout=120)


def sign_in(at, username, password=PW, agree=True):
    at.run()
    at.text_input[0].input(username)
    at.text_input[1].input(password)
    at.button[0].click().run()
    if agree and any(b.key == "gate_yes" for b in at.button):
        next(b for b in at.button if b.key == "gate_yes").click().run()
    return at


def texts(at):
    return " ".join(str(getattr(e, "value", "")) for e in list(at.title) + list(at.header) + list(at.subheader)
                    + list(at.markdown) + list(at.info) + list(at.error) + list(at.caption) + list(at.warning))


def menu(at):
    """Page keys in the sidebar menu, in order."""
    return [b.key[len("nav_"):] for b in at.button if (b.key or "").startswith("nav_")]


def fields(at):
    """Text inputs by label (the first one when a label repeats, e.g. the setup code on two tabs)."""
    out = {}
    for t in at.text_input:
        out.setdefault(t.label, t)
    return out


def open_page(at, key):
    at.query_params["p"] = key
    return at.run()


# ----------------------------------------------------------------- first run

def test_fresh_database_asks_for_setup_and_has_no_sign_in(fresh):
    fresh.run()
    assert not fresh.exception
    assert "Set up the planner" in [t.value for t in fresh.title]
    assert "demo" not in texts(fresh).lower()


def test_setup_creates_admin_and_signs_in(fresh):
    fresh.run()
    form = fields(fresh)
    form["Setup code"].input(CODE)
    form["Admin username"].input("sports.office")
    form["Full name"].input("Sports Office")
    form[f"Password ({auth.ADMIN_MIN_LENGTH}+ characters)"].input("quiet-harbour-lamp-3")
    form["Repeat the password"].input("quiet-harbour-lamp-3")
    next(b for b in fresh.button if b.label == "Create admin account").click().run()
    assert not fresh.exception, [e.value for e in fresh.exception]
    assert "Hi Sports Office" in texts(fresh)
    assert "settings" in menu(fresh) and "users" in menu(fresh)
    for key in menu(fresh):                       # every admin page works on an empty planner
        open_page(fresh, key)
        assert not fresh.exception, (key, [e.value for e in fresh.exception])
        assert not [e.value for e in fresh.error if "couldn't load" in e.value], key


def test_setup_rejects_wrong_code(fresh):
    fresh.run()
    form = fields(fresh)
    form["Setup code"].input("nope")
    form["Admin username"].input("sports.office")
    form[f"Password ({auth.ADMIN_MIN_LENGTH}+ characters)"].input("quiet-harbour-lamp-3")
    form["Repeat the password"].input("quiet-harbour-lamp-3")
    next(b for b in fresh.button if b.label == "Create admin account").click().run()
    assert any("setup code" in e.value for e in fresh.error)
    assert "Set up the planner" in [t.value for t in fresh.title]


def test_old_sample_database_is_blocked(tmp_path, monkeypatch):
    path = testkit.seeded_db(tmp_path / "old.db")
    c = data.connect(path)
    data.set_meta(c, "demo", "1")
    c.close()
    monkeypatch.setenv("PLANNER_DB", path)
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert "This database holds sample records" in [t.value for t in at.title]
    at.text_input[0].input("CLEAR")
    next(b for b in at.button if b.label == "Clear the database").click().run()
    assert "Set up the planner" in [t.value for t in at.title]


# ----------------------------------------------------------------- signed in

def test_login_page_shows_and_rejects_bad_password(app):
    at = sign_in(app, "admin", "nope")
    assert not at.exception
    assert any("don't match" in e.value for e in at.error)
    assert "demo" not in texts(at).lower()


def test_login_locks_after_repeated_failures(app):
    at = app.run()
    for _ in range(auth.MAX_FAILURES + 1):
        at.text_input[0].input("ped")
        at.text_input[1].input("wrong-guess")
        at.button[0].click().run()
    assert any("Too many wrong passwords" in e.value for e in at.error)


@pytest.mark.parametrize("username,expect", ACCOUNTS)
def test_each_role_renders(app, username, expect):
    at = sign_in(app, username)
    assert not at.exception, [e.value for e in at.exception]
    assert expect in texts(at)


@pytest.mark.parametrize("username", [u for u, _ in ACCOUNTS])
def test_every_page_in_the_menu_opens(app, username):
    at = sign_in(app, username)
    keys = menu(at)
    assert keys[0] == "home" and "help" in keys
    for key in keys:
        open_page(at, key)
        assert not at.exception, (key, [e.value for e in at.exception])
        assert not [e.value for e in at.error if "couldn't load" in e.value], key
        assert at.header, key                         # every page has a title ...
        assert any("page-blurb" in str(getattr(h, "proto", "")) for h in at.get("html")), key   # ... and an explanation
        assert "demo" not in texts(at).lower(), key


def test_menu_button_switches_page(app):
    at = sign_in(app, "1RV25CS012")
    next(b for b in at.button if b.key == "nav_clashes").click().run()
    assert at.header[0].value == "Clashes" and not at.exception


def test_faculty_never_see_training_or_wellness(app):
    at = sign_in(app, "proctor.cse")
    open_page(at, "detail")
    labels = [t.label for t in at.tabs]
    assert "Clashes" in labels and "Training load" not in labels and "Wellness" not in labels


def test_coach_only_sees_their_sport(app):
    at = open_page(sign_in(app, "coach.cricket"), "squad")
    squad = at.dataframe[0].value
    assert set(squad["Sport"]) == {"Cricket"}


def test_sign_in_token_never_in_the_address(app):
    at = sign_in(app, "1RV25CS012")
    assert "s" not in at.query_params
    assert at.session_state["session_token"]
    action, token, max_age = at.session_state["cookie_action"]
    assert action == "set" and token == at.session_state["session_token"] and max_age == 12 * 3600


def test_old_token_in_address_is_ignored_and_dropped(app):
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params["s"] = "anything"
    at.run()
    assert "s" not in at.query_params and at.text_input[0].label == "USN or username"


def test_admin_settings_has_rules_and_backup(app):
    at = sign_in(app, "admin")
    assert "settings" in menu(at)
    t = texts(open_page(at, "settings"))
    assert "College rules" in t and "Backup and restore" in t


def test_new_staff_account_gets_a_one_time_password(app):
    at = open_page(sign_in(app, "admin"), "users")
    form = fields(at)
    form["Username"].input("coach.hockey")
    form["Full name"].input("Hockey Coach")
    next(b for b in at.button if b.label == "Create account").click().run()
    assert not at.exception
    pw = at.code[0].value
    c = data.connect()
    u = auth.authenticate(c, "coach.hockey", pw)
    c.close()
    assert u is not None and u.must_change_password


def test_first_sign_in_with_one_time_password_forces_a_change(app):
    c = data.connect()
    pw = auth.reset_password(c, auth.user_by_username(c, "coach.cricket").id)
    c.close()
    at = sign_in(app, "coach.cricket", pw)
    assert "Set a new password" in [t.value for t in at.title]
    at.text_input[0].input("my-own-cricket-pass")
    at.text_input[1].input("my-own-cricket-pass")
    at.button[0].click().run()
    assert "Needs your attention" in texts(at)


def test_athlete_must_agree_to_data_notice_first(app):
    at = sign_in(app, "1RV25CS034", agree=False)
    assert not at.exception
    assert "Before you start" in " ".join(t.value for t in at.title)
    assert "Hi Diya" not in texts(at)
    next(b for b in at.button if b.key == "gate_no").click().run()
    assert "Hi Diya" not in texts(at) and not at.exception       # privacy page only
    next(b for b in at.button if b.key == "gate_yes").click().run()
    assert "Hi Diya" in texts(at)


def test_athlete_cannot_see_staff_tabs(app):
    at = sign_in(app, "1RV25CS012")
    keys = menu(at)
    assert "squad" not in keys and "users" not in keys and "roster" not in keys
    assert "Squad overview" not in texts(open_page(at, "squad"))   # a staff key in the URL falls back to Home
    assert at.header[0].value.startswith("Hi Aarav")
