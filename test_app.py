"""Headless UI smoke test: sign in as each role and render every tab without errors.
Run: python3 -m pytest -q test_app.py"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).with_name("app.py"))

ACCOUNTS = [
    ("1RV25CS012", "1RV25CS012", "Hi Aarav"),
    ("coach.cricket", "rvce-demo", "Squad overview"),
    ("ped", "rvce-demo", "Squad overview"),
    ("proctor.cse", "rvce-demo", "Athletes away"),
    ("hod.cse", "rvce-demo", "Athletes away"),
    ("admin", "rvce-demo", "Squad overview"),
]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANNER_DB", str(tmp_path / "ui.db"))
    return AppTest.from_file(APP, default_timeout=120)


def sign_in(at, username, password, agree=True):
    at.run()
    at.text_input[0].input(username)
    at.text_input[1].input(password)
    at.button[0].click().run()
    if agree and any(b.key == "gate_yes" for b in at.button):
        next(b for b in at.button if b.key == "gate_yes").click().run()
    return at


def texts(at):
    return " ".join(str(getattr(e, "value", "")) for e in list(at.subheader) + list(at.markdown) + list(at.info) + list(at.error))


def test_login_page_shows_and_rejects_bad_password(app):
    at = sign_in(app, "admin", "nope")
    assert not at.exception
    assert any("don't match" in e.value for e in at.error)


@pytest.mark.parametrize("username,password,expect", ACCOUNTS)
def test_each_role_renders(app, username, password, expect):
    at = sign_in(app, username, password)
    assert not at.exception, [e.value for e in at.exception]
    assert expect in texts(at)


def test_coach_only_sees_their_sport(app):
    at = sign_in(app, "coach.cricket", "rvce-demo")
    squad = at.dataframe[0].value
    assert set(squad["Sport"]) == {"Cricket"}


def test_refresh_keeps_you_signed_in(app):
    at = sign_in(app, "1RV25CS012", "1RV25CS012")
    token = at.query_params.get("s")
    assert token
    fresh = AppTest.from_file(APP, default_timeout=120)
    fresh.query_params["s"] = token[0] if isinstance(token, list) else token
    fresh.run()
    assert not fresh.exception and "Hi Aarav" in texts(fresh)


def test_admin_settings_tab_renders(app):
    at = sign_in(app, "admin", "rvce-demo")
    assert "Settings" in [t.label for t in at.tabs]
    assert "College rules" in texts(at)


def test_athlete_must_agree_to_data_notice_first(app):
    at = sign_in(app, "1RV25CS034", "1RV25CS034", agree=False)
    assert not at.exception
    assert "Before you start" in " ".join(t.value for t in at.title)
    assert "Hi Diya" not in texts(at)
    next(b for b in at.button if b.key == "gate_no").click().run()
    assert "Hi Diya" not in texts(at) and not at.exception       # privacy page only
    next(b for b in at.button if b.key == "gate_yes").click().run()
    assert "Hi Diya" in texts(at)


def test_athlete_cannot_see_staff_tabs(app):
    at = sign_in(app, "1RV25CS012", "1RV25CS012")
    labels = [t.label for t in at.tabs]
    assert "Squad" not in labels and "Users" not in labels and "Roster import" not in labels
