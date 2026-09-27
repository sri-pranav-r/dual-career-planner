"""
Login, roles and permissions.

Roles: athlete, coach, faculty, admin. A coach with no sport is the Physical
Education Director (PED) and sees every sport. Faculty see athletes in their
department, narrowed by sem/section when those are set on their account.

Passwords are PBKDF2-SHA256 with a per-user salt (stdlib only).
"""
from __future__ import annotations

import hashlib
import hmac
import os
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


def authenticate(con, username, password) -> User | None:
    row = con.execute("SELECT id, password_hash FROM users WHERE username=?", (_norm_username(username),)).fetchone()
    if not row or not verify_password(password, row[1]):
        return None
    return get_user(con, row[0])


COMMON_PASSWORDS = {"password", "12345678", "123456789", "qwertyui", "planner-demo", "admin123", "password1"}


def check_password_strength(password: str, username: str | None = None) -> None:
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters.")
    if password.lower() in COMMON_PASSWORDS or (username and password.lower() == str(username).lower()):
        raise ValueError("Pick a password that isn't your USN/username or a common password.")


def set_password(con, user_id, new_password):
    u = get_user(con, user_id)
    check_password_strength(new_password, u.username if u else None)
    con.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                (hash_password(new_password), int(user_id)))
    con.commit()


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
    if fields_:
        con.execute(f"UPDATE users SET {', '.join(f'{k}=?' for k in fields_)} WHERE id=?",
                    (*[v if v != "" else None for v in fields_.values()], int(user_id)))
        con.commit()


def delete_user(con, user_id):
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


# ----------------------------------------------------------------- remember-me tokens

def create_session(con, user_id, days: float = 30) -> str:
    """New login token. Only its hash is stored."""
    token = os.urandom(24).hex()
    now = datetime.now()
    con.execute("INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES (?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), int(user_id), now.isoformat(timespec="seconds"),
                 (now + timedelta(days=days)).isoformat(timespec="seconds")))
    con.commit()
    return token


def user_from_session(con, token) -> User | None:
    if not token:
        return None
    row = con.execute("SELECT user_id, expires_at FROM auth_sessions WHERE token_hash=?",
                      (hashlib.sha256(str(token).encode()).hexdigest(),)).fetchone()
    if not row or row[1] < datetime.now().isoformat(timespec="seconds"):
        return None
    return get_user(con, row[0])


def end_session(con, token) -> None:
    if token:
        con.execute("DELETE FROM auth_sessions WHERE token_hash=?", (hashlib.sha256(str(token).encode()).hexdigest(),))
        con.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (datetime.now().isoformat(timespec="seconds"),))
        con.commit()


def end_all_sessions(con, user_id) -> None:
    con.execute("DELETE FROM auth_sessions WHERE user_id=?", (int(user_id),))
    con.commit()


# ----------------------------------------------------------------- streamlit session

def current_user() -> User | None:
    import streamlit as st
    return st.session_state.get("user")


SESSION_PARAM = "s"


def login_session(user: User, con=None, remember: bool = False) -> None:
    """Sign in for this browser tab; the token in ?s= keeps it through refreshes (12 h, or 30 days)."""
    import streamlit as st
    st.session_state["user"] = user
    own = con is None
    con = con or data.connect()
    st.query_params[SESSION_PARAM] = create_session(con, user.id, days=30 if remember else 0.5)
    if own:
        con.close()


def restore_session(con) -> User | None:
    """Called on every page load: the user from this tab's state, else from the ?s= token."""
    import streamlit as st
    u = st.session_state.get("user")
    if u is None:
        u = user_from_session(con, st.query_params.get(SESSION_PARAM))
        if u is not None:
            st.session_state["user"] = u
    return u


def logout_session(con=None) -> None:
    import streamlit as st
    st.session_state.pop("user", None)
    token = st.query_params.get(SESSION_PARAM)
    if token:
        own = con is None
        con = con or data.connect()
        end_session(con, token)
        if own:
            con.close()
        del st.query_params[SESSION_PARAM]


def login_form(key: str = "login") -> None:
    """Sign-in form usable on any page (the URL, e.g. ?verify=..., is left as it is)."""
    import streamlit as st
    with st.form(key):
        username = st.text_input("USN or username")
        password = st.text_input("Password", type="password")
        remember = st.checkbox("Keep me signed in on this device for 30 days")
        if st.form_submit_button("Sign in", type="primary"):
            con = data.connect()
            user = authenticate(con, username, password)
            if user:
                login_session(user, con, remember)
                con.close()
                st.rerun()
            con.close()
            st.error("That USN/username and password don't match.")
