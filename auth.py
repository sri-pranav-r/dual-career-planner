"""
Login, roles and permissions.

Roles: athlete, coach, faculty, admin. A coach with no sport is the Physical
Education Director (PED) and sees every sport. Faculty see athletes in their
department, narrowed by sem/section when those are set on their account.

Passwords are PBKDF2-SHA256 with a per-user salt (stdlib only). There are no
default passwords: the first admin chooses one at setup, and every other account
starts with a random one-time password that must be changed at first sign-in.
After repeated wrong passwords an account is locked for a while.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, fields
from datetime import datetime, timedelta

import data

ROLES = ("athlete", "coach", "faculty", "admin")

PERMISSIONS = {
    "athlete": {"view_own_data", "log_training", "request_letter"},
    "coach": {"view_squad", "manage_tournaments", "import_roster", "export_data"},
    "faculty": {"view_class", "approve_letters", "import_calendar", "export_data"},
    "admin": {"view_squad", "manage_tournaments", "import_roster", "view_class", "approve_letters",
              "import_calendar", "sign_letters_ped", "export_data", "manage_users"},
}

_ITERATIONS = 200_000


@dataclass
class User:
    id: int
    username: str
    name: str
    role: str
    title: str = ""
    athlete_id: int | None = None
    dept: str | None = None
    sem: str | None = None
    section: str | None = None
    sport: str | None = None
    must_change_password: bool = False

    @property
    def is_ped(self) -> bool:
        return self.role == "coach" and not self.sport


# ----------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, AttributeError):
        return False


_DUMMY_HASH = hash_password(os.urandom(8).hex())


# ----------------------------------------------------------------- accounts

def _norm_username(username: str) -> str:
    return str(username).strip().lower()


def _row_to_user(row: dict) -> User:
    names = {f.name for f in fields(User)}
    u = {k: row[k] for k in names if k in row}
    u["must_change_password"] = bool(u.get("must_change_password"))
    if u.get("athlete_id") is not None:
        u["athlete_id"] = int(u["athlete_id"])
    u["id"] = int(u["id"])
    for k in ("dept", "sem", "section", "sport", "title"):
        if u.get(k) == "":
            u[k] = None if k != "title" else ""
    return User(**u)


def create_user(con, username, name, role, password, *, title="", athlete_id=None, dept=None, sem=None,
                section=None, sport=None, must_change_password=False) -> int:
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}")
    cur = con.execute(
        "INSERT INTO users(username,name,role,title,athlete_id,dept,sem,section,sport,password_hash,"
        "must_change_password,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (_norm_username(username), name, role, title or "", athlete_id, dept, sem, section, sport or None,
         hash_password(password), int(must_change_password), datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return cur.lastrowid


def get_user(con, user_id) -> User | None:
    df = data.q(con, "SELECT * FROM users WHERE id=?", (int(user_id),))
    return None if df.empty else _row_to_user(df.iloc[0].to_dict())


def user_by_username(con, username) -> User | None:
    df = data.q(con, "SELECT * FROM users WHERE username=?", (_norm_username(username),))
    return None if df.empty else _row_to_user(df.iloc[0].to_dict())


MAX_FAILURES = 5          # wrong passwords in a row ...
LOCK_MINUTES = 15         # ... lock the username for this long


class AccountLocked(Exception):
    pass


def _recent_failures(con, name) -> int:
    since = (datetime.now() - timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
    return con.execute("SELECT COUNT(*) FROM login_failures WHERE username=? AND at>=?", (name, since)).fetchone()[0]


def authenticate(con, username, password) -> User | None:
    """The user, or None for a wrong username or password. Raises AccountLocked after too many failures."""
    name = _norm_username(username)
    if _recent_failures(con, name) >= MAX_FAILURES:
        raise AccountLocked(f"Too many wrong passwords. Try again in {LOCK_MINUTES} minutes, or ask the sports "
                            "office admin to reset your password.")
    row = con.execute("SELECT id, password_hash FROM users WHERE username=?", (name,)).fetchone()
    if not row or not verify_password(password, row[1]):
        if not row:
            verify_password(password, _DUMMY_HASH)   # same time taken whether or not the username exists
        now = datetime.now()
        con.execute("INSERT INTO login_failures(username, at) VALUES (?,?)", (name, now.isoformat(timespec="seconds")))
        con.execute("DELETE FROM login_failures WHERE at < ?", ((now - timedelta(days=1)).isoformat(timespec="seconds"),))
        con.commit()
        return None
    con.execute("DELETE FROM login_failures WHERE username=?", (name,))
    con.commit()
    return get_user(con, row[0])


MIN_LENGTH = 10
ADMIN_MIN_LENGTH = 12
COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "12345678", "123456789", "1234567890", "qwertyuiop",
    "qwerty123", "iloveyou", "admin123", "administrator", "welcome1", "welcome123", "letmein123", "changeme",
    "football", "cricket123", "sunshine1", "trustno1", "abc123456", "11111111", "00000000", "1q2w3e4r5t",
}


def check_password_strength(password: str, username: str | None = None, min_length: int = MIN_LENGTH) -> None:
    pw = password or ""
    if len(pw) < min_length:
        raise ValueError(f"Password must be at least {min_length} characters.")
    low = pw.lower()
    if low in COMMON_PASSWORDS or len(set(low)) < 4:
        raise ValueError("That password is too easy to guess. Try a few unrelated words.")
    name = str(username or "").strip().lower()
    if name and (name in low if len(name) >= 4 else low == name):   # short names like "ped" only as the whole password
        raise ValueError("The password can't contain your USN or username.")


# No 0/O, 1/l/I: these are read off a printout or a phone screen.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def temporary_password() -> str:
    """A random one-time password like 'k7pm-x3qt-9wbn', for new accounts and resets."""
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(3))


def set_password(con, user_id, new_password):
    """The person's own new password. Signs out their other devices."""
    u = get_user(con, user_id)
    check_password_strength(new_password, u.username if u else None,
                            ADMIN_MIN_LENGTH if u and u.role == "admin" else MIN_LENGTH)
    con.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                (hash_password(new_password), int(user_id)))
    con.commit()


