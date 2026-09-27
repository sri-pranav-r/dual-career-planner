"""
"Next 14 days": one combined academic + training plan per athlete, and pilot
logging of whether the athlete followed each suggestion.

Everything here is rule-based. Each suggestion names the rule that produced
it (RULES below), so an athlete, coach or examiner can see why it's there.
The numbers in RULES are placeholders to check with the PED or coach.

It reuses what other parts of the app already compute instead of repeating it:
  core             clashes, away windows, session-RPE and ACWR
  features.records taper plan for the week before a tournament, injuries
  features.squad   projected attendance against the college floor

No streamlit here. Every function takes `con` so it can be tested with
data.connect(":memory:").

Tables (schema.sql): plan_suggestions, plan_followups.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

import auth
import core
import data
from features.records import logic as records
from features.squad import logic as squad

HORIZON = 14

# ---------------------------------------------------------------------------
# Rules. Numbers are placeholders: confirm them with the PED / coach.
# ---------------------------------------------------------------------------

RULES = {
    "P1": {"name": "Ease off before a test",
           "why": "Hard sessions the day or two before a CIE or SEE cost sleep and focus when it matters most.",
           "days_before": 2, "max_rpe": 5},
    "P2": {"name": "Study slot on a light timetable day",
           "why": "Days with few classes leave room for a 60 to 90 minute study block.",
           "max_slots": 3, "minutes": 60, "minutes_near_test": 90, "near_test_days": 7},
    "P3": {"name": "Revise before a test",
           "why": "A focused block on each of the few days before a test beats one long night.",
           "days_before": 3, "minutes": 90},
    "P4": {"name": "Travel days: light work only",
           "why": "Buses and trains are fine for flashcards and notes, not for new topics or training.",
           "minutes": 30},
    "P5": {"name": "Recover after a tournament",
           "why": "Competition load is high and sleep on the road is poor, so the first days back are for "
                  "recovery and catching up on missed classes.",
           "days": 2},
    "P6": {"name": "Taper before a tournament",
           "why": "The Taper plan tab's day-by-day plan for the week before you leave, from your load zone."},
    "P7": {"name": "Load spike warning",
           "why": "Your 7-day load is well above your 4-week average (ACWR). Spikes are when injuries happen.",
           "high_days": 3, "caution_days": 2},
    "P8": {"name": "Attendance near the floor",
           "why": "Your projected attendance in a subject is close to or below the college minimum.",
           "margin": 5},
    "P9": {"name": "Out injured",
           "why": "You're inside the days-out of a logged injury. Follow the return-to-play ramp in Injuries."},
    "P10": {"name": "Competition day",
            "why": "Tournament days are for competing. No study is planned."},
    "P11": {"name": "Recovery below your baseline",
            "why": "Your latest wellness check-in (sleep, soreness, stress) is well below your own recent average.",
            "drop": 2, "min_history": 2, "days": 2, "fresh_days": 10},
}

# Training intensity, lightest first. When two rules disagree about a day, the lighter one wins
# (ties go to the rule listed first in RULE_PRIORITY). Competition days are the exception.
LEVELS = ["rest", "recovery", "light", "easy", "moderate", "compete"]
LEVEL_LABELS = {"rest": "Rest", "recovery": "Recovery", "light": "Light", "easy": "Easy (RPE 5 or less)",
                "moderate": "Moderate", "compete": "Compete"}
RULE_PRIORITY = ["P10", "P9", "P4", "P5", "P1", "P7", "P11", "P6", "P3", "P2", "P8"]

# Highest RPE a logged session may have for the day to count as matching the suggestion.
LEVEL_MAX_RPE = {"rest": 0, "recovery": 3, "light": 4, "easy": 5}
REST_MOBILITY_MINUTES = 20   # a short mobility session still counts as rest

FOLLOW_STATUSES = {"followed": "Followed", "partly": "Partly", "not_followed": "Didn't follow"}
REASONS = ["No time", "Coach's plan was different", "Felt fine, didn't need it", "Forgot", "Didn't agree with it",
           "Other"]


class PlanError(ValueError):
    """A user-facing validation problem."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _d(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _rows(cur) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _fmt(d: date) -> str:
    return d.strftime("%a %d %b")


