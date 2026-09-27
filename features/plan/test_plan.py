"""Run from the planner folder:  python3 -m pytest -q features/plan"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

import auth
import data
import hooks
from features.plan import logic

T = date.today()
AARAV, DIYA, ROHAN, SNEHA, ANANYA, MEGHANA = ("1RV25CS012", "1RV25CS034", "1RV25EC077", "1RV25ME021",
                                              "1RV25IS045", "1RV25CS101")


@pytest.fixture
def con():
    hooks.clear()
    c = data.connect(":memory:")
    data.seed(c, today=T)
    yield c
    c.close()


def _aid(con, usn):
    return int(data.athlete_by_usn(con, usn)["id"])


def _user(con, username):
    return auth.user_by_username(con, username)


def _day(plan, d):
    return next(x for x in plan["days"] if x["date"] == d)


def _clean(con, aid):
    """An athlete with a plain timetable, no tests, no tournaments and steady training."""
    con.execute("DELETE FROM events WHERE athlete_id=?", (aid,))
    con.execute("DELETE FROM entries WHERE athlete_id=?", (aid,))
    con.execute("DELETE FROM sessions WHERE athlete_id=?", (aid,))
    con.execute("DELETE FROM wellness WHERE athlete_id=?", (aid,))
    for back in range(1, 43):
        data.add_session(con, aid, T - timedelta(days=back), 60, 5, "Skills")
    data.set_timetable(con, aid, [{"weekday": wd, "subject": s, "kind": "class"}
                                  for wd in range(6) for s in ("A", "B", "C", "D")])
    con.commit()


# ----------------------------------------------------------------- the plan

def test_plan_covers_14_days_and_every_suggestion_names_a_rule(con):
    for aid in range(1, 11):
        p = logic.build_plan(con, aid, T)
        assert len(p["days"]) == 14 and p["days"][0]["date"] == T and p["end"] == T + timedelta(days=13)
        for d in p["days"]:
            for s in (d["training"], d["study"], d["academic"]):
                if s:
                    assert s["rule_id"] in logic.RULES and s["rule"] == logic.RULES[s["rule_id"]]["name"]
                    assert s["text"]
        assert p["headlines"] and all(h["rule_id"] in logic.RULES for h in p["headlines"])


def test_ease_training_before_a_test(con):
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    cie = T + timedelta(days=6)
    data.add_event(con, aid, cie, "CIE", "Maths CIE-1")
    p = logic.build_plan(con, aid, T)
    for k in (1, 2):
        tr = _day(p, cie - timedelta(days=k))["training"]
        assert tr["rule_id"] == "P1" and tr["level"] == "easy" and "Maths CIE-1" in tr["text"]
    assert _day(p, cie)["training"]["level"] == "light"
    assert _day(p, cie - timedelta(days=3))["training"] is None      # only 2 days before
    for k in (1, 2, 3):
        st = _day(p, cie - timedelta(days=k))["study"]
        assert st["rule_id"] == "P3" and "Maths CIE-1" in st["text"]


def test_study_slots_only_on_light_timetable_days(con):
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    light_wd = (T + timedelta(days=2)).weekday()
    if light_wd == 6:
        light_wd = 0
    rows = [{"weekday": wd, "subject": s, "kind": "class"} for wd in range(6) for s in ("A", "B", "C", "D")
            if not (wd == light_wd and s == "D")]
    data.set_timetable(con, aid, rows)
    p = logic.build_plan(con, aid, T)
    for d in p["days"]:
        st = d["study"]
        if d["date"].weekday() == 6 or d["date"].weekday() == light_wd:
            assert st and st["rule_id"] == "P2" and "60 minute" in st["text"]
        else:
            assert st is None


def test_study_slot_is_90_minutes_when_a_test_is_near(con):
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    data.set_timetable(con, aid, [])
    data.add_event(con, aid, T + timedelta(days=6), "CIE", "Maths CIE-1")
    p = logic.build_plan(con, aid, T)
    assert _day(p, T)["study"]["rule_id"] == "P2" and "90 minute" in _day(p, T)["study"]["text"]
    assert "Maths CIE-1" in _day(p, T)["study"]["text"]


def test_travel_competition_and_recovery_days(con):
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    start = T + timedelta(days=4)
    data.add_tournament(con, "Zonal Cup", "Football", "Mysuru", start, start + timedelta(days=1), 1, 1, [aid])
    p = logic.build_plan(con, aid, T)
    travel = [start - timedelta(days=1), start + timedelta(days=2)]
    for d in travel:
        day = _day(p, d)
        assert day["away"] == "travel"
        assert day["training"]["rule_id"] == "P4" and day["training"]["level"] == "rest"
        assert day["study"]["rule_id"] == "P4" and "Light study only" in day["study"]["text"]
    for d in (start, start + timedelta(days=1)):
        day = _day(p, d)
        assert day["training"]["rule_id"] == "P10" and day["training"]["level"] == "compete"
        assert day["study"] is None
    for k in (1, 2):
        day = _day(p, start + timedelta(days=2 + k))
        assert day["training"]["rule_id"] == "P5" and day["training"]["level"] == "recovery"
        assert day["study"]["rule_id"] == "P5" and "Catch up" in day["study"]["text"]
    assert any(h["rule_id"] == "P6" for h in p["headlines"])     # taper week before leaving


def test_test_during_a_tournament_is_not_revised_for(con):
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    start = T + timedelta(days=5)
    data.add_tournament(con, "Zonal Cup", "Football", "Mysuru", start, start + timedelta(days=2), 0, 0, [aid])
    data.add_event(con, aid, start + timedelta(days=1), "CIE", "Maths CIE-1")
    p = logic.build_plan(con, aid, T)
    assert [t["missed"] for t in p["academic"]["tests"]] == [True]
    assert not any(s and s["rule_id"] in ("P1", "P3") for d in p["days"] for s in (d["training"], d["study"]))


def test_lighter_rule_wins_and_the_other_is_kept(con):
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    start = T + timedelta(days=8)                      # taper week is T+1 .. T+7
    data.add_tournament(con, "Zonal Cup", "Football", "Mysuru", start, start, 0, 0, [aid])
    data.add_event(con, aid, T + timedelta(days=4), "CIE", "Maths CIE-1")   # ease off on T+2 and T+3
    p = logic.build_plan(con, aid, T)
    d2, d3 = _day(p, T + timedelta(days=2)), _day(p, T + timedelta(days=3))
    # T+2: taper says a sharp (moderate) session, P1 says easy: easy wins, the taper is kept as "also".
    assert d2["training"]["rule_id"] == "P1" and [s["rule_id"] for s in d2["also"]] == ["P6"]
    # T+3: taper says light, which is lighter than P1's easy, so the taper wins.
    assert d3["training"]["rule_id"] == "P6" and d3["training"]["level"] == "light"
    assert [s["rule_id"] for s in d3["also"]] == ["P1"]


def test_load_spike_rule(con):
    aid = _aid(con, AARAV)          # seeded with a spike in the last week
    p = logic.build_plan(con, aid, T)
    assert p["sports"]["load"]["zone"] == "High risk"
    for i in range(3):
        tr = _day(p, T + timedelta(days=i))["training"]
        assert tr["level"] in ("rest", "recovery", "light")
    assert any(h["rule_id"] == "P7" for h in p["headlines"])


def test_recovery_below_baseline(con):
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    for w, (sl, so, stv) in enumerate([(4, 2, 2), (4, 2, 2), (4, 2, 2), (2, 4, 4)]):
        data.add_wellness(con, aid, T - timedelta(days=7 * (3 - w)), sl, so, stv)
    r = logic.recovery_status(con, aid, T)
    assert r["state"] == "below" and r["score"] == 6 and r["baseline"] == 12
    p = logic.build_plan(con, aid, T)
    assert _day(p, T)["training"]["rule_id"] == "P11"
    assert _day(p, T + timedelta(days=2))["training"] is None


def test_recovery_needs_a_baseline(con):
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    assert logic.recovery_status(con, aid, T)["state"] == "no_data"
    data.add_wellness(con, aid, T, 1, 5, 5)
    assert logic.recovery_status(con, aid, T)["state"] == "no_baseline"


def test_injured_days(con):
    from features.records import logic as records
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    records.add_injury(con, aid, T, "ankle", "sprain", 4)
    p = logic.build_plan(con, aid, T)
    assert p["sports"]["injured"]
    for i in range(4):
        assert _day(p, T + timedelta(days=i))["training"]["rule_id"] == "P9"
    assert _day(p, T + timedelta(days=5))["training"] is None


def test_attendance_rule(con):
    import settings
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    settings.set(con, "semester_start", (T - timedelta(days=20)).isoformat())
    settings.set(con, "semester_end", (T + timedelta(days=10)).isoformat())
    data.add_tournament(con, "Long Tour", "Football", "Kochi", T - timedelta(days=12), T - timedelta(days=3), 0, 0,
                        [aid])
    p = logic.build_plan(con, aid, T)
    a = p["academic"]["attendance"]
    assert a["risk"] == "high"
    today = _day(p, T)["academic"]
    assert today["rule_id"] == "P8" and a["subject"] in today["text"]


def test_plan_table(con):
    df = logic.plan_table(logic.build_plan(con, _aid(con, DIYA), T))
    assert list(df.columns) == ["Date", "What's on", "Training", "Study", "Rules"] and len(df) == 14
    assert df["What's on"].str.contains("Tournament").any()


# ----------------------------------------------------------------- pilot logging

def test_record_plan_saves_and_freezes_the_past(con):
    aid = _aid(con, DIYA)
    p = logic.build_plan(con, aid, T)
    ids = logic.record_plan(con, aid, p, T)
    n = sum(1 for d in p["days"] for s in (d["training"], d["study"], d["academic"]) if s)
    assert len(ids) == n
    assert logic.record_plan(con, aid, p, T) == ids       # idempotent
    # Tomorrow: today's rows are in the past and stay as they were even if the plan changes.
    before = con.execute("SELECT id, text FROM plan_suggestions WHERE athlete_id=? AND day=?",
                         (aid, T.isoformat())).fetchall()
    data.add_event(con, aid, T + timedelta(days=1), "CIE", "Surprise CIE")
    logic.record_plan(con, aid, logic.build_plan(con, aid, T + timedelta(days=1)), T + timedelta(days=1))
    after = con.execute("SELECT id, text FROM plan_suggestions WHERE athlete_id=? AND day=?",
                        (aid, T.isoformat())).fetchall()
    assert before == after


def test_future_rows_follow_the_plan(con):
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    cie = T + timedelta(days=6)
    data.add_event(con, aid, cie, "CIE", "Maths CIE-1")
    logic.record_plan(con, aid, logic.build_plan(con, aid, T), T)
    assert con.execute("SELECT COUNT(*) FROM plan_suggestions WHERE rule_id='P1'").fetchone()[0] == 3
    con.execute("DELETE FROM events WHERE athlete_id=?", (aid,))
    logic.record_plan(con, aid, logic.build_plan(con, aid, T), T)
    assert con.execute("SELECT COUNT(*) FROM plan_suggestions WHERE rule_id='P1'").fetchone()[0] == 0


def test_log_followup_and_permissions(con):
    aid = _aid(con, DIYA)
    logic.record_plan(con, aid, logic.build_plan(con, aid, T), T)
    todo = logic.to_answer(con, aid, T)
    assert todo and all(s["day"] == T.isoformat() for s in todo)
    sid = todo[0]["id"]
    me = _user(con, DIYA)
    with pytest.raises(PermissionError):
        logic.log_followup(con, _user(con, "coach.cricket"), sid, "followed", today=T)
    with pytest.raises(PermissionError):
        logic.log_followup(con, _user(con, AARAV), sid, "followed", today=T)
    with pytest.raises(ValueError):
        logic.log_followup(con, me, sid, "maybe", today=T)
    logic.log_followup(con, me, sid, "not_followed", "No time", "exam prep", today=T)
    logic.log_followup(con, me, sid, "followed", "No time", today=T)    # answer again: replaces, reason dropped
    row = con.execute("SELECT status, reason FROM plan_followups WHERE suggestion_id=?", (sid,)).fetchall()
    assert row == [("followed", "")]
    assert sid not in {s["id"] for s in logic.to_answer(con, aid, T)}
    future = con.execute("SELECT id FROM plan_suggestions WHERE athlete_id=? AND day>?",
                         (aid, T.isoformat())).fetchone()[0]
    with pytest.raises(ValueError):
        logic.log_followup(con, me, future, "followed", today=T)


def test_answered_rows_are_not_rewritten(con):
    aid = _aid(con, ROHAN)
    _clean(con, aid)
    data.set_timetable(con, aid, [])
    logic.record_plan(con, aid, logic.build_plan(con, aid, T), T)
    sid = con.execute("SELECT id FROM plan_suggestions WHERE athlete_id=? AND day=? AND area='study'",
                      (aid, T.isoformat())).fetchone()[0]
    logic.log_followup(con, _user(con, ROHAN), sid, "partly", "Forgot", today=T)
    data.add_event(con, aid, T + timedelta(days=1), "CIE", "Maths CIE-1")    # today's study would now be P3
    logic.record_plan(con, aid, logic.build_plan(con, aid, T), T)
    assert con.execute("SELECT rule_id FROM plan_suggestions WHERE id=?", (sid,)).fetchone()[0] == "P2"


def test_log_agrees():
    s = lambda rows: pd.DataFrame(rows, columns=["date", "minutes", "rpe", "type"])  # noqa: E731
    assert logic.log_agrees("rest", s([])) is True
    assert logic.log_agrees("rest", s([("d", 15, 2, "x")])) is True
    assert logic.log_agrees("rest", s([("d", 60, 2, "x")])) is False
    assert logic.log_agrees("easy", s([("d", 60, 5, "x")])) is True
    assert logic.log_agrees("easy", s([("d", 60, 8, "x")])) is False
    assert logic.log_agrees("moderate", s([("d", 60, 8, "x")])) is None
    assert logic.log_agrees(None, s([])) is None


def test_pilot_report(con):
    y = T - timedelta(days=1)
    a1, a2 = _aid(con, ROHAN), _aid(con, SNEHA)
    for aid in (a1, a2):
        logic.record_plan(con, aid, logic.build_plan(con, aid, y), y)
    s1 = logic.to_answer(con, a1, y)
    s2 = logic.to_answer(con, a2, y)
    logic.log_followup(con, _user(con, ROHAN), s1[0]["id"], "followed", today=T)
    logic.log_followup(con, _user(con, SNEHA), s2[0]["id"], "not_followed", "No time", today=T)
    rep = logic.pilot_report(con, [a1, a2], y, T, T)
    o = rep["overall"]
    shown = con.execute("SELECT COUNT(*) FROM plan_suggestions WHERE day<=?", (T.isoformat(),)).fetchone()[0]
    assert o["Shown"] == shown and o["Answered"] == 2 and o["Followed"] == 1 and o["Followed (%)"] == 50.0
    assert rep["athletes"] == 2
    assert set(rep["by_athlete"]["USN"]) == {ROHAN, SNEHA}
    assert rep["by_rule"]["Shown"].sum() == shown
    assert rep["reasons"].to_dict("records") == [{"Reason": "No time", "Count": 1}]
    tr = rep["rows"][(rep["rows"]["Area"] == "training") & (rep["rows"]["Date"] == y.isoformat())]
    assert tr["Training log agrees"].notna().all() or tr["Level"].isin(["moderate", "compete"]).any()
    # Scoped: a coach sees only their own sport.
    coach = _user(con, "coach.cricket")
    assert logic.pilot_report(con, auth.visible_athlete_ids(con, coach), y, T, T)["overall"]["Shown"] == 0
    assert logic.pilot_start(con, [a1, a2]) == y


def test_own_adherence(con):
    aid = _aid(con, DIYA)
    logic.record_plan(con, aid, logic.build_plan(con, aid, T), T)
    sid = logic.to_answer(con, aid, T)[0]["id"]
    logic.log_followup(con, _user(con, DIYA), sid, "partly", today=T)
    o = logic.own_adherence(con, aid, T)
    assert o["Answered"] == 1 and o["Partly"] == 1


def test_can_view_plan(con):
    aid = _aid(con, AARAV)
    assert logic.can_view_plan(con, _user(con, AARAV), aid)
    assert not logic.can_view_plan(con, _user(con, DIYA), aid)
    assert logic.can_view_plan(con, _user(con, "coach.cricket"), aid)
    assert logic.can_view_plan(con, _user(con, "admin"), aid)
    assert not logic.can_view_plan(con, _user(con, "hod.cse"), aid)      # health data: not for faculty


def test_athlete_deleted_removes_rows(con):
    aid = _aid(con, DIYA)
    logic.record_plan(con, aid, logic.build_plan(con, aid, T), T)
    sid = logic.to_answer(con, aid, T)[0]["id"]
    logic.log_followup(con, _user(con, DIYA), sid, "followed", today=T)
    data.delete_athlete(con, aid)
    assert con.execute("SELECT COUNT(*) FROM plan_suggestions WHERE athlete_id=?", (aid,)).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM plan_followups WHERE athlete_id=?", (aid,)).fetchone()[0] == 0


def test_feature_registers():
    import features
    labels = [lbl for lbl, _ in features.tabs_for("athlete")]
    assert "Next 14 days" in labels and "Pilot results" not in labels
    assert "Pilot results" in [lbl for lbl, _ in features.tabs_for("coach")]
    assert "Next 14 days" not in [lbl for lbl, _ in features.tabs_for("faculty")]