def reset_password(con, user_id) -> str:
    """An admin reset: a new one-time password, to be changed at next sign-in. Ends the person's sessions."""
    pw = temporary_password()
    con.execute("UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
                (hash_password(pw), int(user_id)))
    con.execute("DELETE FROM auth_sessions WHERE user_id=?", (int(user_id),))
    con.execute("DELETE FROM login_failures WHERE username=(SELECT username FROM users WHERE id=?)", (int(user_id),))
    con.commit()
    return pw


def retire_username_passwords(con) -> list[str]:
    """
    Versions before v7 gave imported athletes their USN as the first password. Any account still
    using its username as its password gets an unknown random one instead (an admin resets it on
    the Users page to hand out a one-time password). Runs once per database. Returns the usernames.
    """
    if data.get_meta(con, "checked_username_passwords") == "1":
        return []
    retired = []
    # Only first passwords that were never changed can be the USN (choosing your username was always refused).
    for uid_, username, stored in con.execute(
            "SELECT id, username, password_hash FROM users WHERE must_change_password=1").fetchall():
        if verify_password(username, stored) or verify_password(username.upper(), stored):
            con.execute("UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
                        (hash_password(secrets.token_urlsafe(24)), uid_))
            con.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid_,))
            retired.append(username)
    data.set_meta(con, "checked_username_passwords", "1")
    return retired


def admin_count(con) -> int:
    return con.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]


def users(con):
    return data.q(con, "SELECT id, username, name, role, title, dept, sem, section, sport, athlete_id, "
                       "must_change_password FROM users ORDER BY role, username")