def _in(n: int) -> str:
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def slots_by_weekday(con, athlete_id) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for r in data.timetable(con, athlete_id).itertuples():
        out.setdefault(int(r.weekday), []).append(r.subject)
    return out


def load_status(con, athlete_id, today: date) -> dict:
    loads = core.daily_loads(data.sessions(con, athlete_id), today, days=42)
    ratio = core.acwr_series(loads)["acwr"].iloc[-1]
    ratio = None if pd.isna(ratio) else float(ratio)
    zone, advice = core.acwr_zone(ratio)
    return {"ratio": ratio, "zone": zone, "advice": advice, "trend": records.acwr_trend(loads)}


def recovery_status(con, athlete_id, today: date) -> dict:
    """
    Latest wellness check-in against the athlete's own average of the check-ins before it.
    Score = sleep + (6 - soreness) + (6 - stress), so 3 (worst) to 15 (best).
    """
    r = RULES["P11"]
    w = data.wellness(con, athlete_id)
    if w.empty:
        return {"state": "no_data", "score": None, "baseline": None, "date": None}
    w = w.assign(date=pd.to_datetime(w["date"]).dt.date)
    w = w[w["date"] <= today].sort_values("date")
    if w.empty:
        return {"state": "no_data", "score": None, "baseline": None, "date": None}
    w = w.assign(score=w["sleep"] + (6 - w["soreness"]) + (6 - w["stress"]))
    last = w.iloc[-1]
    prev = w.iloc[:-1].tail(4)
    score = float(last["score"])
    if len(prev) < r["min_history"]:
        return {"state": "no_baseline", "score": score, "baseline": None, "date": last["date"]}
    baseline = float(prev["score"].mean())
    if (today - last["date"]).days > r["fresh_days"]:
        state = "stale"
    elif score <= baseline - r["drop"]:
        state = "below"
    elif score >= baseline + r["drop"]:
        state = "above"
    else:
        state = "normal"
    return {"state": state, "score": score, "baseline": round(baseline, 1), "date": last["date"]}


def attendance_status(con, athlete_id, today: date) -> dict | None:
    p = squad.attendance_projection(con, athlete_id, today)
    w = p["worst"]
    if w is None:
        return None
    floor = p["floor"]
    margin = RULES["P8"]["margin"]
    risk = "high" if w["pct"] < floor else "moderate" if w["pct"] < floor + margin else "low"
    return {"subject": w["subject"], "pct": w["pct"], "floor": floor, "risk": risk,
            "uncovered_days": p["uncovered_days"], "dates_known": p["dates_known"]}


def _tournaments(con, athlete_id, first: date, last: date) -> list[dict]:
    """Tournaments whose away window (with travel and the recovery days after) touches [first, last]."""
    out = []
    for _, t in data.tournaments_for(con, athlete_id).iterrows():
        t = t.to_dict()
        leave, back = core.away_window(t)
        if leave <= last and back + timedelta(days=RULES["P5"]["days"]) >= first:
            out.append({**t, "leave": leave, "back": back})
    return out


def _missed_subjects(con, athlete_id, t: dict) -> list[str]:
    clashes = core.find_clashes(t, data.timetable(con, athlete_id), data.events(con, athlete_id))
    seen = []
    for c in sorted(clashes, key=lambda c: c.date):
        if c.kind in ("class", "lab") and c.title not in seen:
            seen.append(c.title)
    return seen


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

def _pick_training(cands: list[dict]) -> tuple[dict | None, list[dict]]:
    if not cands:
        return None, []
    compete = [c for c in cands if c["level"] == "compete"]
    if compete:
        return compete[0], [c for c in cands if c is not compete[0]]
    best = min(cands, key=lambda c: (LEVELS.index(c["level"]), RULE_PRIORITY.index(c["rule_id"])))
    return best, [c for c in cands if c is not best]


