"""
College rules and letter wording, editable by admins (Settings tab) and
stored in the core `meta` table under "setting.<key>".

Defaults are placeholders until RVCE confirms its attendance rule and
letter format; change them in the app, not in code.
"""
from __future__ import annotations

import json
import os
import socket

import core
import data

DEFAULTS: dict = {
    # attendance
    "attendance_min_pct": 85,
    "on_duty_counts_as_present": True,
    "max_on_duty_days": 0,            # 0 = no cap
    "semester_start": "",             # ISO date; "" = unknown
    "semester_end": "",
    # letter (wording defaults live in core.DEFAULT_LETTER)
    **core.DEFAULT_LETTER,
    # links
    "base_url": "",                   # "" = auto-detect
}

LABELS = {
    "attendance_min_pct": "Minimum attendance (%)",
    "on_duty_counts_as_present": "Approved sport absences count as present (on-duty)",
    "max_on_duty_days": "Maximum on-duty days per semester (0 = no limit)",
    "semester_start": "Semester start date",
    "semester_end": "Semester end date",
    "college_name": "College name",
    "letter_to": "Addressed to",
    "letter_through": "Through line",
    "letter_subject": "Subject line",
    "letter_salutation": "Salutation",
    "letter_request": "Request paragraph",
    "letter_closing": "Closing",
    "letter_signatures": "Signature lines (separate with |)",
    "base_url": "App address for QR codes and links",
}


def get(con, key):
    if key not in DEFAULTS:
        raise KeyError(key)
    raw = data.get_meta(con, f"setting.{key}")
    if raw is None:
        return DEFAULTS[key]
    return json.loads(raw)


def set(con, key, value):  # noqa: A001 - mirrors get()
    if key not in DEFAULTS:
        raise KeyError(key)
    default = DEFAULTS[key]
    if isinstance(default, bool):
        value = bool(value)
    elif isinstance(default, int):
        value = int(value)
        if key == "attendance_min_pct" and not 0 <= value <= 100:
            raise ValueError("Attendance must be between 0 and 100.")
        if value < 0:
            raise ValueError(f"{LABELS[key]} can't be negative.")
    else:
        value = str(value).strip()
    data.set_meta(con, f"setting.{key}", json.dumps(value))


def all(con) -> dict:  # noqa: A001
    return {k: get(con, k) for k in DEFAULTS}


def letter_template(con) -> dict:
    t = {k: get(con, k) for k in DEFAULTS if k.startswith("letter_")}
    t["college_name"] = get(con, "college_name")
    return t


# ----------------------------------------------------------------- app address

# Captured at import, before app.py exports the resolved address into PLANNER_BASE_URL
# for the verification feature, so a value the user set themselves still wins.
_USER_ENV_URL = os.environ.get("PLANNER_BASE_URL")

def lan_ip() -> str | None:
    """This machine's address on the local network (no packets are sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def _port() -> int:
    try:
        import streamlit as st
        return int(st.get_option("server.port") or 8501)
    except Exception:  # noqa: BLE001
        return 8501


def _browser_host() -> str | None:
    """Host the current browser used, if it isn't localhost (e.g. a deployed URL)."""
    try:
        import streamlit as st
        host = st.context.headers.get("Host")
    except Exception:  # noqa: BLE001
        return None
    if not host or host.split(":")[0] in ("localhost", "127.0.0.1", "0.0.0.0"):
        return None
    return host


def base_url(con) -> str:
    """Setting > PLANNER_BASE_URL env > browser host > LAN IP > localhost."""
    configured = get(con, "base_url")
    if configured:
        return configured.rstrip("/")
    if _USER_ENV_URL:
        return _USER_ENV_URL.rstrip("/")
    host = _browser_host()
    if host:
        scheme = "http" if host.split(":")[0].replace(".", "").isdigit() else "https"
        return f"{scheme}://{host}"
    ip = lan_ip()
    return f"http://{ip or 'localhost'}:{_port()}"
