"""Run from the planner folder:  python3 -m pytest -q features/reminders"""
import json
from datetime import date, timedelta

import pytest

import data
import hooks
from features.reminders import logic, senders

TODAY = date(2026, 9, 28)   # a Monday


class FakeSender:
    live = True
    name = "fake"

    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    def send(self, channel, to, text):
        self.sent.append((channel, to, text))
        return self.ok, "fake-id" if self.ok else "boom"


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setattr(logic, "base_url", lambda con: "http://planner.test")
    c = data.connect(":memory:")
    data.seed(c, today=TODAY)
    c.execute("DELETE FROM letters_notifications")
    c.commit()
    yield c
    c.close()


def _silence(con, aid, days):
    """Remove the athlete's last `days` days of sessions."""
    con.execute("DELETE FROM sessions WHERE athlete_id=? AND date>?", (aid, (TODAY - timedelta(days=days)).isoformat()))
    con.commit()


def _tid(con, name_part):
    return con.execute("SELECT id FROM tournaments WHERE name LIKE ?", (f"%{name_part}%",)).fetchone()[0]


# ----------------------------------------------------------------- senders

@pytest.mark.parametrize("raw, want", [
    ("9876543210", "+919876543210"), ("098765 43210", "+919876543210"), ("+91 98765-43210", "+919876543210"),
    ("919876543210", "+919876543210"), ("+14155238886", "+14155238886"),
    ("98XXXXXXXX", None), ("", None), (None, None), ("12345", None), ("1234567890", None),
])
def test_normalise_phone(raw, want):
    assert senders.normalise_phone(raw) == want


def test_from_env_defaults_to_dry_run():
    assert not senders.from_env({}).live
    assert not senders.from_env({"PLANNER_REMINDER_PROVIDER": "twilio"}).live            # LIVE flag missing
    assert not senders.from_env({"PLANNER_REMINDERS_LIVE": "1", "PLANNER_REMINDER_PROVIDER": "twilio"}).live  # no creds
    assert not senders.from_env({"PLANNER_REMINDERS_LIVE": "1", "PLANNER_REMINDER_PROVIDER": "carrier-pigeon"}).live
    live = senders.from_env({"PLANNER_REMINDERS_LIVE": "1", "PLANNER_REMINDER_PROVIDER": "twilio",
                             "TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t"})
    assert live.live and isinstance(live, senders.TwilioSender)
    hook = senders.from_env({"PLANNER_REMINDERS_LIVE": "1", "PLANNER_REMINDER_PROVIDER": "webhook",
                             "PLANNER_REMINDER_WEBHOOK_URL": "https://x.test/send"})
    assert isinstance(hook, senders.WebhookSender)


def test_twilio_builds_whatsapp_request_without_network():
    seen = {}

    class Resp:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"sid": "SM123"}'

    def opener(req, timeout):
        seen["url"], seen["body"] = req.full_url, req.data.decode()
        return Resp()

    s = senders.TwilioSender("AC1", "tok", sms_from="+15550000000", whatsapp_from="+14155238886", opener=opener)
    assert s.send("whatsapp", "+919876543210", "hi") == (True, "SM123")
    assert "Accounts/AC1/Messages.json" in seen["url"]
    assert "To=whatsapp%3A%2B919876543210" in seen["body"] and "From=whatsapp%3A%2B14155238886" in seen["body"]
    assert senders.TwilioSender("AC1", "tok", opener=opener).send("sms", "+919876543210", "hi")[0] is False


# ----------------------------------------------------------------- tournament reminders

def test_tournament_reminder_counts_clashes_and_points_to_letter(con):
    tid = _tid(con, "Basketball")            # starts in 9 days in the seed
    aid = data.entries(con, tid)[0]
    data.add_event(con, aid, TODAY + timedelta(days=9), "CIE", "Maths CIE-1")
    logic.tournament_reminders(con, TODAY, senders.DryRunSender())
    msgs = [r for r in logic.outbox(con, [aid]) if r["kind"] == "tournament"]
    assert {r["channel"] for r in msgs} == {"in_app", "whatsapp"}
    text = msgs[0]["message"]
    assert "starts in 9 days" in text and "1 test and" in text and "classes" in text
    assert "no exemption letter yet" in text and "http://planner.test" in text
    # the athlete's in-app inbox has it too
    from features.letters import logic as letters
    assert any("Basketball" in n["message"] for n in letters.notifications(con, aid))


def test_reminder_steps_fire_once_each(con):
    tid = _tid(con, "Basketball")
    first, _ = logic.core.away_window(data.tournament(con, tid))
    n_entered = len(data.entries(con, tid))
    counts = []
    for back in (12, 10, 9, 5, 3, 2, 0, -1):
        day = first - timedelta(days=back)
        logic.tournament_reminders(con, day, senders.DryRunSender())
        counts.append(con.execute("SELECT COUNT(*) FROM reminders_outbox WHERE channel='in_app' AND kind='tournament' "
                                  "AND dedupe_key LIKE ?", (f"tournament:{tid}:%",)).fetchone()[0])
    # nothing at 12 days, first reminder at 10, second at 3, none after the start
    assert counts == [0, n_entered, n_entered, n_entered, 2 * n_entered, 2 * n_entered, 2 * n_entered, 2 * n_entered]