def _pick_study(cands: list[dict]) -> dict | None:
    if not cands:
        return None
    return min(cands, key=lambda c: RULE_PRIORITY.index(c["rule_id"]))


def build_plan(con, athlete_id, today: date | None = None, horizon: int = HORIZON) -> dict:
    """
    The athlete's next `horizon` days.

    Returns {athlete, start, end, academic, sports, headlines, days}:
      academic   tests in the window, attendance risk
      sports     tournaments in the window, load zone, recovery vs baseline, injury
      headlines  one line per rule that fired, for the "Recommended plan" list
      days       [{date, weekday, classes, tests, away, training, study, also}] where training and study
                 are {area, rule_id, rule, level, text} or None
    """
    today = today or date.today()
    aid = int(athlete_id)
    end = today + timedelta(days=horizon - 1)
    athlete = data.athlete(con, aid)
    slots = slots_by_weekday(con, aid)
    events = data.events(con, aid)
    tests = [] if events.empty else [
        {"date": r.date, "kind": r.kind, "title": r.title} for r in events.itertuples()
        if r.kind in ("CIE", "SEE", "lab") and today <= r.date <= end + timedelta(days=RULES["P3"]["days_before"])]
    tests.sort(key=lambda t: t["date"])
    tours = _tournaments(con, aid, today, end)
    away_dates = {d for t in tours for d in core.tournament_window(t["leave"], t["back"])}
    for t in tests:
        t["missed"] = t["date"] in away_dates   # away for sport that day: a make-up test, not revision
    exams = [t for t in tests if t["kind"] in ("CIE", "SEE") and not t["missed"]]
    load = load_status(con, aid, today)
    recovery = recovery_status(con, aid, today)
    attendance = attendance_status(con, aid, today)

    # Taper for the next tournament, if its taper week reaches into the window.
    taper = {}
    nxt = records.next_tournament(con, aid, today)
    if nxt is not None and (core.away_window(nxt)[0] - today).days <= horizon + 7:
        tp = records.taper_plan(con, aid, nxt, today)
        for d in tp["days"]:
            if d["kind"] != "travel" and today <= d["date"] <= end:
                taper[d["date"]] = d

    training: dict[date, list[dict]] = {}
    study: dict[date, list[dict]] = {}

    def add(bucket, d, rule_id, text, level=None):
        if today <= d <= end:
            bucket.setdefault(d, []).append({"area": "training" if bucket is training else "study",
                                             "rule_id": rule_id, "rule": RULES[rule_id]["name"],
                                             "level": level, "text": text})

    # P10 / P4 / P5: tournaments, travel, recovery after.
    away_days: dict[date, str] = {}
    for t in tours:
        for d in core.tournament_window(t["leave"], t["back"]):
            comp = t["start_date"] <= d <= t["end_date"]
            away_days[d] = "tournament" if comp else "travel"
            if comp:
                add(training, d, "P10", f"{t['name']}: compete. Warm up properly and eat on time.", "compete")
            else:
                add(training, d, "P4", "Travel day: rest, or 15 minutes of mobility when you arrive.", "rest")
                add(study, d, "P4", f"Light study only: about {RULES['P4']['minutes']} minutes of notes or "
                                    "flashcards on the way. No new topics.")
        missed = _missed_subjects(con, aid, t)
        for i in range(RULES["P5"]["days"]):
            d = t["back"] + timedelta(days=1 + i)
            add(training, d, "P5", f"Recovery after {t['name']}: easy movement, stretching, sleep. No hard sessions.",
                "recovery")
            if missed:
                subs = ", ".join(missed[:3]) + (" and more" if len(missed) > 3 else "")
                add(study, d, "P5", f"Catch up on the classes you missed while away: {subs}. "
                                    "Borrow notes and ask the class teacher for any assignments.")

    # P1 / P3: tests.
    for ex in exams:
        for k in range(1, RULES["P1"]["days_before"] + 1):
            text = (f"{ex['title']} tomorrow: keep training easy (RPE {RULES['P1']['max_rpe']} or below) and sleep early."
                    if k == 1 else
                    f"{ex['title']} in {k} days: no high-intensity work, keep it to RPE {RULES['P1']['max_rpe']} or below.")
            add(training, ex["date"] - timedelta(days=k), "P1", text, "easy")
        add(training, ex["date"], "P1", f"{ex['title']} today: skip training or keep it very light.", "light")
        for k in range(1, RULES["P3"]["days_before"] + 1):
            add(study, ex["date"] - timedelta(days=k), "P3",
                f"{RULES['P3']['minutes']} minutes revising for {ex['title']} ({_fmt(ex['date'])}).")

    # P9: injured days.
    for i in range(horizon):
        d = today + timedelta(days=i)
        if records.injured_on_day(con, aid, d):
            add(training, d, "P9", "Out injured: no training beyond what the physio has cleared. "
                                   "See the return-to-play ramp in Injuries.", "rest")

    # P7: load spike.
    n = {"High risk": RULES["P7"]["high_days"], "Caution": RULES["P7"]["caution_days"]}.get(load["zone"], 0)
    for i in range(n):
        lvl = "light" if load["zone"] == "High risk" else "easy"
        add(training, today + timedelta(days=i), "P7",
            f"Load {load['zone'].lower()} (ACWR {load['ratio']:.2f}): {LEVEL_LABELS[lvl].lower()} day. "
            "Talk to your coach before the next hard session.", lvl)

    # P11: recovery below the athlete's own baseline.
    if recovery["state"] == "below":
        for i in range(RULES["P11"]["days"]):
            add(training, today + timedelta(days=i), "P11",
                f"Recovery score {recovery['score']:.0f}/15 against your usual {recovery['baseline']:.0f}: "
                "keep it easy and prioritise sleep.", "easy")

    # P6: taper days.
    for d, td in taper.items():
        lvl = {"rest": "rest", "light": "light", "activation": "light"}.get(td["kind"], "moderate")
        what = td["plan"].split(": ", 1)[1] if td["kind"] in ("sharp", "light", "activation") else td["plan"]
        add(training, d, "P6", f"Taper for {nxt['name']}: {what[0].lower()}{what[1:]}.", lvl)

    # P2: study slots on light timetable days (Sunday and days with few classes).
    r2 = RULES["P2"]
    for i in range(horizon):
        d = today + timedelta(days=i)
        if d in away_days or any(t["date"] == d for t in exams):
            continue
        n_slots = len(slots.get(d.weekday(), [])) if d.weekday() < 6 else 0
        if n_slots > r2["max_slots"]:
            continue
        soon = [e for e in exams if 0 < (e["date"] - d).days <= r2["near_test_days"]]
        minutes = r2["minutes_near_test"] if soon else r2["minutes"]
        what = f"start on {soon[0]['title']} ({_fmt(soon[0]['date'])})" if soon else \
            "review this week's notes and finish pending assignments"
        light = "no classes" if n_slots == 0 else f"only {n_slots} class{'es' if n_slots > 1 else ''}"
        add(study, d, "P2", f"{minutes} minute study block ({light} today): {what}.")

    # P8: attendance. One academic item on today's plan.
    academic_today = None
    if attendance and attendance["risk"] in ("high", "moderate"):
        todo = "get your pending exemption letters approved and " if attendance["uncovered_days"] else ""
        academic_today = {"area": "academic", "rule_id": "P8", "rule": RULES["P8"]["name"], "level": None,
                          "text": f"{attendance['subject']} is projected at {attendance['pct']:.0f}% "
                                  f"(minimum {attendance['floor']:.0f}%): {todo}don't miss any more "
                                  f"{attendance['subject']} classes this fortnight."}

    days = []
    for i in range(horizon):
        d = today + timedelta(days=i)
        tr, also = _pick_training(training.get(d, []))
        st = None if away_days.get(d) == "tournament" else _pick_study(study.get(d, []))
        days.append({
            "date": d, "weekday": d.weekday(),
            "classes": slots.get(d.weekday(), []) if d.weekday() < 6 and d not in away_days else [],
            "tests": [t for t in tests if t["date"] == d],
            "away": away_days.get(d),
            "training": tr, "study": st, "also": also,
            "academic": academic_today if i == 0 else None,
        })

    # Headlines: one per rule that made it into the plan, with the days it covers.
    used: dict[str, list[date]] = {}
    for day in days:
        for s in (day["training"], day["study"], day["academic"]):
            if s:
                used.setdefault(s["rule_id"], []).append(day["date"])
    headlines = []
    for rid in RULE_PRIORITY:
        if rid not in used:
            continue
        ds = sorted(set(used[rid]))
        when = _fmt(ds[0]) if len(ds) == 1 else f"{len(ds)} days, {_fmt(ds[0])} to {_fmt(ds[-1])}"
        headlines.append({"rule_id": rid, "rule": RULES[rid]["name"], "when": when, "days": ds,
                          "text": _headline(rid, ds, exams, tours, attendance, load)})

    injured_now = bool(records.injured_on_day(con, aid, today))
    return {
        "athlete": athlete, "start": today, "end": end,
        "academic": {"tests": tests, "attendance": attendance},
        "sports": {"tournaments": tours, "load": load, "recovery": recovery, "injured": injured_now},
        "headlines": headlines, "days": days,
    }