def update_user(con, user_id, **fields_):
    allowed = {"name", "role", "title", "dept", "sem", "section", "sport"}
    bad = set(fields_) - allowed
    if bad:
        raise ValueError(f"Cannot update {sorted(bad)}")
    if "role" in fields_ and fields_["role"] not in ROLES:
        raise ValueError(f"Unknown role {fields_['role']!r}")
    u = get_user(con, user_id)
    if u and u.role == "admin" and fields_.get("role", "admin") != "admin" and admin_count(con) <= 1:
        raise ValueError("This is the only admin account. Make another admin first.")
    if fields_:
        con.execute(f"UPDATE users SET {', '.join(f'{k}=?' for k in fields_)} WHERE id=?",
                    (*[v if v != "" else None for v in fields_.values()], int(user_id)))
        con.commit()


def delete_user(con, user_id):
    u = get_user(con, user_id)
    if u and u.role == "admin" and admin_count(con) <= 1:
        raise ValueError("This is the only admin account, so it can't be deleted.")
    con.execute("DELETE FROM auth_sessions WHERE user_id=?", (int(user_id),))
    con.execute("DELETE FROM users WHERE id=?", (int(user_id),))
    con.commit()


# ----------------------------------------------------------------- permissions

def can(user: User | None, perm: str) -> bool:
    if user is None:
        return False
    if perm == "sign_letters_ped" and user.is_ped:
        return True
    return perm in PERMISSIONS.get(user.role, set())


def require(user: User | None, perm: str) -> None:
    if not can(user, perm):
        raise PermissionError(f"{getattr(user, 'username', 'anonymous')} lacks {perm}")


def visible_athlete_ids(con, user: User | None) -> list[int]:
    if user is None:
        return []
    if user.role == "athlete":
        return [user.athlete_id] if user.athlete_id is not None else []
    if user.role == "admin" or user.is_ped:
        sql, params = "SELECT id FROM athletes", ()
    elif user.role == "coach":
        sql, params = "SELECT id FROM athletes WHERE lower(sport)=lower(?)", (user.sport,)
    elif user.role == "faculty":
        if not user.dept:
            return []
        sql, params = "SELECT id FROM athletes WHERE dept=?", [user.dept]
        if user.sem:
            sql += " AND sem=?"; params.append(user.sem)
        if user.section:
            sql += " AND section=?"; params.append(user.section)
    else:
        return []
    return [r[0] for r in con.execute(sql + " ORDER BY name", params)]


def can_view_athlete(con, user: User | None, athlete_id) -> bool:
    return athlete_id is not None and int(athlete_id) in visible_athlete_ids(con, user)


# ----------------------------------------------------------------- sign-in tokens

def _token_hash(token) -> str:
    return hashlib.sha256(str(token).encode()).hexdigest()


def create_session(con, user_id, days: float = 30) -> str:
    """New sign-in token. Only its hash is stored."""
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    con.execute("INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES (?,?,?,?)",
                (_token_hash(token), int(user_id), now.isoformat(timespec="seconds"),
                 (now + timedelta(days=days)).isoformat(timespec="seconds")))
    con.commit()
    return token


def user_from_session(con, token) -> User | None:
    if not token:
        return None
    row = con.execute("SELECT user_id, expires_at FROM auth_sessions WHERE token_hash=?", (_token_hash(token),)).fetchone()
    if not row or row[1] < datetime.now().isoformat(timespec="seconds"):
        return None
    return get_user(con, row[0])


def session_expiry(con, token) -> datetime | None:
    row = con.execute("SELECT expires_at FROM auth_sessions WHERE token_hash=?", (_token_hash(token),)).fetchone()
    return datetime.fromisoformat(row[0]) if row else None


def end_session(con, token) -> None:
    if token:
        con.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_token_hash(token),))
    con.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (datetime.now().isoformat(timespec="seconds"),))
    con.commit()


def end_all_sessions(con, user_id, keep_token=None) -> None:
    """Sign a person out everywhere (optionally except the current browser)."""
    if keep_token:
        con.execute("DELETE FROM auth_sessions WHERE user_id=? AND token_hash<>?", (int(user_id), _token_hash(keep_token)))
    else:
        con.execute("DELETE FROM auth_sessions WHERE user_id=?", (int(user_id),))
    con.commit()


