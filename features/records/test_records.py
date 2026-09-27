"""Run from the planner folder:  python3 -m pytest -q features/records"""
from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta

import pandas as pd
import pytest

import auth
import core
import data
import hooks
from features.records import logic

T = date.today()   # add_injury refuses future dates, so the seed is anchored on the real today


@pytest.fixture
def con():
    hooks.clear()
    c = data.connect(":memory:")      # registers every feature's hooks, ours included
    data.seed(c, today=T)
    yield c
    c.close()


def _aid(con, usn):
    return int(data.athlete_by_usn(con, usn)["id"])


def _user(con, username):
    return auth.user_by_username(con, username)


AARAV, SNEHA, ANANYA = "1RV25CS012", "1RV25ME021", "1RV25IS045"   # cricket (load spike), athletics, badminton


# ----------------------------------------------------------------- injuries

def test_add_injury_validates(con):
    a = _aid(con, AARAV)
    with pytest.raises(logic.RecordsError):
        logic.add_injury(con, a, T, "", "fell", 3)
    with pytest.raises(logic.RecordsError):
        logic.add_injury(con, a, T, "Ankle", "rolled it", -1)
    with pytest.raises(logic.RecordsError):
        logic.add_injury(con, a, T + timedelta(days=5), "Ankle", "rolled it", 3)
    iid = logic.add_injury(con, a, T, "Ankle", "rolled it", 3, logged_by="Coach Ramesh")
    df = logic.injuries(con, [a])
    assert list(df["id"]) == [iid] and df.iloc[0]["name"] == "Aarav Kulkarni"


def test_ramp_gets_longer_with_longer_layoffs():
    assert logic.ramp_percentages(0) == []
    assert logic.ramp_percentages(3) == [70, 100]
    assert logic.ramp_percentages(10) == [50, 70, 85, 100]
    assert logic.ramp_percentages(40)[0] == 40 and logic.ramp_percentages(40)[-1] == 100


def test_return_ramp_status_moves_out_returning_cleared(con):
    a = _aid(con, ANANYA)
    injured = T - timedelta(days=20)
    iid = logic.add_injury(con, a, injured, "Knee", "twisted landing", 10)
    inj = logic.injuries(con, [a]).set_index("id").loc[iid].to_dict() | {"id": iid}
    back = injured + timedelta(days=10)
    assert logic.return_ramp(con, inj, back - timedelta(days=1))["status"] == "out"
    r = logic.return_ramp(con, inj, back)
    assert r["status"] == "returning" and r["current"]["pct"] == 50
    assert r["baseline"] and r["weeks"][0]["target"] == round(r["baseline"] * 0.5)
    assert logic.return_ramp(con, inj, back + timedelta(days=28))["status"] == "cleared"


def test_ramp_flags_a_week_logged_over_target(con):
    a = _aid(con, ANANYA)
    iid = logic.add_injury(con, a, T - timedelta(days=4), "Wrist", "jarred it", 2)   # back 2 days ago
    for back in (2, 1, 0):                      # straight back into long, hard sessions
        data.add_session(con, a, T - timedelta(days=back), 150, 9, "Match practice")
    inj = logic.injuries(con, [a]).set_index("id").loc[iid].to_dict() | {"id": iid}
    r = logic.return_ramp(con, inj, T)
    assert r["status"] == "returning"
    assert r["current"]["flag"] == "over"


def test_squad_dashboard_by_sport_and_month_respects_scope(con):
    logic.add_injury(con, _aid(con, AARAV), T - timedelta(days=2), "Hamstring", "sprint", 14)
    logic.add_injury(con, _aid(con, "1RV24CS110"), T - timedelta(days=40), "Shoulder", "dive", 7)
    logic.add_injury(con, _aid(con, SNEHA), T - timedelta(days=1), "Calf", "cramp", 2)
    coach = _user(con, "coach.cricket")
    d = logic.squad_injury_dashboard(con, auth.visible_athlete_ids(con, coach), T)
    assert set(d["by_month"]["sport"]) == {"Cricket"}
    assert d["by_month"]["injuries"].sum() == 2
    assert [c["name"] for c in d["current"]] == ["Aarav Kulkarni"] and d["current"][0]["status"] == "out"
    ped = _user(con, "ped")
    d_all = logic.squad_injury_dashboard(con, auth.visible_athlete_ids(con, ped), T)
    assert set(d_all["by_month"]["sport"]) == {"Cricket", "Athletics"}


# ----------------------------------------------------------------- calendar