def _headline(rid, ds, exams, tours, attendance, load) -> str:
    if rid == "P1":
        names = ", ".join(e["title"] for e in exams if any(0 <= (e["date"] - d).days <= RULES["P1"]["days_before"]
                                                         for d in ds))
        return f"Reduce high-intensity training in the {RULES['P1']['days_before']} days before {names}"
    if rid == "P2":
        return f"Take a {RULES['P2']['minutes']} to {RULES['P2']['minutes_near_test']} minute study block on " \
               f"{len(ds)} light timetable day{'s' if len(ds) > 1 else ''}"
    if rid == "P3":
        return f"Revise {RULES['P3']['minutes']} minutes a day in the {RULES['P3']['days_before']} days before each test"
    if rid == "P4":
        return "Keep tournament travel days for light academic work only"
    if rid == "P5":
        return "Schedule recovery and catch-up study after " + ", ".join(
            t["name"] for t in tours if any(0 < (d - t["back"]).days <= RULES["P5"]["days"] for d in ds))
    if rid == "P6":
        return "Follow the taper plan in the week before you leave"
    if rid == "P7":
        return f"Back off now: training load is in the {load['zone'].lower()} zone"
    if rid == "P8":
        return f"Protect attendance in {attendance['subject']} ({attendance['pct']:.0f}%, minimum {attendance['floor']:.0f}%)"
    if rid == "P9":
        return "Stay on the return-to-play ramp while injured"
    if rid == "P10":
        return "Compete: " + ", ".join(t["name"] for t in tours if any(t["start_date"] <= d <= t["end_date"] for d in ds))
    if rid == "P11":
        return "Go easier for a couple of days: recovery is below your usual"
    return RULES[rid]["name"]


