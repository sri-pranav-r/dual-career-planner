"""Run from the planner folder:  python3 -m pytest -q features/squad"""
import io
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

import auth
import data
import hooks
import settings
from features.letters import logic as letters
from features.squad import logic

TODAY = date(2026, 9, 28)
HERE = Path(__file__).parent


@pytest.fixture
def con():
    c = data.connect(":memory:")
    c.executescript((HERE.parent / "letters" / "schema.sql").read_text())
    c.executescript((HERE / "schema.sql").read_text())
    data.seed(c, today=TODAY)
    hooks.clear()
    hooks.on("tournament_updated", logic.on_tournament_changed)
    hooks.on("entries_changed", logic.on_tournament_changed)
    hooks.on("athlete_deleted", logic.on_athlete_deleted)
    c.execute("DELETE FROM letters_notifications")
    c.commit()
    yield c
    hooks.clear()
    c.close()


def u(con, username):
    return auth.user_by_username(con, username)


def cricket(con):
    return con.execute("SELECT id FROM tournaments WHERE sport='Cricket'").fetchone()[0]


def aarav(con):
    return data.athlete_by_usn(con, "1RV25CS012")["id"]


def first_test(con):
    aid = aarav(con)
    return aid, logic.missed_tests(con, aid)[0]


def request(con):
    aid, t = first_test(con)
    rid = logic.request_makeup(con, u(con, "1RV25CS012"), aid, t["tournament_id"], t["test_date"], t["kind"], t["title"])
    return aid, t, rid


# ----------------------------------------------------------------- make-up tests

def test_missed_tests_lists_cie_in_away_window(con):
    tests = logic.missed_tests(con, aarav(con))
    assert len(tests) == 3
    assert {t["kind"] for t in tests} == {"CIE"}
    assert all(t["request"] is None for t in tests)


def test_request_is_idempotent_and_logged(con):
    aid, t, rid = request(con)
    again = logic.request_makeup(con, u(con, "1RV25CS012"), aid, t["tournament_id"], t["test_date"], t["kind"], t["title"])
    assert again == rid
    assert logic.get_request(con, rid)["status"] == "requested"
    assert [h["status"] for h in logic.makeup_history(con, rid)] == ["requested"]


def test_only_the_athlete_can_request(con):
    aid, t = first_test(con)
    with pytest.raises(PermissionError):
        logic.request_makeup(con, u(con, "1RV24CS110"), aid, t["tournament_id"], t["test_date"], t["kind"], t["title"])


def test_cannot_request_a_test_outside_the_window(con):
    aid, t = first_test(con)
    with pytest.raises(logic.MakeupError):
        logic.request_makeup(con, u(con, "1RV25CS012"), aid, t["tournament_id"], t["test_date"] + timedelta(days=30),
                             t["kind"], t["title"])


def test_proctor_schedules_and_athlete_is_notified(con):
    aid, t, rid = request(con)
    logic.approve_makeup(con, u(con, "proctor.cse"), rid, t["test_date"] + timedelta(days=7), "Room 204, 2 pm")
    r = logic.get_request(con, rid)
    assert r["status"] == "approved" and r["makeup_note"] == "Room 204, 2 pm"
    msgs = letters.notifications(con, aid)
    assert msgs[0]["kind"] == "makeup_scheduled" and "Room 204" in msgs[0]["message"]
    logic.complete_makeup(con, u(con, "proctor.cse"), rid)
    assert logic.get_request(con, rid)["status"] == "completed"


def test_coach_cannot_schedule_and_date_must_follow_test(con):
    _, t, rid = request(con)
    with pytest.raises(PermissionError):
        logic.approve_makeup(con, u(con, "coach.cricket"), rid, t["test_date"] + timedelta(days=7))
    with pytest.raises(logic.MakeupError):
        logic.approve_makeup(con, u(con, "hod.cse"), rid, t["test_date"] - timedelta(days=1))


