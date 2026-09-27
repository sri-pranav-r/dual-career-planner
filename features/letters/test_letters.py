"""Run from the planner folder:  python3 -m pytest -q features/letters"""
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import data
import hooks
from features.letters import logic

SCHEMA = Path(__file__).with_name("schema.sql").read_text()


@pytest.fixture
def con():
    c = data.connect(":memory:")
    c.executescript(SCHEMA)          # core also runs this on connect; IF NOT EXISTS makes it harmless
    data.seed(c, today=date(2026, 9, 28))
    hooks.clear()
    c.execute("DELETE FROM letters_notifications")   # seeding may have sent "you were entered" notices
    c.commit()
    yield c
    hooks.clear()
    c.close()


def _entered_pair(con):
    """First tournament with at least two entered athletes: (tid, [aid, aid, ...])."""
    for tid, in con.execute("SELECT id FROM tournaments ORDER BY id"):
        aids = [r[0] for r in con.execute("SELECT athlete_id FROM entries WHERE tournament_id=? ORDER BY athlete_id", (tid,))]
        if len(aids) >= 2:
            return tid, aids
    raise AssertionError("seed has no tournament with two entrants")


def _t(con, tid):
    return dict(zip(["name", "venue", "start_date", "end_date", "travel_before", "travel_after"],
                    con.execute("SELECT name, venue, start_date, end_date, travel_before, travel_after "
                                "FROM tournaments WHERE id=?", (tid,)).fetchone()))


# ----------------------------------------------------------------- tracker

def test_letter_walks_all_stages_in_order(con):
    tid, (a, _, *_) = _entered_pair(con)
    lid = logic.create_letter(con, a, tid)
    assert logic.get_letter(con, lid)["stage"] == "drafted"
    seen = [logic.advance_letter(con, lid, by="x") for _ in range(4)]
    assert seen == ["ped_signed", "proctor_signed", "hod_approved", "submitted"]
    with pytest.raises(logic.LetterError):
        logic.advance_letter(con, lid)
    assert [h["stage"] for h in logic.letter_history(con, lid)] == logic.STAGES
    assert logic.progress("submitted") == 1.0 and logic.progress(None) == 0.0


def test_create_letter_is_idempotent(con):
    tid, (a, *_) = _entered_pair(con)
    assert logic.create_letter(con, a, tid) == logic.create_letter(con, a, tid)
    assert con.execute("SELECT COUNT(*) FROM letters_status").fetchone()[0] == 1


def test_undo_steps_back(con):
    tid, (a, *_) = _entered_pair(con)
    lid = logic.create_letter(con, a, tid)
    logic.advance_letter(con, lid)
    assert logic.undo_last_step(con, lid) == "drafted"
    with pytest.raises(logic.LetterError):
        logic.undo_last_step(con, lid)


def test_letters_for_athlete_lists_undrafted_tournaments(con):
    tid, (a, *_) = _entered_pair(con)
    rows = logic.letters_for_athlete(con, a)
    assert any(r["tournament_id"] == tid and r["stage"] is None for r in rows)
    logic.create_letter(con, a, tid)
    rows = logic.letters_for_athlete(con, a)
    assert next(r for r in rows if r["tournament_id"] == tid)["stage"] == "drafted"


def test_permissions_per_stage():
    ath = SimpleNamespace(role="athlete", athlete_id=7, name="A")
    other = SimpleNamespace(role="athlete", athlete_id=8, name="B")
    ped = SimpleNamespace(role="coach", sport="", name="PED")
    coach = SimpleNamespace(role="coach", sport="Cricket", name="C")
    proctor = SimpleNamespace(role="faculty", title="proctor", name="P")
    hod = SimpleNamespace(role="faculty", title="hod", name="H")
    admin = SimpleNamespace(role="admin", name="Adm")
    assert logic.can_advance_to(ped, "ped_signed", 7) and not logic.can_advance_to(coach, "ped_signed", 7)
    assert logic.can_advance_to(proctor, "proctor_signed", 7) and not logic.can_advance_to(hod, "proctor_signed", 7)
    assert logic.can_advance_to(hod, "hod_approved", 7) and not logic.can_advance_to(proctor, "hod_approved", 7)
    assert logic.can_advance_to(ath, "submitted", 7) and not logic.can_advance_to(other, "submitted", 7)
    assert not logic.can_advance_to(ath, "ped_signed", 7)
    assert all(logic.can_advance_to(admin, s, 7) for s in logic.STAGES)