def plan_table(plan: dict) -> pd.DataFrame:
    """The day-by-day plan as a table for display or CSV."""
    rows = []
    for d in plan["days"]:
        on = []
        if d["away"] == "tournament":
            on.append("🏆 Tournament")
        elif d["away"] == "travel":
            on.append("✈️ Travel")
        on += [f"📝 {t['title']}" for t in d["tests"]]
        if d["classes"]:
            on.append(f"📚 {len(d['classes'])} class{'es' if len(d['classes']) > 1 else ''}")
        tr, st = d["training"], d["study"]
        rules = sorted({s["rule_id"] for s in (tr, st, d["academic"]) if s}, key=RULE_PRIORITY.index)
        rows.append({"Date": _fmt(d["date"]), "What's on": ", ".join(on) or "Free day",
                     "Training": f"{LEVEL_LABELS[tr['level']]}: {tr['text']}" if tr else "Train as planned",
                     "Study": st["text"] if st else "-",
                     "Rules": ", ".join(rules) or "-"})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Pilot logging
# ---------------------------------------------------------------------------

def _suggestions_of(plan: dict):
    for d in plan["days"]:
        for s in (d["training"], d["study"], d["academic"]):
            if s:
                yield d["date"], s


def record_plan(con, athlete_id, plan: dict, today: date | None = None) -> dict[tuple, int]:
    """
    Save what the athlete was shown. Rows for today and later are refreshed; rows for past days, and any
    row the athlete has already answered, are left as they were. Returns {(day, area): suggestion_id}.
    """
    today = today or date.today()
    aid, now = int(athlete_id), _now()
    shown = {(d.isoformat(), s["area"]): s for d, s in _suggestions_of(plan) if d >= today}
    answered = {r[0] for r in con.execute("SELECT suggestion_id FROM plan_followups WHERE athlete_id=?", (aid,))}
    existing = {(r["day"], r["area"]): r for r in _rows(con.execute(
        "SELECT id, day, area, rule_id, text FROM plan_suggestions WHERE athlete_id=? AND day>=?",
        (aid, today.isoformat())))}
    for key, s in shown.items():
        old = existing.get(key)
        if old is None:
            con.execute("INSERT INTO plan_suggestions(athlete_id,day,area,rule_id,level,text,first_shown,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?)", (aid, key[0], key[1], s["rule_id"], s["level"], s["text"], now, now))
        elif old["id"] not in answered and (old["rule_id"], old["text"]) != (s["rule_id"], s["text"]):
            con.execute("UPDATE plan_suggestions SET rule_id=?, level=?, text=?, updated_at=? WHERE id=?",
                        (s["rule_id"], s["level"], s["text"], now, old["id"]))
    # A future suggestion the plan no longer makes (a tournament moved, say) is dropped unless answered.
    for key, old in existing.items():
        if key not in shown and key[0] > today.isoformat() and old["id"] not in answered:
            con.execute("DELETE FROM plan_suggestions WHERE id=?", (old["id"],))
    con.commit()
    return {(r["day"], r["area"]): r["id"] for r in _rows(con.execute(
        "SELECT id, day, area FROM plan_suggestions WHERE athlete_id=?", (aid,)))}