def test_reject_needs_reason_then_athlete_can_ask_again(con):
    aid, t, rid = request(con)
    with pytest.raises(logic.MakeupError):
        logic.reject_makeup(con, u(con, "hod.cse"), rid, "  ")
    logic.reject_makeup(con, u(con, "hod.cse"), rid, "Attach the PED letter")
    assert logic.get_request(con, rid)["status"] == "rejected"
    assert letters.notifications(con, aid)[0]["kind"] == "makeup_rejected"
    again = logic.request_makeup(con, u(con, "1RV25CS012"), aid, t["tournament_id"], t["test_date"], t["kind"], t["title"])
    assert again == rid and logic.get_request(con, rid)["status"] == "requested"


def test_withdraw_by_athlete(con):
    _, _, rid = request(con)
    logic.withdraw_makeup(con, u(con, "1RV25CS012"), rid)
    assert logic.get_request(con, rid)["status"] == "withdrawn"


def test_requests_table_shows_letter_status(con):
    aid, _, _ = request(con)
    df = logic.requests_table(con, [aid])
    assert len(df) == 1 and df.iloc[0]["Status"] == "Requested" and df.iloc[0]["Exemption letter"] == "Not drafted"
    assert logic.requests_table(con, [aid], statuses=("approved",)).empty


def test_request_docx_is_a_word_file(con):
    _, _, rid = request(con)
    blob = logic.makeup_request_docx(con, rid)
    assert zipfile.ZipFile(io.BytesIO(blob)).testzip() is None
    from docx import Document
    text = "\n".join(p.text for p in Document(io.BytesIO(blob)).paragraphs)
    assert "make-up" in text and "1RV25CS012" in text


def test_moving_the_tournament_withdraws_requests_that_no_longer_clash(con):
    aid, _, rid = request(con)
    tid = cricket(con)
    t = data.tournament(con, tid)
    data.update_tournament(con, tid, start_date=t["start_date"] + timedelta(days=20), end_date=t["end_date"] + timedelta(days=20))
    assert logic.get_request(con, rid)["status"] == "withdrawn"
    assert any(n["kind"] == "makeup_withdrawn" for n in letters.notifications(con, aid))


def test_dropping_the_athlete_withdraws_requests(con):
    aid, _, rid = request(con)
    tid = cricket(con)
    data.set_entries(con, tid, [a for a in data.entries(con, tid) if a != aid])
    assert logic.get_request(con, rid)["status"] == "withdrawn"


def test_athlete_deleted_clears_rows(con):
    aid, _, rid = request(con)
    hooks.emit(con, "athlete_deleted", {"athlete_id": aid})
    assert con.execute("SELECT COUNT(*) FROM squad_makeup_requests").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM squad_makeup_history WHERE request_id=?", (rid,)).fetchone()[0] == 0


# ----------------------------------------------------------------- bulk letters

def test_bulk_letters_zip_has_every_entrant_and_an_index(con):
    tid = cricket(con)
    blob, index = logic.bulk_letters(con, u(con, "coach.cricket"), tid)
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert len([n for n in names if n.endswith(".docx")]) == 2 and "index.csv" in names
    assert {r["USN"] for r in index} == {"1RV25CS012", "1RV24CS110"}
    assert all(letters.find_letter(con, a, tid)["stage"] == "drafted" for a in data.entries(con, tid))


def test_bulk_letters_can_skip_tracking(con):
    tid = cricket(con)
    logic.bulk_letters(con, u(con, "ped"), tid, record=False)
    assert all(letters.find_letter(con, a, tid) is None for a in data.entries(con, tid))


def test_bulk_letters_wrong_sport_refused(con):
    with pytest.raises(PermissionError):
        logic.bulk_letters(con, u(con, "coach.football"), cricket(con))
    with pytest.raises(PermissionError):
        logic.bulk_letters(con, u(con, "proctor.cse"), cricket(con))


# ----------------------------------------------------------------- squad selection

def test_preview_counts_clashes_for_candidates(con):
    t = data.tournament(con, cricket(con))
    df = logic.selection_preview(con, u(con, "coach.cricket"), t, today=TODAY)
    assert set(df["USN"]) == {"1RV25CS012", "1RV24CS110"}
    assert df["Entered"].all()
    row = df[df["USN"] == "1RV25CS012"].iloc[0]
    assert row["CIE"] == 3 and "Misses a CIE" in row["Flags"]