def test_advance_as_blocks_wrong_person(con):
    tid, (a, *_) = _entered_pair(con)
    lid = logic.create_letter(con, a, tid)
    athlete = SimpleNamespace(role="athlete", athlete_id=a, name="self")
    with pytest.raises(PermissionError):
        logic.advance_as(con, athlete, lid)       # athletes can't sign as PED
    ped = SimpleNamespace(role="coach", sport=None, name="PED")
    assert logic.advance_as(con, ped, lid) == "ped_signed"


# ----------------------------------------------------------------- alerts

def test_date_change_notifies_every_entrant_and_flags_letters(con):
    tid, aids = _entered_pair(con)
    before = _t(con, tid)
    lid = logic.create_letter(con, aids[0], tid)
    logic.advance_letter(con, lid)
    new_start = (date.fromisoformat(before["start_date"]) + timedelta(days=3)).isoformat()
    new_end = (date.fromisoformat(before["end_date"]) + timedelta(days=3)).isoformat()
    after = {**before, "start_date": new_start, "end_date": new_end}
    res = logic.on_tournament_updated(con, {"tournament_id": tid, "before": before, "after": after,
                                            "changed": ["start_date", "end_date"]})
    assert sorted(res["notified"]) == sorted(aids)
    assert res["letters_flagged"] == [lid]
    for a in aids:
        assert logic.unread_count(con, a) == 1
    with_letter = logic.notifications(con, aids[0])[0]["message"]
    without = logic.notifications(con, aids[1])[0]["message"]
    assert "old dates" in with_letter and "old dates" not in without
    assert before["name"] in without and "→" in without
    # the flagged letter can't collect signatures until redrafted, and redrafting restarts it
    with pytest.raises(logic.LetterError):
        logic.advance_letter(con, lid)
    assert logic.create_letter(con, aids[0], tid) == lid
    letter = logic.get_letter(con, lid)
    assert letter["stage"] == "drafted" and letter["needs_redo"] == 0


def test_venue_change_notifies_but_keeps_letters(con):
    tid, aids = _entered_pair(con)
    before = _t(con, tid)
    lid = logic.create_letter(con, aids[0], tid)
    res = logic.on_tournament_updated(con, {"tournament_id": tid, "before": before,
                                            "after": {**before, "venue": "Somewhere new"}, "changed": ["venue"]})
    assert res["letters_flagged"] == [] and logic.get_letter(con, lid)["needs_redo"] == 0
    assert "Somewhere new" in logic.notifications(con, aids[1])[0]["message"]


def test_no_real_change_sends_nothing(con):
    tid, aids = _entered_pair(con)
    before = _t(con, tid)
    after = {**before, "start_date": date.fromisoformat(before["start_date"])}  # same day, different type
    res = logic.on_tournament_updated(con, {"tournament_id": tid, "before": before, "after": after,
                                            "changed": ["start_date"]})
    assert res["notified"] == [] and logic.unread_count(con, aids[0]) == 0


def test_entries_changed_tells_added_and_removed_once(con):
    tid, aids = _entered_pair(con)
    logic.on_tournament_created(con, {"tournament_id": tid})
    logic.on_entries_changed(con, {"tournament_id": tid, "added": [aids[0]], "removed": []})  # duplicate event
    assert logic.unread_count(con, aids[0]) == 1
    logic.on_entries_changed(con, {"tournament_id": tid, "added": [], "removed": [aids[1]]})
    assert logic.notifications(con, aids[1])[0]["kind"] == "removed_from_tournament"
    logic.on_entries_changed(con, {"tournament_id": tid, "added": [aids[1]], "removed": []})  # re-entered
    assert logic.notifications(con, aids[1])[0]["kind"] == "entered_in_tournament"


def test_mark_read(con):
    tid, (a, *_) = _entered_pair(con)
    n1 = logic.notify(con, a, "one")
    logic.notify(con, a, "two")
    logic.mark_read(con, n1)
    assert logic.unread_count(con, a) == 1
    assert logic.mark_all_read(con, a) == 1 and logic.unread_count(con, a) == 0