def get_suggestion(con, suggestion_id) -> dict:
    r = _rows(con.execute("SELECT * FROM plan_suggestions WHERE id=?", (int(suggestion_id),)))
    if not r:
        raise PlanError("That suggestion no longer exists.")
    return r[0]


def log_followup(con, user, suggestion_id, status: str, reason: str = "", note: str = "",
                 today: date | None = None) -> int:
    """The athlete says whether they followed a suggestion. Answering again replaces the earlier answer."""
    today = today or date.today()
    s = get_suggestion(con, suggestion_id)
    if user is None or not (user.role == "admin" or (user.role == "athlete" and user.athlete_id == s["athlete_id"])):
        raise PermissionError("Only the athlete can say whether they followed their plan.")
    if status not in FOLLOW_STATUSES:
        raise PlanError("Pick Followed, Partly or Didn't follow.")
    if _d(s["day"]) > today:
        raise PlanError("You can only answer for today or earlier.")
    reason = (reason or "").strip() if status != "followed" else ""
    con.execute(
        "INSERT INTO plan_followups(suggestion_id,athlete_id,status,reason,note,logged_at,logged_by) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(suggestion_id) DO UPDATE SET status=excluded.status, "
        "reason=excluded.reason, note=excluded.note, logged_at=excluded.logged_at, logged_by=excluded.logged_by",
        (int(suggestion_id), int(s["athlete_id"]), status, reason, (note or "").strip(), _now(), user.username))
    con.commit()
    return con.execute("SELECT id FROM plan_followups WHERE suggestion_id=?", (int(suggestion_id),)).fetchone()[0]