def test_month_calendar_merges_everything(con):
    a = _aid(con, AARAV)
    t = data.tournaments_for(con, a).iloc[0].to_dict()
    items = logic.month_items(con, a, t["start_date"].year, t["start_date"].month, T)
    kinds = {it["kind"] for it in items[t["start_date"]]}
    assert "tournament" in kinds
    travel_day = t["start_date"] - timedelta(days=1)
    if travel_day in items:
        assert "travel" in {it["kind"] for it in items[travel_day]}
    ev = data.events(con, a)
    cie = ev[ev["kind"] == "CIE"].iloc[0]
    cm = logic.month_items(con, a, cie["date"].year, cie["date"].month, T)
    assert any(it["kind"] == "CIE" and it["label"] == cie["title"] for it in cm[cie["date"]])
    now = logic.month_items(con, a, T.year, T.month, T)
    assert any(it["kind"] == "training" for its in now.values() for it in its)
    for d, its in now.items():
        if d.weekday() == 6:
            assert not any(it["kind"] == "classes" for it in its)
        else:
            assert any(it["kind"] == "classes" for it in its)
    # Most important first
    for its in now.values():
        order = [logic.CAL_KINDS.index(it["kind"]) for it in its]
        assert order == sorted(order)


def test_calendar_hides_classes_outside_the_semester(con):
    import settings
    a = _aid(con, AARAV)
    settings.set(con, "semester_start", T.isoformat())
    settings.set(con, "semester_end", (T + timedelta(days=90)).isoformat())
    prev = T.replace(day=1) - timedelta(days=1)
    items = logic.month_items(con, a, prev.year, prev.month, T)
    assert not any(it["kind"] == "classes" for its in items.values() for it in its)


def test_calendar_shows_out_injured_days(con):
    a = _aid(con, AARAV)
    logic.add_injury(con, a, T, "Ankle", "rolled", 3)
    items = logic.month_items(con, a, T.year, T.month, T)
    assert items[T][0]["kind"] == "injury"


def test_calendar_html_escapes_and_marks_today():
    from features.records import ui
    items = {T: [{"kind": "CIE", "label": "<b>DS</b> CIE-1"}]}
    html = ui.calendar_html(items, T.year, T.month, T, set(logic.CAL_KINDS))
    assert "&lt;b&gt;DS&lt;/b&gt;" in html and "rc-today" in html and "<b>DS" not in html


# ----------------------------------------------------------------- taper

def test_acwr_trend():
    flat = pd.Series([100.0] * 14)
    assert logic.acwr_trend(flat) == "steady"
    assert logic.acwr_trend(pd.Series([100.0] * 7 + [140.0] * 7)) == "rising"
    assert logic.acwr_trend(pd.Series([100.0] * 7 + [60.0] * 7)) == "falling"


def test_taper_for_a_spiking_athlete_halves_volume_with_two_rest_days(con):
    a = _aid(con, AARAV)                       # seed spikes his load in the last week
    t = data.tournaments_for(con, a).iloc[0].to_dict()
    p = logic.taper_plan(con, a, t, T)
    assert p["zone"] == "High risk" and p["factor"] == 0.5 and p["rest_days"] == 2
    week = [d for d in p["days"] if d["kind"] != "travel"]
    assert len(week) == 7 and sum(d["kind"] == "rest" for d in week) == 2
    assert week[-1]["kind"] == "activation" and week[-1]["date"] == p["depart"] - timedelta(days=1)
    assert abs(sum(d["target_load"] for d in week) - p["normal_daily"] * 7 * 0.5) <= 7
    travel = [d for d in p["days"] if d["kind"] == "travel"]
    assert len(travel) == int(t["travel_before"])


def test_taper_for_a_steady_athlete_keeps_one_rest_day(con):
    a = _aid(con, ANANYA)
    t = data.tournaments_for(con, a).iloc[0].to_dict()
    p = logic.taper_plan(con, a, t, T)
    assert p["zone"] in ("Sweet spot", "Under-trained", "Caution")
    assert p["rest_days"] == logic.TAPER_RULES[p["zone"]][1]
    assert all(d["minutes"] is None for d in p["days"] if d["kind"] == "rest")


def test_taper_with_no_logs_uses_percentages_only(con):
    a = _aid(con, ANANYA)
    con.execute("DELETE FROM sessions WHERE athlete_id=?", (a,))
    t = data.tournaments_for(con, a).iloc[0].to_dict()
    p = logic.taper_plan(con, a, t, T)
    assert p["zone"] == "No data yet" and all(d["target_load"] in (None, 0) for d in p["days"])


def test_squad_taper_lists_athletes_leaving_soon(con):
    ped = _user(con, "ped")
    rows = logic.squad_taper(con, auth.visible_athlete_ids(con, ped), T, horizon=14)
    names = {r["name"] for r in rows}
    assert {"Ananya Rao", "Meghana S"} <= names           # badminton, 5 days out
    assert "Sneha Patil" not in names                      # athletics, 21 days out
    assert [r["depart"] for r in rows] == sorted(r["depart"] for r in rows)