def test_moved_tournament_gets_fresh_reminder(con):
    tid = _tid(con, "Badminton")                # starts in 5 days
    logic.tournament_reminders(con, TODAY, senders.DryRunSender())
    before = con.execute("SELECT COUNT(*) FROM reminders_outbox").fetchone()[0]
    t = data.tournament(con, tid)
    data.update_tournament(con, tid, start_date=t["start_date"] + timedelta(days=1), end_date=t["end_date"] + timedelta(days=1))
    logic.tournament_reminders(con, TODAY, senders.DryRunSender())
    assert con.execute("SELECT COUNT(*) FROM reminders_outbox").fetchone()[0] > before


def test_letter_status_changes_message(con):
    from features.letters import logic as letters
    tid = _tid(con, "Basketball")
    aid = data.entries(con, tid)[0]
    a, t = data.athlete(con, aid), data.tournament(con, tid)
    lid = letters.create_letter(con, aid, tid)
    assert "Drafted" in logic.tournament_message(con, a, t, TODAY)
    for _ in range(3):
        letters.advance_letter(con, lid)
    assert "approved" in logic.tournament_message(con, a, t, TODAY)


def test_no_clash_message(con):
    tid = _tid(con, "Badminton")
    aid = data.entries(con, tid)[0]
    con.execute("DELETE FROM timetable WHERE athlete_id=?", (aid,))
    con.execute("DELETE FROM events WHERE athlete_id=?", (aid,))
    text = logic.tournament_message(con, data.athlete(con, aid), data.tournament(con, tid), TODAY)
    assert "No classes or tests clash" in text and "letter" not in text


# ----------------------------------------------------------------- delivery rules

def test_live_sender_needs_consent_and_valid_phone(con):
    tid = _tid(con, "Badminton")
    a1, a2 = data.entries(con, tid)
    con.execute("UPDATE athletes SET phone='9876543210' WHERE id IN (?,?)", (a1, a2))
    logic.set_prefs(con, a1, True, "sms")
    fake = FakeSender()
    logic.tournament_reminders(con, TODAY, fake)
    assert [(c, to) for c, to, _ in fake.sent] == [("sms", "+919876543210")]
    status = {r["athlete_id"]: r["status"] for r in logic.outbox(con, [a1, a2]) if r["channel"] != "in_app"}
    assert status == {a1: "sent", a2: "skipped"}


def test_failed_send_is_recorded(con):
    tid = _tid(con, "Badminton")
    aid = data.entries(con, tid)[0]
    con.execute("UPDATE athletes SET phone='9876543210' WHERE id=?", (aid,))
    logic.set_prefs(con, aid, True, "whatsapp")
    logic.tournament_reminders(con, TODAY, FakeSender(ok=False))
    row = [r for r in logic.outbox(con, [aid]) if r["channel"] == "whatsapp"][0]
    assert row["status"] == "failed" and row["detail"] == "boom"


def test_dry_run_explains_what_live_would_skip(con):
    logic.tournament_reminders(con, TODAY, senders.DryRunSender())
    row = [r for r in logic.outbox(con) if r["channel"] == "whatsapp"][0]
    assert row["status"] == "dry_run"
    assert "no valid phone number" in row["detail"] and "not opted in" in row["detail"]


def test_prefs_default_and_validation(con):
    assert logic.prefs(con, 1) == {"external_ok": False, "channel": "whatsapp"}
    logic.set_prefs(con, 1, True, "sms")
    assert logic.prefs(con, 1) == {"external_ok": True, "channel": "sms"}
    with pytest.raises(ValueError):
        logic.set_prefs(con, 1, True, "pager")


# ----------------------------------------------------------------- nudges

def test_nudge_after_three_silent_days_then_weekly(con):
    aid = 3
    _silence(con, aid, 2)                       # last log 2 days ago: fine
    assert aid not in [s["athlete"]["id"] for s in logic.silent_athletes(con, TODAY)]
    _silence(con, aid, 3)                       # 3 days: nudge
    assert logic.logging_nudges(con, TODAY, senders.DryRunSender()) >= 1
    text = [r for r in logic.outbox(con, [aid]) if r["kind"] == "nudge"][0]["message"]
    assert "(3 days)" in text
    # the next few days stay quiet, a week later it nudges again
    nudges = lambda: con.execute("SELECT COUNT(*) FROM reminders_outbox WHERE athlete_id=? AND kind='nudge' "
                                 "AND channel='in_app'", (aid,)).fetchone()[0]
    for d in range(1, 7):
        logic.logging_nudges(con, TODAY + timedelta(days=d), senders.DryRunSender())
    assert nudges() == 1
    logic.logging_nudges(con, TODAY + timedelta(days=7), senders.DryRunSender())
    assert nudges() == 2


