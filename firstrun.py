"""
First-run setup: creating the first admin on an empty database.

A new deployment has no accounts and no default password. The first person to
open it is asked for a setup code before they can create the admin account, so
a stranger who finds the address first can't take it over. The code is the
PLANNER_SETUP_CODE secret when one is set; otherwise a random code is made when
the app starts and written to the server log (Streamlit Cloud: Manage app, then
the logs panel).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import sys
from datetime import datetime, timedelta

import auth
import config
import data
import settings

log = logging.getLogger("planner")

_META_KEY = "setup_code_hash"
_FAIL_KEY = "__setup__"          # setup attempts share the sign-in lock-out table under this name
_code_logged = False             # one fresh code per server process


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


def code_from_secret() -> bool:
    return bool(config.get("PLANNER_SETUP_CODE"))


def prepare(con) -> None:
    """Make sure a setup code exists. Without a configured one, make a new one and write it to the log."""
    global _code_logged
    if code_from_secret() or (_code_logged and data.get_meta(con, _META_KEY)):
        return
    code = auth.temporary_password()
    data.set_meta(con, _META_KEY, _hash(code))
    _code_logged = True
    # Printed as well as logged, so it shows in the Streamlit Cloud log panel whatever the log settings.
    print(f"Planner setup code: {code}  (enter it on the setup page to create the first admin account)",
          file=sys.stderr, flush=True)


def check_code(con, entered: str) -> None:
    """Raise ValueError when the code is wrong or there were too many wrong tries."""
    since = (datetime.now() - timedelta(minutes=auth.LOCK_MINUTES)).isoformat(timespec="seconds")
    tries = con.execute("SELECT COUNT(*) FROM login_failures WHERE username=? AND at>=?", (_FAIL_KEY, since)).fetchone()[0]
    if tries >= auth.MAX_FAILURES:
        raise ValueError(f"Too many wrong setup codes. Wait {auth.LOCK_MINUTES} minutes and try again.")
    configured = config.get("PLANNER_SETUP_CODE")
    entered = (entered or "").strip()
    ok = bool(entered) and (hmac.compare_digest(entered, configured.strip()) if configured
                            else hmac.compare_digest(_hash(entered), data.get_meta(con, _META_KEY) or ""))
    if not ok:
        con.execute("INSERT INTO login_failures(username, at) VALUES (?,?)",
                    (_FAIL_KEY, datetime.now().isoformat(timespec="seconds")))
        con.commit()
        raise ValueError("That setup code isn't right. Check the secret you set, or the latest code in the server log.")


def create_first_admin(con, code: str, username: str, name: str, password: str, repeat: str,
                       college: str = "") -> int:
    """Create the first admin account. Only works while there is no admin. Returns the new user id."""
    if data.has_admin(con):
        raise ValueError("An admin account already exists. Sign in instead.")
    check_code(con, code)
    username = (username or "").strip().lower()
    if not username or " " in username:
        raise ValueError("Choose a username without spaces, for example sports.office.")
    if password != repeat:
        raise ValueError("The two passwords don't match.")
    auth.check_password_strength(password, username, auth.ADMIN_MIN_LENGTH)
    if auth.user_by_username(con, username):
        raise ValueError("That username is taken.")
    uid = auth.create_user(con, username, (name or "").strip() or username, "admin", password)
    if college.strip():
        settings.set(con, "college_name", college.strip())
    finish(con)
    log.info("First admin account created: %s", username)
    return uid


def restore_backup(con, code: str, raw: bytes) -> dict:
    """Load a backup into an empty deployment (for example after Streamlit Cloud restarted the app)."""
    if data.has_admin(con):
        raise ValueError("This planner is already set up. An admin can restore a backup from Settings.")
    check_code(con, code)
    res = data.restore_bytes(con, raw)
    finish(con)
    return res


def finish(con) -> None:
    con.execute("DELETE FROM meta WHERE key=?", (_META_KEY,))
    con.execute("DELETE FROM login_failures WHERE username=?", (_FAIL_KEY,))
    con.commit()

