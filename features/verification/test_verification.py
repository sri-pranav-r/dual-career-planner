"""Run from the planner folder:  python3 -m pytest -q features/verification"""
import io
from datetime import date, timedelta

import pytest

import auth
import data
import features
import hooks
from features.letters import logic as letters
from features.verification import logic

TODAY = date(2026, 9, 28)


@pytest.fixture
def con():
    c = data.connect(":memory:")
    data.seed(c, today=TODAY)
    yield c
    c.close()


def user(con, username):
    return auth.user_by_username(con, username)


def cricket(con):
    """(tournament_id, first CSE athlete entered) for the seeded cricket tournament that clashes with CIE-1."""
    tid = con.execute("SELECT id FROM tournaments WHERE sport='Cricket' ORDER BY id").fetchone()[0]
    aid = con.execute("""SELECT a.id FROM entries e JOIN athletes a ON a.id=e.athlete_id
                         WHERE e.tournament_id=? AND a.dept='CSE' ORDER BY a.id""", (tid,)).fetchone()[0]
    return tid, aid


def drafted_and_ped_signed(con):
    tid, aid = cricket(con)
    lid = letters.create_letter(con, aid, tid)
    letters.advance_as(con, user(con, "ped"), lid)
    return tid, aid, lid


# ----------------------------------------------------------------- tokens

def test_token_round_trip(con):
    tid, aid = cricket(con)
    p = logic.parse_token(con, logic.make_token(con, aid, tid))
    assert (p["athlete_id"], p["tournament_id"]) == (aid, tid)
    assert (p["first"], p["last"]) == logic.away_window(data.tournament(con, tid))


def test_tampered_or_made_up_tokens_fail(con):
    tid, aid = cricket(con)
    tok = logic.make_token(con, aid, tid)
    body, sig = tok.rsplit(".", 1)
    other = f"{aid + 1}-" + body.split("-", 1)[1]
    for bad in (f"{other}.{sig}", body + ".AAAAAAAAAAAAAAAA", "", "hello", tok + "x"):
        assert logic.parse_token(con, bad) is None
        assert not logic.verify_token(con, bad).valid


def test_token_from_another_database_fails(con):
    tid, aid = cricket(con)
    tok = logic.make_token(con, aid, tid)
    other = data.connect(":memory:")
    data.seed(other, today=TODAY)
    assert logic.parse_token(other, tok) is None


def test_verify_url_uses_base(monkeypatch):
    monkeypatch.setenv("PLANNER_BASE_URL", "https://planner.example/")
    assert logic.verify_url("1-2-x.y") == "https://planner.example/?verify=1-2-x.y"


# ----------------------------------------------------------------- verification page data

def test_verify_shows_live_ped_data(con):
    tid, aid, _ = drafted_and_ped_signed(con)
    v = logic.verify_token(con, logic.make_token(con, aid, tid))
    assert v.valid and v.entered and v.ped_confirmed and not v.dates_changed
    assert v.approvable and v.warnings == []
    assert any(c.kind == "CIE" for c in v.missed)
    assert v.status == "PED signed"


def test_verify_flags_moved_dates_and_removed_entry(con):
    tid, aid, _ = drafted_and_ped_signed(con)
    tok = logic.make_token(con, aid, tid)
    t = data.tournament(con, tid)
    data.update_tournament(con, tid, end_date=logic._d(t["end_date"]) + timedelta(days=2))
    v = logic.verify_token(con, tok)
    assert v.valid and v.dates_changed and not v.approvable
    assert any("dates changed" in w for w in v.warnings)

    others = [a for a in data.entries(con, tid) if a != aid]
    data.set_entries(con, tid, others)
    v = logic.verify_token(con, logic.make_token(con, aid, tid))
    assert not v.entered and not v.approvable