def to_answer(con, athlete_id, today: date | None = None, lookback: int = 7) -> list[dict]:
    """Suggestions from the last `lookback` days (today included) the athlete hasn't answered yet."""
    today = today or date.today()
    return _rows(con.execute(
        """SELECT s.* FROM plan_suggestions s LEFT JOIN plan_followups f ON f.suggestion_id=s.id
           WHERE s.athlete_id=? AND s.day<=? AND s.day>=? AND f.id IS NULL ORDER BY s.day DESC, s.area""",
        (int(athlete_id), today.isoformat(), (today - timedelta(days=lookback - 1)).isoformat())))


def log_agrees(level: str | None, day_sessions: pd.DataFrame) -> bool | None:
    """
    Does the training log for the day match a training suggestion? None when the suggestion has no
    ceiling to check (moderate, compete) or there's no level.
    """
    if level not in LEVEL_MAX_RPE:
        return None
    if day_sessions.empty:
        return True
    if level == "rest":
        return float(day_sessions["minutes"].sum()) <= REST_MOBILITY_MINUTES
    return float(day_sessions["rpe"].max()) <= LEVEL_MAX_RPE[level]


def pilot_rows(con, athlete_ids, start: date, end: date, today: date | None = None) -> pd.DataFrame:
    """Every suggestion shown for a day in [start, min(end, today)], with its answer and the log check."""
    today = today or date.today()
    last = min(end, today)
    ids = [int(i) for i in athlete_ids]
    cols = ["suggestion_id", "athlete_id", "Athlete", "USN", "Sport", "Date", "Area", "Rule", "Rule name", "Level",
            "Suggestion", "Answer", "Reason", "Note", "Training log agrees"]
    if not ids or last < start:
        return pd.DataFrame(columns=cols)
    marks = ",".join("?" * len(ids))
    df = data.q(con, f"""SELECT s.id AS suggestion_id, s.athlete_id, a.name, a.usn, a.sport, s.day, s.area,
                                s.rule_id, s.level, s.text, f.status, f.reason, f.note
                         FROM plan_suggestions s JOIN athletes a ON a.id=s.athlete_id
                         LEFT JOIN plan_followups f ON f.suggestion_id=s.id
                         WHERE s.athlete_id IN ({marks}) AND s.day>=? AND s.day<=?
                         ORDER BY s.day, a.name, s.area""", (*ids, start.isoformat(), last.isoformat()))
    if df.empty:
        return pd.DataFrame(columns=cols)
    sess = {aid: data.sessions(con, aid) for aid in set(df["athlete_id"])}
    agrees = []
    for r in df.itertuples():
        if r.area != "training" or _d(r.day) >= today:   # today's log may not be in yet
            agrees.append(None)
            continue
        s = sess[r.athlete_id]
        agrees.append(log_agrees(r.level, s[s["date"] == r.day] if not s.empty else s))
    return pd.DataFrame({
        "suggestion_id": df["suggestion_id"], "athlete_id": df["athlete_id"], "Athlete": df["name"],
        "USN": df["usn"], "Sport": df["sport"], "Date": df["day"], "Area": df["area"], "Rule": df["rule_id"],
        "Rule name": df["rule_id"].map(lambda r: RULES.get(r, {}).get("name", r)), "Level": df["level"],
        "Suggestion": df["text"], "Answer": df["status"].map(FOLLOW_STATUSES).fillna("Not answered"),
        "Reason": df["reason"].fillna(""), "Note": df["note"].fillna(""), "Training log agrees": agrees,
    }, columns=cols)


