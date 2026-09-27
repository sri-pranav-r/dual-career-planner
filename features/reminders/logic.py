"""
Reminders outside the app, logging nudges and the weekly team load report.
No Streamlit here, so everything is unit-tested in test_reminders.py.

  1. Tournament reminders - athletes entered in a tournament get a message 10 days
                            and again 3 days before it starts: how many tests and
                            classes it clashes with, and where their letter stands.
  2. Logging nudges       - athletes with no training logged for 3 days are asked to
                            log (repeated every 7 days of silence, not daily).
  3. Team load report     - per sport, each athlete's week: sessions, load, ACWR, and
                            two flags coaches asked for: "spiked" and "not logging".

Every message goes to the athlete's in-app inbox (features.letters notify) and to
the outbox table, which the sender (senders.py) either delivers or, by default,
only records as a dry run. A dedupe key per message means running this twice in a
day, or from both the app and a cron job, never sends anything twice.

Tables (schema.sql): reminders_outbox, reminders_prefs, reminders_reports, reminders_runs.
Handles: athlete_deleted.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pandas as pd

import core
import data

from . import senders

REMINDER_DAYS = (10, 3)          # days before a tournament's first away day
NUDGE_AFTER_DAYS = 3             # no session logged for this many days -> nudge
NUDGE_REPEAT_DAYS = 7            # while still silent, nudge again this often
REPORT_WEEKDAY = 0               # run_if_due builds the weekly report on Mondays
SPIKE_ACWR = 1.3                 # core.acwr_zone calls anything above this "Caution"
SPIKE_WEEK_RATIO = 1.5           # fallback under 28 days of logs: this week vs the average earlier week
TESTS = ("CIE", "SEE")
DAY_FMT = "%a %d %b"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rows(cur) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _as_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def base_url(con) -> str:
    """Where the app is reachable, for links in messages. Empty when unknown."""
    try:
        import settings
        return (settings.base_url(con) or "").rstrip("/")
    except Exception:  # noqa: BLE001 - settings.py is new in v3; fall back to the env var
        import os
        return os.environ.get("PLANNER_BASE_URL", "").rstrip("/")


def _link(con) -> str:
    url = base_url(con)
    return f" {url}" if url else ""


# ---------------------------------------------------------------------------
# preferences (consent to WhatsApp / SMS)
# ---------------------------------------------------------------------------

def prefs(con, athlete_id: int) -> dict:
    row = con.execute("SELECT external_ok, channel FROM reminders_prefs WHERE athlete_id=?", (athlete_id,)).fetchone()
    if row is None:
        return {"external_ok": False, "channel": senders.default_channel()}
    return {"external_ok": bool(row[0]), "channel": row[1]}


def set_prefs(con, athlete_id: int, external_ok: bool, channel: str = "whatsapp") -> None:
    if channel not in senders.CHANNELS:
        raise ValueError(f"channel must be one of {senders.CHANNELS}")
    con.execute("INSERT INTO reminders_prefs(athlete_id, external_ok, channel, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(athlete_id) DO UPDATE SET external_ok=excluded.external_ok, channel=excluded.channel, "
                "updated_at=excluded.updated_at", (athlete_id, int(bool(external_ok)), channel, _now()))
    con.commit()


# ---------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------

def _already(con, key: str) -> bool:
    return con.execute("SELECT 1 FROM reminders_outbox WHERE dedupe_key=? LIMIT 1", (key,)).fetchone() is not None


def _record(con, aid, kind, key, channel, to, text, status, detail=None):
    con.execute("INSERT OR IGNORE INTO reminders_outbox(athlete_id, kind, dedupe_key, channel, to_addr, message, "
                "status, detail, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (aid, kind, key, channel, to, text, status, detail, _now()))


def _notify_in_app(con, aid, text, kind, tournament_id) -> bool:
    try:
        from features.letters import logic as letters
    except ImportError:
        return False
    letters.notify(con, aid, text, kind=kind, tournament_id=tournament_id)
    return True


def deliver(con, athlete: dict, kind: str, key: str, text: str, sender, tournament_id: int | None = None) -> bool:
    """Send one reminder in-app and outside the app. False if this key was already handled."""
    if _already(con, key):
        return False
    aid = int(athlete["id"])
    if _notify_in_app(con, aid, text, f"reminder_{kind}", tournament_id):
        _record(con, aid, kind, key, "in_app", None, text, "sent")

    p = prefs(con, aid)
    phone = senders.normalise_phone(athlete.get("phone"))
    channel = p["channel"]
    problems = []
    if not phone:
        problems.append(f"no valid phone number ({athlete.get('phone') or 'blank'})")
    if not p["external_ok"]:
        problems.append("athlete has not opted in to WhatsApp/SMS")
    if not sender.live:
        note = "dry run, not sent" + (f". Live mode would skip it: {'; '.join(problems)}" if problems else "")
        _record(con, aid, kind, key, channel, phone or athlete.get("phone"), text, "dry_run", note)
    elif problems:
        _record(con, aid, kind, key, channel, phone, text, "skipped", "; ".join(problems))
    else:
        ok, detail = sender.send(channel, phone, text)
        _record(con, aid, kind, key, channel, phone, text, "sent" if ok else "failed", detail)
    con.commit()
    return True


def outbox(con, athlete_ids: list[int] | None = None, limit: int = 200) -> list[dict]:
    sql = ("SELECT o.id, o.created_at, a.name AS athlete, a.usn, o.kind, o.channel, o.to_addr, o.status, "
           "o.detail, o.message, o.athlete_id FROM reminders_outbox o LEFT JOIN athletes a ON a.id=o.athlete_id")
    params: list = []
    if athlete_ids is not None:
        if not athlete_ids:
            return []
        sql += f" WHERE o.athlete_id IN ({','.join('?' * len(athlete_ids))})"
        params += [int(x) for x in athlete_ids]
    return _rows(con.execute(sql + " ORDER BY o.id DESC LIMIT ?", (*params, limit)))


# ---------------------------------------------------------------------------
# 1. Tournament reminders
# ---------------------------------------------------------------------------

def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}{'es' if word.endswith('s') else 's'}"


def _letter_line(con, aid, tid, link) -> str:
    try:
        from features.letters import logic as letters
    except ImportError:
        return f"Download your exemption letter from the planner.{link}"
    letter = letters.find_letter(con, aid, tid)
    if letter is None:
        return f"You have no exemption letter yet. Generate it now in the planner.{link}"
    if letter.get("rejected"):
        return f"Your letter was rejected. Redraft it in the planner.{link}"
    if letter.get("needs_redo"):
        return f"The dates changed, so your letter needs redoing.{link}"
    if letter["stage"] == "submitted":
        return "Your letter is submitted. Nothing to do."
    if letter["stage"] == "hod_approved":
        return "Your letter is approved. Hand it in at the department office and mark it submitted."
    return f"Your letter is at: {letters.STAGE_LABELS.get(letter['stage'], letter['stage'])}. Chase the next signature."


def tournament_message(con, athlete: dict, tournament: dict, today: date) -> str:
    first, _ = core.away_window(tournament)
    days = (first - today).days
    clashes = core.find_clashes(tournament, data.timetable(con, athlete["id"]), data.events(con, athlete["id"]))
    tests = sum(c.kind in TESTS for c in clashes)
    classes = sum(c.kind in ("class", "lab") for c in clashes)
    when = "today" if days <= 0 else "tomorrow" if days == 1 else f"in {days} days"
    head = f"Planner: {tournament['name']} {'starts' if days > 0 else 'is'} {when} (away from {first.strftime(DAY_FMT)})."
    if not clashes:
        return f"{head} No classes or tests clash. Good luck!"
    parts = [_plural(tests, "test")] if tests else []
    if classes:
        parts.append(_plural(classes, "class"))
    clash = f"It clashes with {' and '.join(parts)}."
    return f"{head} {clash} {_letter_line(con, athlete['id'], tournament['id'], _link(con))}"


def tournament_reminders(con, today: date, sender) -> int:
    sent = 0
    for _, t in data.all_tournaments(con).iterrows():
        t = t.to_dict()
        first, _ = core.away_window(t)
        days = (first - today).days
        due = [d for d in REMINDER_DAYS if 0 <= days <= d]
        if not due:
            continue
        step = min(due)
        for aid in data.entries(con, t["id"]):
            a = data.athlete(con, aid)
            if a is None:
                continue
            key = f"tournament:{t['id']}:{t['start_date']}:{aid}:{step}"
            if deliver(con, a, "tournament", key, tournament_message(con, a, t, today), sender, int(t["id"])):
                sent += 1
    return sent


# ---------------------------------------------------------------------------
# 2. Logging nudges
# ---------------------------------------------------------------------------

def last_logged(con, athlete_id: int, today: date) -> date | None:
    row = con.execute("SELECT MAX(date) FROM sessions WHERE athlete_id=? AND date<=?",
                      (athlete_id, today.isoformat())).fetchone()
    return _as_date(row[0]) if row and row[0] else None


def silent_athletes(con, today: date, athlete_ids: list[int] | None = None) -> list[dict]:
    """Athletes with nothing logged for NUDGE_AFTER_DAYS or more. days=None means never logged."""
    out = []
    for _, a in data.athletes(con).iterrows():
        if athlete_ids is not None and int(a["id"]) not in athlete_ids:
            continue
        last = last_logged(con, int(a["id"]), today)
        days = None if last is None else (today - last).days
        if days is None or days >= NUDGE_AFTER_DAYS:
            out.append({"athlete": a.to_dict(), "last": last, "days": days})
    return out


def nudge_message(con, last: date | None, days: int | None) -> str:
    link = _link(con)
    if last is None:
        return ("Planner: you haven't logged any training yet. Log each session (minutes and effort 1-10) "
                f"so your coach can see your load before tournaments.{link}")
    return (f"Planner: you haven't logged training since {last.strftime(DAY_FMT)} ({days} days). "
            f"Log today's session (minutes and effort 1-10), even a rest day, so your load stays accurate.{link}")


def logging_nudges(con, today: date, sender) -> int:
    sent = 0
    for s in silent_athletes(con, today):
        a = s["athlete"]
        if s["last"] is None:
            key = f"nudge:{a['id']}:never:{today.isocalendar()[0]}-{today.isocalendar()[1]}"
        else:
            key = f"nudge:{a['id']}:{s['last'].isoformat()}:{(s['days'] - NUDGE_AFTER_DAYS) // NUDGE_REPEAT_DAYS}"
        if deliver(con, a, "nudge", key, nudge_message(con, s["last"], s["days"]), sender):
            sent += 1
    return sent


# ---------------------------------------------------------------------------
# 3. Weekly team load report
# ---------------------------------------------------------------------------

def athlete_week(con, athlete: dict, week_end: date) -> dict:
    s = data.sessions(con, athlete["id"])
    if not s.empty:
        s = s[pd.to_datetime(s["date"]).dt.date <= week_end]
    loads = core.daily_loads(s, week_end, days=42)
    week = float(loads.iloc[-7:].sum())
    first = None if s.empty else pd.to_datetime(s["date"]).dt.date.min()
    history = 0 if first is None else (week_end - first).days + 1
    # core zero-fills missing days, so a ratio before 28 days of history would compare
    # against a chronic load full of zeros and flag every newcomer. Treat it as unknown.
    ratio = core.acwr_series(loads)["acwr"].iloc[-1] if history >= 28 else float("nan")
    ratio = None if pd.isna(ratio) else round(float(ratio), 2)
    weeks_before = min(3, max(0, (history - 7) // 7))
    prev_avg = float(loads.iloc[-7 - 7 * weeks_before:-7].sum()) / weeks_before if weeks_before else 0.0
    sessions_7d = 0 if s.empty else int((pd.to_datetime(s["date"]).dt.date > week_end - timedelta(days=7)).sum())
    last = last_logged(con, int(athlete["id"]), week_end)
    silent = None if last is None else (week_end - last).days
    if ratio is not None:
        spiked = ratio > SPIKE_ACWR
    else:
        spiked = prev_avg > 0 and week > SPIKE_WEEK_RATIO * prev_avg
    return {
        "athlete_id": int(athlete["id"]), "name": athlete["name"], "usn": athlete["usn"],
        "sessions_7d": sessions_7d, "load_7d": round(week), "avg_week_before": round(prev_avg),
        "acwr": ratio, "zone": core.acwr_zone(ratio)[0],
        "last_logged": last.isoformat() if last else None, "days_silent": silent,
        "spiked": bool(spiked), "not_logging": silent is None or silent >= NUDGE_AFTER_DAYS,
    }


def _summary(sport: str, week_end: date, rows: list[dict]) -> str:
    spiked = [f"{r['name']} (ACWR {r['acwr']})" if r["acwr"] is not None else r["name"] for r in rows if r["spiked"]]
    quiet = [f"{r['name']} ({'never' if r['days_silent'] is None else str(r['days_silent']) + ' days'})"
             for r in rows if r["not_logging"]]
    return (f"{sport}, week to {week_end.strftime(DAY_FMT)}: {_plural(len(rows), 'athlete')}. "
            f"Spiked: {', '.join(spiked) or 'none'}. Not logging: {', '.join(quiet) or 'none'}.")


def team_report(con, sport: str, week_end: date, athlete_ids: list[int] | None = None) -> dict:
    """Build (without saving) one sport's report. athlete_ids limits it to athletes the viewer may see."""
    rows = []
    for _, a in data.athletes(con).iterrows():
        if a["sport"] != sport or (athlete_ids is not None and int(a["id"]) not in athlete_ids):
            continue
        rows.append(athlete_week(con, a.to_dict(), week_end))
    rows.sort(key=lambda r: (not r["spiked"], not r["not_logging"], r["name"]))
    return {"sport": sport, "week_end": week_end.isoformat(), "rows": rows, "summary": _summary(sport, week_end, rows)}