def test_verify_before_ped_signs_warns(con):
    tid, aid = cricket(con)
    letters.create_letter(con, aid, tid)
    v = logic.verify_token(con, logic.make_token(con, aid, tid))
    assert v.valid and not v.ped_confirmed
    assert any("Physical Education Director" in w for w in v.warnings)


# ----------------------------------------------------------------- approve / reject

def test_proctor_then_hod_approve(con):
    tid, aid, lid = drafted_and_ped_signed(con)
    seen = []
    hooks.on("verification_decided", lambda c, p: seen.append(p))

    assert logic.record_decision(con, user(con, "proctor.cse"), aid, tid, "approved") == "Proctor signed"
    assert logic.record_decision(con, user(con, "hod.cse"), aid, tid, "approved") == "HoD approved"
    assert letters.get_letter(con, lid)["stage"] == "hod_approved"
    assert [p["decision"] for p in seen] == ["approved", "approved"]
    inbox = [n["message"] for n in letters.notifications(con, aid)]
    assert any("approved your exemption letter" in m for m in inbox)
    assert len(logic.decisions(con, aid, tid)) == 2


def test_out_of_order_approvals_are_refused(con):
    tid, aid = cricket(con)
    letters.create_letter(con, aid, tid)
    with pytest.raises(ValueError, match="PED's signature"):
        logic.record_decision(con, user(con, "proctor.cse"), aid, tid, "approved")
    letters.advance_as(con, user(con, "ped"), letters.find_letter(con, aid, tid)["id"])
    with pytest.raises(ValueError, match="proctor's signature"):
        logic.record_decision(con, user(con, "hod.cse"), aid, tid, "approved")
    logic.record_decision(con, user(con, "proctor.cse"), aid, tid, "approved")
    with pytest.raises(ValueError, match="Already past"):
        logic.record_decision(con, user(con, "proctor.cse"), aid, tid, "approved")


def test_reject_needs_reason_and_athlete_must_redraft(con):
    tid, aid, lid = drafted_and_ped_signed(con)
    with pytest.raises(ValueError, match="reason"):
        logic.record_decision(con, user(con, "proctor.cse"), aid, tid, "rejected", "  ")
    status = logic.record_decision(con, user(con, "proctor.cse"), aid, tid, "rejected", "Attach the selection letter")
    assert status == "Rejected"
    assert letters.get_letter(con, lid)["rejected"]
    assert any("Attach the selection letter" in n["message"] for n in letters.notifications(con, aid))
    assert logic.decisions_table(con, [aid]).iloc[0]["Decision"] == "rejected"
    v = logic.verify_token(con, logic.make_token(con, aid, tid))
    assert not v.approvable and any("rejected" in w for w in v.warnings)
    with pytest.raises(ValueError, match="redraft"):
        logic.record_decision(con, user(con, "proctor.cse"), aid, tid, "approved")
    # Athlete redrafts: signatures start again from the PED
    letters.create_letter(con, aid, tid)
    assert logic.letter_status(con, aid, tid) == "Drafted"


def test_only_faculty_in_scope_can_decide(con):
    tid, aid, _ = drafted_and_ped_signed(con)
    for name in ("coach.cricket", "ped"):
        with pytest.raises(PermissionError):
            logic.record_decision(con, user(con, name), aid, tid, "approved")
    usn = con.execute("SELECT usn FROM athletes WHERE id=?", (aid,)).fetchone()[0]
    with pytest.raises(PermissionError):
        logic.record_decision(con, user(con, usn), aid, tid, "approved")
    auth.create_user(con, "proctor.ece", "Prof. Anil K", "faculty", "proctor", title="proctor", dept="ECE")
    with pytest.raises(PermissionError, match="outside your department"):
        logic.record_decision(con, user(con, "proctor.ece"), aid, tid, "approved")
    auth.create_user(con, "teacher.cse", "Ms. Rao", "faculty", "teacher1", title="teacher", dept="CSE")
    assert logic.record_decision(con, user(con, "teacher.cse"), aid, tid, "approved") == "Proctor signed"