def test_core_update_tournament_reaches_athletes_through_hooks(con):
    """End to end through core: data.update_tournament -> tournament_updated -> inbox."""
    if not hasattr(data, "update_tournament"):
        pytest.skip("core data.update_tournament not available yet")
    hooks.on("tournament_updated", logic.on_tournament_updated)
    tid, aids = _entered_pair(con)
    start = date.fromisoformat(_t(con, tid)["start_date"]) + timedelta(days=1)
    end = date.fromisoformat(_t(con, tid)["end_date"]) + timedelta(days=1)
    data.update_tournament(con, tid, start_date=start, end_date=end)
    assert not hooks.last_errors, hooks.last_errors
    for a in aids:
        assert logic.unread_count(con, a) == 1


def test_new_tournament_via_core_tells_each_entrant_once(con):
    """add_tournament emits tournament_created and entries_changed; athletes get one message."""
    import features
    features.register_hooks()
    aids = [r[0] for r in con.execute("SELECT id FROM athletes ORDER BY id LIMIT 3")]
    tid = data.add_tournament(con, "Test Cup", "Cricket", "Mysuru", date(2026, 11, 2), date(2026, 11, 3), 1, 0, aids)
    assert not hooks.last_errors, hooks.last_errors
    for a in aids:
        msgs = logic.notifications(con, a)
        assert len(msgs) == 1 and msgs[0]["tournament_id"] == tid and "Test Cup" in msgs[0]["message"]


def test_reject_notifies_athlete_and_redraft_restarts(con):
    tid, (a, *_) = _entered_pair(con)
    lid = logic.create_letter(con, a, tid)
    ped = SimpleNamespace(role="coach", sport=None, name="PED")
    hod = SimpleNamespace(role="faculty", title="hod", name="HoD")
    with pytest.raises(PermissionError):
        logic.reject_as(con, hod, lid, "wrong dates")      # not HoD's turn yet
    with pytest.raises(logic.LetterError):
        logic.reject_as(con, ped, lid, "  ")               # reason required
    logic.reject_as(con, ped, lid, "Selection letter missing.")
    assert logic.status_label(logic.get_letter(con, lid)) == "Rejected"
    n = logic.notifications(con, a)[0]
    assert n["kind"] == "letter_rejected" and "Selection letter missing." in n["message"]
    with pytest.raises(logic.LetterError):
        logic.advance_as(con, ped, lid)
    assert logic.create_letter(con, a, tid) == lid
    assert logic.status_label(logic.get_letter(con, lid)) == "Drafted"
    assert logic.advance_as(con, ped, lid) == "ped_signed"


def _letter_rows(con, aid):
    return (con.execute("SELECT COUNT(*) FROM letters_status WHERE athlete_id=?", (aid,)).fetchone()[0],
            con.execute("SELECT COUNT(*) FROM letters_history h JOIN letters_status l ON l.id=h.letter_id "
                        "WHERE l.athlete_id=?", (aid,)).fetchone()[0],
            con.execute("SELECT COUNT(*) FROM letters_notifications WHERE athlete_id=?", (aid,)).fetchone()[0])


def test_athlete_deleted_removes_only_their_rows(con):
    tid, (a, b, *_) = _entered_pair(con)
    for x in (a, b):
        lid = logic.create_letter(con, x, tid)
        logic.advance_letter(con, lid)
        logic.notify(con, x, "hello", tournament_id=tid)
    a_letter = logic.find_letter(con, a, tid)["id"]
    assert logic.on_athlete_deleted(con, {"athlete_id": a}) == 1
    assert _letter_rows(con, a) == (0, 0, 0)
    assert con.execute("SELECT COUNT(*) FROM letters_history WHERE letter_id=?", (a_letter,)).fetchone()[0] == 0
    assert _letter_rows(con, b) == (1, 2, 1)


def test_core_delete_athlete_clears_letters_through_hooks(con):
    if not hasattr(data, "delete_athlete"):
        pytest.skip("core data.delete_athlete not available yet")
    import features
    features.register_hooks()
    tid, (a, b, *_) = _entered_pair(con)
    logic.create_letter(con, a, tid)
    logic.notify(con, a, "hello")
    data.delete_athlete(con, a)
    assert not hooks.last_errors, hooks.last_errors
    assert _letter_rows(con, a) == (0, 0, 0)