def save_report(con, report: dict) -> None:
    con.execute("INSERT INTO reminders_reports(sport, week_end, created_at, summary, rows_json) VALUES (?,?,?,?,?) "
                "ON CONFLICT(sport, week_end) DO UPDATE SET created_at=excluded.created_at, "
                "summary=excluded.summary, rows_json=excluded.rows_json",
                (report["sport"], report["week_end"], _now(), report["summary"], json.dumps(report["rows"])))
    con.commit()


def saved_reports(con, sports: list[str] | None = None, limit: int = 20) -> list[dict]:
    rows = _rows(con.execute("SELECT * FROM reminders_reports ORDER BY week_end DESC, sport LIMIT ?", (limit * 10,)))
    out = [dict(r, rows=json.loads(r.pop("rows_json"))) for r in rows if sports is None or r["sport"] in sports]
    return out[:limit]


def sports(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT DISTINCT sport FROM athletes WHERE sport IS NOT NULL AND sport<>'' ORDER BY sport")]


def weekly_reports(con, week_end: date) -> list[str]:
    done = []
    for sport in sports(con):
        save_report(con, team_report(con, sport, week_end))
        done.append(sport)
    return done


# ---------------------------------------------------------------------------
# running it
# ---------------------------------------------------------------------------

def run_all(con, today: date | None = None, sender=None, report: bool | None = None) -> dict:
    """One pass of everything. Safe to repeat: already-sent reminders are skipped.
    report=None builds the team report only on REPORT_WEEKDAY."""
    today = today or date.today()
    sender = sender or senders.from_env()
    out = {"day": today.isoformat(), "sender": sender.name,
           "tournament": tournament_reminders(con, today, sender),
           "nudges": logging_nudges(con, today, sender), "reports": []}
    if report or (report is None and today.weekday() == REPORT_WEEKDAY):
        out["reports"] = weekly_reports(con, today)
    return out


def run_if_due(con, today: date | None = None, sender=None) -> dict | None:
    """Run once per day. Core can call this on app start; a cron job can call it too."""
    today = today or date.today()
    if con.execute("SELECT 1 FROM reminders_runs WHERE day=?", (today.isoformat(),)).fetchone():
        return None
    out = run_all(con, today, sender)
    con.execute("INSERT OR REPLACE INTO reminders_runs(day, ran_at, summary) VALUES (?,?,?)",
                (today.isoformat(), _now(), json.dumps(out)))
    con.commit()
    return out


def last_run(con) -> dict | None:
    row = con.execute("SELECT day, ran_at, summary FROM reminders_runs ORDER BY day DESC LIMIT 1").fetchone()
    return None if row is None else {"day": row[0], "ran_at": row[1], **json.loads(row[2] or "{}")}


# ---------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------

def on_athlete_deleted(con, payload: dict) -> None:
    aid = int(payload["athlete_id"])
    con.execute("DELETE FROM reminders_outbox WHERE athlete_id=?", (aid,))
    con.execute("DELETE FROM reminders_prefs WHERE athlete_id=?", (aid,))
    for rid, sport, week_end, rows_json in con.execute(
            "SELECT id, sport, week_end, rows_json FROM reminders_reports").fetchall():
        rows = json.loads(rows_json)
        kept = [r for r in rows if r.get("athlete_id") != aid]
        if len(kept) != len(rows):
            con.execute("UPDATE reminders_reports SET rows_json=?, summary=? WHERE id=?",
                        (json.dumps(kept), _summary(sport, _as_date(week_end), kept), rid))
    con.commit()