# ----------------------------------------------------------------- streamlit session
#
# The sign-in token lives in a cookie, never in the page address, so copying a link
# never shares a signed-in session. The browser sends the cookie when the page opens
# (st.context.cookies); a tiny script sets or clears it, because Streamlit itself
# can't write cookies (app.write_cookie runs it).

COOKIE = "planner_session"
LEGACY_PARAM = "s"   # versions before v7 kept the token in ?s=; it is dropped from the address


def current_user() -> User | None:
    import streamlit as st
    return st.session_state.get("user")


def _cookie_token() -> str | None:
    import streamlit as st
    try:
        return st.context.cookies.get(COOKIE)
    except Exception:  # noqa: BLE001 - no request context (tests)
        return None


def login_session(user: User, con=None, remember: bool = False) -> None:
    """Sign in for this browser: 12 hours, or 30 days when "keep me signed in" is ticked."""
    import streamlit as st
    own = con is None
    con = con or data.connect()
    days = 30 if remember else 0.5
    token = create_session(con, user.id, days=days)
    if own:
        con.close()
    st.session_state["user"] = user
    st.session_state["session_token"] = token
    st.session_state["cookie_action"] = ("set", token, int(days * 86400))


def restore_session(con) -> User | None:
    """Called on every page load: the user from this tab's state, else from the sign-in cookie."""
    import streamlit as st
    if LEGACY_PARAM in st.query_params:
        del st.query_params[LEGACY_PARAM]
    u = st.session_state.get("user")
    token = st.session_state.get("session_token")
    if u is not None and token and user_from_session(con, token) is None:
        u = None                       # signed out elsewhere, expired, or the password was reset
        st.session_state.pop("user", None)
        st.session_state.pop("session_token", None)
    if u is None and st.session_state.get("cookie_action", ("",))[0] != "clear":
        token = _cookie_token()
        u = user_from_session(con, token)
        if u is not None:
            st.session_state["user"] = u
            st.session_state["session_token"] = token
    return u


def logout_session(con=None) -> None:
    import streamlit as st
    st.session_state.pop("user", None)
    token = st.session_state.pop("session_token", None) or _cookie_token()
    own = con is None
    con = con or data.connect()
    end_session(con, token)
    if own:
        con.close()
    st.session_state["cookie_action"] = ("clear", "", 0)


def cookie_script() -> str | None:
    """The script that stores or clears the sign-in cookie in this browser, if one is due."""
    import json

    import streamlit as st
    action = st.session_state.get("cookie_action")
    if not action:
        return None
    kind, token, max_age = action
    value = f"{COOKIE}={token}; Path=/; SameSite=Strict; Max-Age={max_age if kind == 'set' else 0}"
    # Runs either in the app page (st.html) or in a same-origin component frame (older Streamlit),
    # so it writes to the app page's document, whichever window that is.
    return ("<script>(function(){var w=window;try{if(window.parent&&window.parent.document){w=window.parent;}}"
            "catch(e){}var c=" + json.dumps(value) + ";"
            "if(w.location.protocol==='https:'){c+='; Secure';}"
            "w.document.cookie=c;})();</script>")


def login_form(key: str = "login") -> None:
    """Sign-in form usable on any page (the URL, e.g. ?verify=..., is left as it is)."""
    import streamlit as st
    with st.form(key):
        username = st.text_input("USN or username")
        password = st.text_input("Password", type="password")
        remember = st.checkbox("Keep me signed in on this device for 30 days")
        if st.form_submit_button("Sign in", type="primary"):
            con = data.connect()
            try:
                user = authenticate(con, username, password)
            except AccountLocked as err:
                con.close()
                st.error(str(err))
                return
            if user:
                login_session(user, con, remember)
                con.close()
                st.rerun()
            con.close()
            st.error("That USN/username and password don't match.")