def test_letter_needing_redo_cannot_be_approved(con):
    tid, aid, _ = drafted_and_ped_signed(con)
    t = data.tournament(con, tid)
    data.update_tournament(con, tid, start_date=logic._d(t["start_date"]) - timedelta(days=1))
    with pytest.raises(ValueError, match="fresh letter"):
        logic.record_decision(con, user(con, "proctor.cse"), aid, tid, "approved")


def test_no_letter_drafted(con):
    tid, aid = cricket(con)
    with pytest.raises(ValueError, match="not drafted"):
        logic.record_decision(con, user(con, "proctor.cse"), aid, tid, "approved")
    assert logic.letter_status(con, aid, tid) == "Not drafted"


def test_athlete_deleted_removes_their_decisions(con):
    tid, aid, _ = drafted_and_ped_signed(con)
    logic.record_decision(con, user(con, "proctor.cse"), aid, tid, "approved")
    other = next(a for a in data.entries(con, tid) if a != aid)
    con.execute("INSERT INTO verification_decisions(athlete_id,tournament_id,decision,decided_at) VALUES (?,?,?,?)",
                (other, tid, "approved", "2026-09-27T10:00:00"))
    features.register_hooks()
    if hasattr(data, "delete_athlete"):
        data.delete_athlete(con, aid)
    else:
        hooks.emit(con, "athlete_deleted", {"athlete_id": aid})
    assert not hooks.last_errors
    assert logic.decisions(con, aid, tid) == []
    assert len(logic.decisions(con, other, tid)) == 1


# ----------------------------------------------------------------- department view

def test_upcoming_absences_scoped_to_department(con):
    tid, aid = cricket(con)
    hod = user(con, "hod.cse")
    ids = auth.visible_athlete_ids(con, hod)
    df = logic.upcoming_absences(con, ids, TODAY, 60)
    assert not df.empty
    assert set(df["athlete_id"]) <= set(ids)
    row = df[(df["athlete_id"] == aid) & (df["tournament_id"] == tid)].iloc[0]
    assert "CIE-1" in row["Tests missed"] and row["Class days"] > 0
    plan = logic.makeup_plan(con, ids, TODAY, 60)
    assert (plan["Type"] == "CIE").any()
    assert plan["Students"].min() >= 1


def test_upcoming_absences_respects_horizon(con):
    ids = auth.visible_athlete_ids(con, user(con, "admin"))
    assert logic.upcoming_absences(con, ids, TODAY, 60)["Away from"].max() <= TODAY + timedelta(days=60)
    assert logic.upcoming_absences(con, [], TODAY).empty


# ----------------------------------------------------------------- letter QR

def test_letter_gets_qr_and_link(con):
    from docx import Document
    import core
    tid, aid = cricket(con)
    a, t = data.athlete(con, aid), data.tournament(con, tid)
    t = {**t, "start_date": logic._d(t["start_date"]), "end_date": logic._d(t["end_date"])}
    out = logic.letter_with_qr(con, core.build_letter_docx(a, t, logic.missed_items(con, aid, t)), aid, tid,
                               base_url="https://planner.example")
    doc = Document(io.BytesIO(out))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "https://planner.example/?verify=" in text
    tok = text.split("?verify=")[1].split()[0]
    assert logic.verify_token(con, tok).valid
    if logic.qr_png("x"):
        assert len(doc.inline_shapes) == 1


# ----------------------------------------------------------------- wiring

def test_feature_loads():
    loaded = {f["name"]: f for f in features.load_all(force=True)}
    assert "verification" in loaded, features.load_errors.get("verification")
    f = loaded["verification"]
    assert callable(f["public_routes"]["verify"])
    assert {t["label"] for t in f["tabs"]} == {"Letter approvals", "Upcoming absences"}
    assert f["hooks"]["athlete_deleted"] is logic.on_athlete_deleted