def _summarise(g: pd.DataFrame) -> dict:
    shown = len(g)
    answered = int((g["Answer"] != "Not answered").sum())
    followed = int((g["Answer"] == FOLLOW_STATUSES["followed"]).sum())
    partly = int((g["Answer"] == FOLLOW_STATUSES["partly"]).sum())
    notf = int((g["Answer"] == FOLLOW_STATUSES["not_followed"]).sum())
    chk = g["Training log agrees"].dropna()
    return {"Shown": shown, "Answered": answered, "Followed": followed, "Partly": partly, "Didn't follow": notf,
            "Answer rate (%)": round(100 * answered / shown, 1) if shown else None,
            "Followed (%)": round(100 * followed / answered, 1) if answered else None,
            "Followed or partly (%)": round(100 * (followed + partly) / answered, 1) if answered else None,
            "Log agrees (%)": round(100 * float(chk.astype(bool).mean()), 1) if len(chk) else None}


def pilot_report(con, athlete_ids, start: date, end: date, today: date | None = None) -> dict:
    """
    Adherence for the pilot: overall, per rule, per athlete, and why suggestions weren't followed.
    Followed (%) is out of the suggestions answered; Log agrees (%) compares training suggestions with
    the training log (a check on self-report that doesn't depend on the athlete answering).
    """
    rows = pilot_rows(con, athlete_ids, start, end, today)
    overall = _summarise(rows)
    by_rule = pd.DataFrame([{"Rule": rid, "Rule name": RULES[rid]["name"], **_summarise(g)}
                            for rid, g in rows.groupby("Rule")] if len(rows) else [])
    if len(by_rule):
        by_rule = by_rule.sort_values("Rule", key=lambda s: s.map(RULE_PRIORITY.index)).reset_index(drop=True)
    by_athlete = pd.DataFrame([{"Athlete": g["Athlete"].iloc[0], "USN": usn, "Sport": g["Sport"].iloc[0],
                                **_summarise(g)} for usn, g in rows.groupby("USN")] if len(rows) else [])
    if len(by_athlete):
        by_athlete = by_athlete.sort_values("Athlete").reset_index(drop=True)
    missed = rows[rows["Answer"].isin([FOLLOW_STATUSES["partly"], FOLLOW_STATUSES["not_followed"]])]
    reasons = (missed.assign(Reason=missed["Reason"].replace("", "No reason given"))
               .groupby("Reason").size().rename("Count").reset_index().sort_values("Count", ascending=False)
               if len(missed) else pd.DataFrame(columns=["Reason", "Count"]))
    athletes_shown = int(rows["athlete_id"].nunique()) if len(rows) else 0
    return {"overall": overall, "by_rule": by_rule, "by_athlete": by_athlete, "reasons": reasons,
            "rows": rows, "athletes": athletes_shown}


def own_adherence(con, athlete_id, today: date | None = None, days: int = 28) -> dict:
    today = today or date.today()
    rows = pilot_rows(con, [athlete_id], today - timedelta(days=days - 1), today, today)
    return _summarise(rows)


def pilot_start(con, athlete_ids) -> date | None:
    ids = [int(i) for i in athlete_ids]
    if not ids:
        return None
    r = con.execute(f"SELECT MIN(day) FROM plan_suggestions WHERE athlete_id IN ({','.join('?' * len(ids))})",
                    ids).fetchone()[0]
    return _d(r) if r else None


def can_view_plan(con, user, athlete_id) -> bool:
    return user is not None and user.role in ("athlete", "coach", "admin") and auth.can_view_athlete(con, user, athlete_id)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

def on_athlete_deleted(con, payload: dict) -> None:
    aid = int(payload["athlete_id"])
    con.execute("DELETE FROM plan_followups WHERE athlete_id=?", (aid,))
    con.execute("DELETE FROM plan_suggestions WHERE athlete_id=?", (aid,))
    con.commit()