def test_preview_for_proposed_dates_and_double_booking(con):
    t = data.tournament(con, cricket(con))
    proposed = {"id": None, "name": "Friendly", "sport": "Cricket", "venue": "", "start_date": t["start_date"],
                "end_date": t["end_date"], "travel_before": 0, "travel_after": 0}
    df = logic.selection_preview(con, u(con, "coach.cricket"), proposed, today=TODAY)
    assert not df["Entered"].any()
    assert (df["Other tournaments"] == t["name"]).all() and df["Flags"].str.contains("Double-booked").all()
    assert df["Attendance after (%)"].notna().all()


def test_finalise_keeps_athletes_outside_scope(con):
    tid = cricket(con)
    outsider = data.athlete_by_usn(con, "1RV25EC077")["id"]   # a footballer the PED added
    data.set_entries(con, tid, data.entries(con, tid) + [outsider])
    res = logic.finalise_selection(con, u(con, "coach.cricket"), tid, [aarav(con)])
    assert set(data.entries(con, tid)) == {aarav(con), outsider}
    assert res["removed"] == [data.athlete_by_usn(con, "1RV24CS110")["id"]]
    with pytest.raises(PermissionError):
        logic.finalise_selection(con, u(con, "coach.cricket"), tid, [outsider])


def test_create_with_selection(con):
    coach = u(con, "coach.cricket")
    tid = logic.create_with_selection(con, coach, "Zonal Friendly", "Cricket", "Mysuru", TODAY + timedelta(days=3),
                                      TODAY + timedelta(days=4), 0, 0, [aarav(con)])
    assert data.entries(con, tid) == [aarav(con)]
    with pytest.raises(PermissionError):
        logic.create_with_selection(con, coach, "X", "Football", "", TODAY, TODAY, 0, 0, [])


# ----------------------------------------------------------------- attendance risk

def _semester(con):
    settings.set(con, "semester_start", (TODAY - timedelta(days=30)).isoformat())
    settings.set(con, "semester_end", (TODAY + timedelta(days=90)).isoformat())


def test_semester_window_defaults_and_settings(con):
    s, e, known = logic.semester_window(con, TODAY)
    assert not known and s < TODAY < e and (e - s).days == logic.SEMESTER_DAYS
    _semester(con)
    assert logic.semester_window(con, TODAY) == (TODAY - timedelta(days=30), TODAY + timedelta(days=90), True)


def test_projection_counts_sport_days_and_on_duty_credit(con):
    _semester(con)
    aid, tid = aarav(con), cricket(con)
    before = logic.attendance_projection(con, aid, TODAY)
    assert before["sport_days"] == 6 and before["on_duty_days"] == 0 and before["worst"]["pct"] < 100

    lid = letters.create_letter(con, aid, tid)
    for _ in range(3):
        letters.advance_letter(con, lid)                      # to HoD approved
    after = logic.attendance_projection(con, aid, TODAY)
    assert after["on_duty_days"] == 6 and after["worst"]["pct"] == 100.0

    settings.set(con, "max_on_duty_days", 2)
    assert logic.attendance_projection(con, aid, TODAY)["on_duty_days"] == 2
    settings.set(con, "on_duty_counts_as_present", False)
    assert logic.attendance_projection(con, aid, TODAY)["worst"]["pct"] == before["worst"]["pct"]


def test_risk_list_flags_athletes_under_the_floor(con):
    _semester(con)
    settings.set(con, "attendance_min_pct", 99)
    df = logic.attendance_risk(con, u(con, "coach.cricket"), TODAY, only_at_risk=True)
    assert set(df["USN"]) == {"1RV25CS012", "1RV24CS110"}
    assert (df["Status"] == "Below floor").all()
    assert (df["With all letters approved (%)"] > df["Projected (%)"]).all()
    assert (df["What to do"] == "Get the pending exemption letters approved").all()

    settings.set(con, "attendance_min_pct", 50)
    assert logic.attendance_risk(con, u(con, "coach.cricket"), TODAY, margin=5, only_at_risk=True).empty


def test_risk_list_respects_scope(con):
    _semester(con)
    df = logic.attendance_risk(con, u(con, "1RV25CS012"), TODAY)
    assert list(df["USN"]) == ["1RV25CS012"]
    ece = logic.attendance_risk(con, u(con, "proctor.cse"), TODAY)
    assert not ece["Dept"].ne("CSE").any()