# ----------------------------------------------------------------- semester summary

def test_semester_window_defaults_and_settings(con):
    import settings
    assert logic.semester_window(con, T) == (T - timedelta(days=120), T)
    settings.set(con, "semester_start", "2026-08-01")
    assert logic.semester_window(con, T) == (date(2026, 8, 1), date(2026, 8, 1) + timedelta(days=120))
    settings.set(con, "semester_end", "2026-12-15")
    assert logic.semester_window(con, T) == (date(2026, 8, 1), date(2026, 12, 15))


def test_semester_summary_counts_tournaments_training_and_injuries(con):
    a = _aid(con, AARAV)
    logic.add_injury(con, a, T - timedelta(days=3), "Back", "stiff", 2)
    s = logic.semester_summary(con, a, T - timedelta(days=60), T + timedelta(days=60), today=T)
    assert s["athlete"]["usn"] == AARAV
    assert s["totals"]["tournaments"] == 1
    t = s["tournaments"][0]
    assert t["days_away"] >= 4 and t["tests_missed"] >= 1 and t["letter"] == "Not drafted"
    assert s["training"]["sessions"] > 30 and s["training"]["weeks_logged"] >= 6
    assert len(s["injuries"]) == 1 and s["wellness"]["sleep"] > 0
    doc = logic.summary_docx(s, "R V College of Engineering")
    assert doc[:2] == b"PK"


def test_semester_summary_without_health_for_faculty(con):
    a = _aid(con, AARAV)
    logic.add_injury(con, a, T - timedelta(days=3), "Back", "stiff", 2)
    s = logic.semester_summary(con, a, T - timedelta(days=60), T, include_health=False, today=T)
    assert "injuries" not in s and "wellness" not in s
    logic.summary_docx(s)
    df = logic.summaries_table(con, [a], T - timedelta(days=60), T, include_health=False)
    assert "Injuries" not in df.columns and df.iloc[0]["USN"] == AARAV


def test_semester_summary_shows_letter_stage(con):
    from features.letters import logic as letters
    a = _aid(con, AARAV)
    tid = int(data.tournaments_for(con, a).iloc[0]["id"])
    lid = letters.create_letter(con, a, tid)
    for _ in range(3):
        letters.advance_letter(con, lid)
    s = logic.semester_summary(con, a, T - timedelta(days=10), T + timedelta(days=60), today=T)
    assert s["tournaments"][0]["letter"] == "HoD approved" and s["totals"]["letters_approved"] == 1


# ----------------------------------------------------------------- export

