"""First-run setup: no accounts, no default password, setup code required. Run: python3 -m pytest -q"""
import pytest

import auth
import data
import firstrun
import settings
import testkit

CODE = "test-setup-code"


@pytest.fixture
def empty(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANNER_SETUP_CODE", CODE)
    c = data.connect(tmp_path / "new.db")
    yield c
    c.close()


def test_first_admin_needs_the_setup_code(empty):
    with pytest.raises(ValueError, match="setup code"):
        firstrun.create_first_admin(empty, "wrong", "sports.office", "Office", "a-long-admin-pass", "a-long-admin-pass")
    assert not data.has_admin(empty)


def test_first_admin_needs_a_strong_password(empty):
    for pw in ("short-pass", "sports.office-2026", "password123"):
        with pytest.raises(ValueError):
            firstrun.create_first_admin(empty, CODE, "sports.office", "Office", pw, pw)
    with pytest.raises(ValueError, match="match"):
        firstrun.create_first_admin(empty, CODE, "sports.office", "Office", "a-long-admin-pass", "a-long-admin-pas")


def test_first_admin_created_once(empty):
    uid = firstrun.create_first_admin(empty, CODE, " Sports.Office ", "Sports Office", "blue-river-candle-7",
                                      "blue-river-candle-7", college="Example College of Engineering")
    u = auth.authenticate(empty, "sports.office", "blue-river-candle-7")
    assert u.id == uid and u.role == "admin" and not u.must_change_password
    assert settings.get(empty, "college_name") == "Example College of Engineering"
    with pytest.raises(ValueError, match="already exists"):
        firstrun.create_first_admin(empty, CODE, "intruder", "X", "another-long-pass", "another-long-pass")


def test_setup_code_tries_are_limited(empty):
    for _ in range(auth.MAX_FAILURES):
        with pytest.raises(ValueError):
            firstrun.check_code(empty, "guess")
    with pytest.raises(ValueError, match="Too many"):
        firstrun.check_code(empty, CODE)


def test_generated_code_when_no_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PLANNER_SETUP_CODE", raising=False)
    monkeypatch.setattr(firstrun, "_code_logged", False)
    c = data.connect(tmp_path / "gen.db")
    firstrun.prepare(c)
    line = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("Planner setup code:")][0]
    code = line.split(":", 1)[1].split()[0]
    assert data.get_meta(c, "setup_code_hash") and code not in data.get_meta(c, "setup_code_hash")
    firstrun.create_first_admin(c, code, "office", "Office", "green-paper-lamp-4", "green-paper-lamp-4")
    assert data.has_admin(c) and data.get_meta(c, "setup_code_hash") is None
    c.close()


def test_restore_backup_at_setup(empty, tmp_path):
    src = data.connect(tmp_path / "src.db")
    testkit.seed(src)
    raw = data.backup_bytes(src)
    src.close()
    with pytest.raises(ValueError):
        firstrun.restore_backup(empty, "wrong", raw)
    res = firstrun.restore_backup(empty, CODE, raw)
    assert res["athletes"] == 10 and data.has_admin(empty)
    with pytest.raises(ValueError, match="already set up"):
        firstrun.restore_backup(empty, CODE, raw)
