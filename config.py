"""
Deployment settings read from the environment or from Streamlit secrets.

Nothing secret lives in the code or the database defaults. On Streamlit Cloud,
put values in the app's Secrets box (TOML, root level); when self-hosting, set
environment variables or .streamlit/secrets.toml. An environment variable wins
over a secret of the same name.

  PLANNER_DB            path of the SQLite file (default: planner.db next to app.py)
  PLANNER_BASE_URL      the https address people open, for QR codes and links
  PLANNER_SETUP_CODE    code asked for when creating the first admin (else one is
                        generated and written to the server log)
  PLANNER_SECRET        key that signs letter QR codes (else one is generated and
                        kept in the database)
  PLANNER_REMINDERS_LIVE, PLANNER_REMINDER_*, TWILIO_*   see features/reminders
"""
from __future__ import annotations

import os

# Root-level secrets with these prefixes are copied into the environment at start-up,
# so feature modules and the cron runner that read os.environ see them too.
PREFIXES = ("PLANNER_", "TWILIO_")


def _secrets() -> dict:
    try:
        import streamlit as st
        return {k: v for k, v in st.secrets.items() if isinstance(v, (str, int, float, bool))}
    except Exception:  # noqa: BLE001 - no secrets file, or not running under Streamlit
        return {}


def get(name: str, default: str | None = None) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    val = _secrets().get(name)
    return default if val in (None, "") else str(val)


def load_secrets_into_env() -> list[str]:
    """Copy PLANNER_* and TWILIO_* secrets into os.environ (env values already set win). Returns the names copied."""
    copied = []
    for k, v in _secrets().items():
        if k.startswith(PREFIXES) and not os.environ.get(k):
            os.environ[k] = str(v)
            copied.append(k)
    return copied