def _zip_tables(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    return {n[:-4]: pd.read_csv(io.BytesIO(z.read(n))) for n in z.namelist() if n.endswith(".csv")}, z


def test_admin_export_has_everything_but_secrets(con):
    logic.add_injury(con, _aid(con, AARAV), T, "Ankle", "rolled", 3)
    tables, z = _zip_tables(logic.export_zip(con, _user(con, "admin")))
    assert {"athletes", "sessions", "tournaments", "users", "records_injuries"} <= set(tables)
    assert "password_hash" not in tables["users"].columns
    assert not {"meta", "auth_sessions", "verification_keys"} & set(tables)
    assert len(tables["athletes"]) == 10
    assert "README.txt" in z.namelist()


def test_coach_export_is_their_sport_only(con):
    tables, _ = _zip_tables(logic.export_zip(con, _user(con, "coach.cricket")))
    assert set(tables["athletes"]["sport"]) == {"Cricket"}
    cricket = set(tables["athletes"]["id"])
    assert set(tables["sessions"]["athlete_id"]) <= cricket
    assert set(tables["tournaments"]["sport"]) == {"Cricket"}
    assert "users" not in tables


def test_faculty_export_leaves_out_health_data(con):
    logic.add_injury(con, _aid(con, AARAV), T, "Ankle", "rolled", 3)
    tables = logic.export_tables(con, _user(con, "proctor.cse"))
    assert "wellness" not in tables and "records_injuries" not in tables
    assert set(tables["athletes"]["dept"]) == {"CSE"}


def test_athlete_export_is_only_their_own_rows(con):
    a = _aid(con, AARAV)
    me = _user(con, AARAV)
    tables = logic.export_tables(con, me)
    assert list(tables["athletes"]["id"]) == [a]
    assert list(tables["users"]["username"]) == [AARAV.lower()]
    assert set(tables["sessions"]["athlete_id"]) == {a}
    assert "imports_class_events" not in tables


def test_export_requires_permission(con):
    fake = auth.User(id=999, username="x", name="x", role="nobody")
    with pytest.raises(PermissionError):
        logic.export_tables(con, fake)


# ----------------------------------------------------------------- consent and deletion

def test_consent_lifecycle(con):
    a = _aid(con, AARAV)
    assert logic.consent_label(logic.consent_status(con, a)) == "Not answered"
    logic.set_consent(con, a, True)
    assert logic.consent_status(con, a)["current"]
    logic.set_consent(con, a, False)
    assert logic.consent_label(logic.consent_status(con, a)) == "Withdrawn"
    con.execute("INSERT INTO records_consent(athlete_id,version,accepted,decided_at) VALUES (?,?,?,?)",
                (a, logic.CONSENT_VERSION - 1, 1, "2026-01-01T00:00:00"))
    assert logic.consent_label(logic.consent_status(con, a)) == "Agreed to an older notice"
    ov = logic.consent_overview(con, [a, _aid(con, SNEHA)])
    assert list(ov["Consent"]) == ["Agreed to an older notice", "Not answered"]


def test_deletion_request_is_idempotent_and_cancellable(con):
    a = _aid(con, AARAV)
    r1 = logic.request_deletion(con, a, "graduating")
    assert logic.request_deletion(con, a) == r1
    assert [r["usn"] for r in logic.deletion_requests(con)] == [AARAV]
    logic.cancel_deletion(con, a)
    assert logic.pending_deletion(con, a) is None and logic.deletion_requests(con) == []


def test_admin_deletes_athlete_everywhere(con):
    a = _aid(con, AARAV)
    logic.add_injury(con, a, T, "Ankle", "rolled", 3)
    logic.set_consent(con, a, True)
    logic.request_deletion(con, a, "my phone number is 98...")
    with pytest.raises(PermissionError):
        logic.delete_athlete_everywhere(con, _user(con, "coach.cricket"), a)
    logic.delete_athlete_everywhere(con, _user(con, "admin"), a)
    assert data.athlete(con, a) is None and auth.user_by_username(con, AARAV) is None
    for table in ("records_injuries", "records_consent", "sessions", "wellness", "entries"):
        assert con.execute(f"SELECT COUNT(*) FROM {table} WHERE athlete_id=?", (a,)).fetchone()[0] == 0
    done = logic.deletion_requests(con, "done")
    assert len(done) == 1 and done[0]["reason"] is None and done[0]["handled_by"] == "Sports Office Admin"
    assert done[0]["name"] is None       # nothing left to join a name onto
    with pytest.raises(logic.RecordsError):
        logic.delete_athlete_everywhere(con, _user(con, "admin"), a)


def test_admin_delete_without_request_leaves_a_trace(con):
    a = _aid(con, SNEHA)
    logic.delete_athlete_everywhere(con, _user(con, "admin"), a)
    done = logic.deletion_requests(con, "done")
    assert len(done) == 1 and done[0]["athlete_id"] == a


# ----------------------------------------------------------------- wiring

def test_feature_registers_tabs_and_cleanup_hook():
    import features
    from features.records import FEATURE
    assert FEATURE["hooks"] == {"athlete_deleted": "logic:on_athlete_deleted"}
    loaded = {f["name"]: f for f in features.load_all(force=True)}
    assert "records" not in features.load_errors
    labels = {t["label"]: t["roles"] for t in loaded["records"]["tabs"]}
    assert "faculty" not in labels["Injuries"] and "athlete" not in labels["Export"]
    assert labels["Privacy & data"] == {"athlete", "admin"}


def _ui_script():
    """Render every records tab for the user named in session_state (runs inside AppTest)."""
    from datetime import date as _date

    import streamlit as st

    import auth as _auth
    import data as _data
    from features.records import logic as _logic, ui as _ui

    con = _data.connect(":memory:")
    _data.seed(con, today=_date.today())
    a = _data.athlete_by_usn(con, "1RV25CS012")["id"]
    _logic.add_injury(con, a, _date.today(), "Ankle", "rolled", 10, logged_by="t")
    _logic.request_deletion(con, a, "test")
    user = _auth.user_by_username(con, st.session_state["who"])
    for name in ("render_calendar", "render_injuries", "render_taper", "render_summary", "render_export",
                 "render_privacy"):
        getattr(_ui, name)(con, user)


@pytest.mark.parametrize("who", ["1rv25cs012", "coach.cricket", "ped", "proctor.cse", "admin"])
def test_every_tab_renders_for_each_role(who):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_function(_ui_script, default_timeout=120)
    at.session_state["who"] = who
    at.run()
    assert not at.exception, [e.value for e in at.exception]