def test_never_logged_athlete_is_nudged(con):
    con.execute("DELETE FROM sessions WHERE athlete_id=2")
    s = {x["athlete"]["id"]: x for x in logic.silent_athletes(con, TODAY)}
    assert s[2]["last"] is None
    logic.logging_nudges(con, TODAY, senders.DryRunSender())
    assert "haven't logged any training yet" in [r for r in logic.outbox(con, [2]) if r["kind"] == "nudge"][0]["message"]


# ----------------------------------------------------------------- team report

def test_team_report_flags_spike_and_silence(con):
    rep = logic.team_report(con, "Cricket", TODAY)
    top = rep["rows"][0]
    assert top["name"] == "Aarav Kulkarni" and top["spiked"] and top["acwr"] > 1.5   # the seed's overloaded athlete
    _silence(con, 3, 4)
    foot = logic.team_report(con, "Football", TODAY)
    quiet = [r for r in foot["rows"] if r["athlete_id"] == 3][0]
    assert quiet["not_logging"] and quiet["days_silent"] == 4
    assert "Not logging: Rohan Hegde (4 days)" in foot["summary"]


def test_spike_fallback_without_28_days(con):
    aid = 1
    con.execute("DELETE FROM sessions WHERE athlete_id=?", (aid,))
    for back in range(6, -1, -1):              # one week only: no baseline, no ratio, no flag
        data.add_session(con, aid, TODAY - timedelta(days=back), 120, 9, "Skills")
    row = logic.athlete_week(con, data.athlete(con, aid), TODAY)
    assert row["acwr"] is None and row["spiked"] is False
    for back in range(20, 6, -1):              # two easy weeks before the hard one
        data.add_session(con, aid, TODAY - timedelta(days=back), 60, 4, "Skills")
    row = logic.athlete_week(con, data.athlete(con, aid), TODAY)
    assert row["acwr"] is None and row["avg_week_before"] == 7 * 240 and row["spiked"] is True


def test_report_respects_visible_athletes(con):
    rep = logic.team_report(con, "Cricket", TODAY, athlete_ids=[1])
    assert [r["athlete_id"] for r in rep["rows"]] == [1]


def test_save_report_upserts(con):
    logic.save_report(con, logic.team_report(con, "Cricket", TODAY))
    logic.save_report(con, logic.team_report(con, "Cricket", TODAY))
    saved = logic.saved_reports(con, ["Cricket"])
    assert len(saved) == 1 and saved[0]["rows"]


# ----------------------------------------------------------------- running

def test_run_all_is_idempotent_and_reports_on_monday(con):
    first = logic.run_all(con, TODAY, senders.DryRunSender())
    assert first["tournament"] > 0 and first["reports"]            # TODAY is a Monday
    again = logic.run_all(con, TODAY, senders.DryRunSender())
    assert again["tournament"] == 0 and again["nudges"] == 0
    tue = logic.run_all(con, TODAY + timedelta(days=1), senders.DryRunSender())
    assert tue["reports"] == []


def test_run_if_due_once_per_day(con):
    assert logic.run_if_due(con, TODAY, senders.DryRunSender()) is not None
    assert logic.run_if_due(con, TODAY, senders.DryRunSender()) is None
    assert logic.last_run(con)["day"] == TODAY.isoformat()


def test_cli_runs_against_a_db_file(tmp_path, monkeypatch, capsys):
    from features.reminders import run
    db = tmp_path / "p.db"
    c = data.connect(str(db))
    data.seed(c, today=TODAY)
    c.close()
    monkeypatch.delenv("PLANNER_REMINDERS_LIVE", raising=False)
    out = run.main(["--db", str(db), "--today", TODAY.isoformat()])
    assert out["tournament"] > 0 and "Dry run" in out["sender"]
    assert run.main(["--db", str(db), "--today", TODAY.isoformat()]) is None
    assert "Already ran today" in capsys.readouterr().out


# ----------------------------------------------------------------- hooks

def test_athlete_deleted_clears_rows(con):
    _silence(con, 1, 5)                         # so athlete 1 (cricket) gets a nudge
    logic.set_prefs(con, 1, True)
    logic.run_all(con, TODAY, senders.DryRunSender(), report=True)
    assert con.execute("SELECT COUNT(*) FROM reminders_outbox WHERE athlete_id=1").fetchone()[0]
    logic.on_athlete_deleted(con, {"athlete_id": 1})
    assert con.execute("SELECT COUNT(*) FROM reminders_outbox WHERE athlete_id=1").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM reminders_prefs WHERE athlete_id=1").fetchone()[0] == 0
    cricket = logic.saved_reports(con, ["Cricket"])[0]
    assert all(r["athlete_id"] != 1 for r in cricket["rows"]) and "Aarav" not in cricket["summary"]


def test_feature_registers_the_hook():
    import features
    f = [x for x in features.load_all(force=True) if x["name"] == "reminders"][0]
    assert "athlete_deleted" in f["hooks"]
    assert {t["label"] for t in f["tabs"]} == {"Reminders", "Team load report", "Reminder settings"}
