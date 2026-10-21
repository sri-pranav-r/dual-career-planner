"""
College rules and letter wording, editable by admins (Settings tab) and
stored in the core `meta` table under "setting.<key>".

Defaults follow the college's rule: 85% attendance is mandatory, and a shortfall
for a valid reason (sport, hackathons, other approved leave) can be accepted down
to 75%. Leave to represent the college is granted by the Dean of Student Affairs
on a letter (or email) copied to the student's faculty. Change them in the app.
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
    "attendance_relaxed_pct": 75,     # lowest allowed with a valid reason (Dean's permission)
    "attendance_relaxed_enabled": True,
    "on_duty_counts_as_present": True,
    "on_duty_from": "dean",           # when sport days start counting as present: dean | hod | always
    "max_on_duty_days": 0,            # 0 = no cap
    "semester_start": "",             # ISO date; "" = unknown
    "semester_end": "",
    # letter (wording defaults live in core.DEFAULT_LETTER)
    **core.DEFAULT_LETTER,
    # links
    "base_url": "",                   # "" = auto-detect
}

# When sport days start counting as present, in the order shown in Settings.
ON_DUTY_RULES = {
    "dean": "Once the Dean of Student Affairs approves the letter",
    "hod": "Once the HoD approves the letter (before the Dean)",
    "always": "Straight away, without waiting for a letter",
    "never": "Never: sport days count as absent",
}
ON_DUTY_FROM = ("dean", "hod", "always")

LABELS = {
    "attendance_min_pct": "Expected attendance (%)",
    "attendance_relaxed_pct": "Lowest attendance allowed with a valid reason (%)",
    "attendance_relaxed_enabled": "Allow a lower attendance with the Dean's permission",
    "on_duty_counts_as_present": "Approved sport absences count as present (on-duty)",
    "on_duty_from": "Sport days count as present",
    "max_on_duty_days": "Maximum on-duty days per semester (0 = no limit)",
    "semester_start": "Semester start date",
    "semester_end": "Semester end date",
    "college_name": "College name",
    "letter_addressee": "Letter goes to",
    "letter_to": "Addressed to",
    "letter_through": "Through line (optional)",
    "letter_subject": "Subject line",
    "letter_salutation": "Salutation",
    "letter_request": "Request paragraph",
    "letter_closing": "Closing",
    "letter_signatures": "Signature lines (separate with |)",
    "letter_cc": "Copy to (separate with ;)",
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
        if key in ("attendance_min_pct", "attendance_relaxed_pct") and not 0 <= value <= 100:
            raise ValueError("Attendance must be between 0 and 100.")
        if value < 0:
            raise ValueError(f"{LABELS[key]} can't be negative.")
    else:
        value = str(value).strip()
        if key == "on_duty_from" and value not in ON_DUTY_FROM:
            raise ValueError(f"Choose one of: {', '.join(ON_DUTY_FROM)}.")
        if key == "letter_addressee" and value not in core.ADDRESSEES:
            raise ValueError(f"The letter goes to one of: {', '.join(core.ADDRESSEES)}.")
    data.set_meta(con, f"setting.{key}", json.dumps(value))


def all(con) -> dict:  # noqa: A001
    return {k: get(con, k) for k in DEFAULTS}


def attendance_floors(con) -> tuple[int, int]:
    """(required, relaxed). Relaxed equals required when the relaxation is off, and is never above it."""
    required = get(con, "attendance_min_pct")
    if not get(con, "attendance_relaxed_enabled"):
        return required, required
    return required, min(get(con, "attendance_relaxed_pct"), required)


def on_duty_rule(con) -> str:
    """When sport days count as present: "dean", "hod", "always", or "never"."""
    if not get(con, "on_duty_counts_as_present"):
        return "never"
    return get(con, "on_duty_from")


def set_on_duty_rule(con, rule: str) -> None:
    if rule not in ON_DUTY_RULES:
        raise ValueError(f"Choose one of: {', '.join(ON_DUTY_RULES)}.")
    set(con, "on_duty_counts_as_present", rule != "never")
    if rule != "never":
        set(con, "on_duty_from", rule)


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
