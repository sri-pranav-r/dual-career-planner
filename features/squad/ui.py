"""Streamlit tabs: Make-up tests, Squad selection, Bulk letters, Attendance risk."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import auth
import data
import settings

from . import logic

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
STATUS_ICON = {"requested": "⏳", "approved": "📅", "rejected": "❌", "completed": "✅", "withdrawn": "▫️"}


def _history(con, rid):
    h = logic.makeup_history(con, rid)
    if h:
        df = pd.DataFrame(h)
        df["status"] = df["status"].map(logic.MAKEUP_LABELS)
        st.dataframe(df.rename(columns={"status": "Status", "changed_at": "When", "changed_by": "By", "note": "Note"}),
                     hide_index=True, width="stretch")


def _run(fn, *args, ok: str | None = None):
    """Call a logic function, show its error message instead of a traceback, rerun on success."""
    try:
        fn(*args)
    except (ValueError, PermissionError) as err:
        st.error(str(err))
        return
    if ok:
        st.toast(ok)
    st.rerun()


def _visible_tournaments(con, user) -> pd.DataFrame:
    tours = data.all_tournaments(con)
    if user.role == "coach" and user.sport and not tours.empty:
        tours = tours[tours["sport"].str.lower() == user.sport.lower()]
    return tours


# ----------------------------------------------------------------- tab: Make-up tests

def render_makeups(con, user):
    if user.role == "athlete":
        _athlete_makeups(con, user)
    else:
        _staff_makeups(con, user)


def _athlete_makeups(con, user):
    aid = user.athlete_id
    st.subheader("Make-up tests")
    st.caption("Every CIE or SEE a tournament makes you miss. Request a make-up for each one; your proctor, "
               "class teacher or HoD schedules it and you get a notification.")
    tests = logic.missed_tests(con, aid)
    if not tests:
        st.success("None of your tournaments clash with a CIE or SEE.")
        return
    for t in tests:
        req = t["request"]
        with st.container(border=True):
            left, right = st.columns([3, 1])
            left.markdown(f"**{t['title']}** ({t['kind']}) · {t['test_date']:%a %d %b} · missed for {t['tournament']}")
            if req is None or req["status"] in ("rejected", "withdrawn"):
                if req is not None:
                    left.caption(f"{STATUS_ICON[req['status']]} {logic.MAKEUP_LABELS[req['status']]}. "
                                 "You can ask again.")
                reason = left.text_input("Note for the faculty (optional)", key=f"mk_note_{t['tournament_id']}_{t['title']}")
                if right.button("Request make-up", key=f"mk_req_{t['tournament_id']}_{t['title']}", type="primary"):
                    _run(logic.request_makeup, con, user, aid, t["tournament_id"], t["test_date"], t["kind"],
                         t["title"], reason, ok="Make-up requested")
                if req is not None:
                    with left.expander("History"):
                        _history(con, req["id"])
                continue
            label = logic.MAKEUP_LABELS[req["status"]]
            if req["status"] == "approved":
                left.success(f"📅 Make-up on **{logic._d(req['makeup_date']):%a %d %b %Y}**"
                             + (f". {req['makeup_note']}" if req["makeup_note"] else ""))
            else:
                left.caption(f"{STATUS_ICON[req['status']]} {label}"
                             + (". Waiting for your proctor, class teacher or HoD." if req["status"] == "requested" else ""))
            right.download_button("⬇️ Request (.docx)", logic.makeup_request_docx(con, req["id"]),
                                  file_name=f"makeup_{req['title'].replace(' ', '_')}.docx", mime=DOCX,
                                  key=f"mk_dl_{req['id']}")
            if req["status"] in logic.OPEN and right.button("Withdraw", key=f"mk_wd_{req['id']}"):
                _run(logic.withdraw_makeup, con, user, req["id"], ok="Withdrawn")
            with left.expander("History"):
                _history(con, req["id"])


def _staff_makeups(con, user):
    st.subheader("Make-up test requests")
    ids = auth.visible_athlete_ids(con, user)
    decider = user.role == "admin" or (user.role == "faculty" and (user.title or "") in logic.DECIDER_TITLES)
    st.caption("Schedule a make-up for each test a student missed for sport, or reject it with a reason. "
               "The student is notified either way." if decider else
               "Make-up tests your athletes asked for. Faculty schedule them; you can see where each one is.")
    show = st.radio("Show", ["Open", "All"], horizontal=True, key="mk_show")
    df = logic.requests_table(con, ids, statuses=logic.OPEN if show == "Open" else None)
    if df.empty:
        st.info("No open make-up requests." if show == "Open" else "No make-up requests yet.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Waiting to be scheduled", int((df["Status"] == "Requested").sum()))
    c2.metric("Scheduled", int((df["Status"] == "Scheduled").sum()))
    c3.metric("Students", df["athlete_id"].nunique())
    st.download_button("⬇️ Download as CSV", df.drop(columns=["id", "athlete_id", "tournament_id"]).to_csv(index=False),
                       file_name="makeup_requests.csv", mime="text/csv", key="mk_csv")
    if not decider:
        st.dataframe(df.drop(columns=["id", "athlete_id", "tournament_id"]), hide_index=True, width="stretch")
        return
    for r in df.to_dict("records"):
        rid, test_day = r["id"], r["Test date"]
        with st.container(border=True):
            left, right = st.columns([3, 1])
            left.markdown(f"**{r['Athlete']}** · {r['USN']} · {r['Dept']} {r['Sem']} · **{r['Test']}** ({r['Type']}) "
                          f"on {test_day:%a %d %b}")
            when_txt = f" for {r['Make-up date']:%d %b}" if r["Status"] == "Scheduled" and r["Make-up date"] else ""
            left.caption(f"Away for {r['Tournament']} · exemption letter: {r['Exemption letter']} · "
                         f"status: {r['Status']}{when_txt}")
            if r["Status"] not in ("Requested", "Scheduled") or not logic.can_decide(con, user, r["athlete_id"]):
                continue
            with right.popover("Schedule" if r["Status"] == "Requested" else "Reschedule"):
                when = st.date_input("Make-up date", max(date.today(), test_day), min_value=test_day, key=f"mk_d_{rid}")
                note = st.text_input("Time / room (optional)", key=f"mk_n_{rid}")
                if st.button("Save", key=f"mk_ok_{rid}", type="primary"):
                    _run(logic.approve_makeup, con, user, rid, when, note, ok="Scheduled")
            if r["Status"] == "Scheduled" and right.button("Mark taken", key=f"mk_done_{rid}"):
                _run(logic.complete_makeup, con, user, rid, ok="Marked taken")
            with right.popover("Reject"):
                why = st.text_input("Reason", key=f"mk_why_{rid}")
                if st.button("Reject", key=f"mk_rej_{rid}"):
                    _run(logic.reject_makeup, con, user, rid, why, ok="Rejected")
            with left.expander("History"):
                _history(con, rid)


# ----------------------------------------------------------------- tab: Squad selection

def render_selection(con, user):
    if not auth.can(user, "manage_tournaments"):
        st.info("Only coaches and the sports office pick squads.")
        return
    st.subheader("Pick the squad")
    st.caption("See what each athlete would miss before you finalise entries. Cleanest picks are listed first; "
               "attendance is their worst subject after this trip if no letter is approved.")
    tours = _visible_tournaments(con, user)
    mode = st.radio("Tournament", ["Existing tournament", "New tournament"], horizontal=True, key="sel_mode")
    if mode == "Existing tournament":
        up = tours[tours["end_date"] >= date.today()] if not tours.empty else tours
        if up.empty:
            st.info("No upcoming tournaments. Choose New tournament to plan one.")
            return
        opts = {int(t["id"]): f"{t['name']} ({t['start_date']:%d %b} to {t['end_date']:%d %b})" for _, t in up.iterrows()}
        tid = st.selectbox("Tournament", list(opts), format_func=opts.get, key="sel_t")
        t = data.tournament(con, tid)
    else:
        tid = None
        c1, c2 = st.columns(2)
        name = c1.text_input("Name", key="sel_name")
        if user.role == "coach" and user.sport:
            sport = user.sport
            c2.text_input("Sport", sport, disabled=True, key="sel_sport_ro")
        else:
            sports = sorted(set(data.athletes(con)["sport"].dropna())) or ["Cricket"]
            sport = c2.selectbox("Sport", sports, key="sel_sport")
        venue = st.text_input("Venue", key="sel_venue")
        c1, c2, c3, c4 = st.columns(4)
        s = c1.date_input("Start", date.today() + timedelta(days=14), key="sel_s")
        e = c2.date_input("End", date.today() + timedelta(days=15), key="sel_e")
        tb = c3.number_input("Travel days before", 0, 5, 0, key="sel_tb")
        ta = c4.number_input("Travel days after", 0, 5, 0, key="sel_ta")
        if e < s:
            st.error("End date is before the start date.")
            return
        t = {"id": None, "name": name or "New tournament", "sport": sport, "venue": venue, "start_date": s,
             "end_date": e, "travel_before": int(tb), "travel_after": int(ta)}

    df = logic.selection_preview(con, user, t)
    if df.empty:
        st.info(f"No {t['sport']} athletes in your scope. Import the roster first.")
        return
    k1, k2, k3 = st.columns(3)
    k1.metric("Candidates", len(df))
    k2.metric("Would miss a CIE or SEE", int(((df["CIE"] + df["SEE"]) > 0).sum()))
    k3.metric("Double-booked", int((df["Other tournaments"] != "").sum()))
    table = df.drop(columns=["athlete_id"]).rename(columns={"Entered": "Pick"})
    edited = st.data_editor(
        table, hide_index=True, width="stretch", key=f"sel_ed_{tid}",
        disabled=[c for c in table.columns if c != "Pick"],
        column_config={"Pick": st.column_config.CheckboxColumn("Pick", help="Tick to enter this athlete"),
                       "Attendance after (%)": st.column_config.NumberColumn(format="%.1f")})
    picked = [int(a) for a, p in zip(df["athlete_id"], edited["Pick"]) if p]
    warn = df[df["athlete_id"].isin(picked) & (df["Flags"] != "Clear")]
    if not warn.empty:
        st.warning("Check before finalising: " + "; ".join(f"{r.Athlete} ({r.Flags})" for r in warn.itertuples()))
    label = f"Finalise {len(picked)} entries" if tid else f"Save tournament with {len(picked)} athletes"
    if st.button(label, type="primary", key="sel_go"):
        try:
            if tid:
                res = logic.finalise_selection(con, user, tid, picked)
                st.toast(f"Saved: {len(res['added'])} added, {len(res['removed'])} removed. They've been notified.")
            else:
                logic.create_with_selection(con, user, t["name"] if t["name"] != "New tournament" else "",
                                            t["sport"], t["venue"], t["start_date"], t["end_date"],
                                            t["travel_before"], t["travel_after"], picked)
                st.toast("Tournament saved. Entered athletes have been notified.")
        except (ValueError, PermissionError) as err:
            st.error(str(err))
            return
        st.rerun()


# ----------------------------------------------------------------- tab: Bulk letters

def render_bulk_letters(con, user):
    if not auth.can(user, "manage_tournaments"):
        st.info("Only coaches and the sports office can print letters in bulk.")
        return
    st.subheader("Exemption letters for the whole squad")
    st.caption("One download with every entered athlete's letter (with its verification QR) and an index sheet "
               "for the PED's signing session.")
    tours = _visible_tournaments(con, user)
    if tours.empty:
        st.info("No tournaments yet.")
        return
    tours = tours.sort_values("start_date", ascending=False)
    opts = {int(t["id"]): f"{t['name']} ({t['start_date']:%d %b})" for _, t in tours.iterrows()}
    tid = st.selectbox("Tournament", list(opts), format_func=opts.get, key="bulk_t")
    record = st.checkbox("Mark these letters as drafted in the letter tracker", value=True, key="bulk_rec",
                         help="So the PED can sign them in the Letter status tab straight away.")
    if st.button("Prepare letters", type="primary", key="bulk_go"):
        try:
            with st.spinner("Writing letters…"):
                st.session_state["bulk_zip"] = (tid, *logic.bulk_letters(con, user, tid, record=record))
        except (ValueError, PermissionError) as err:
            st.error(str(err))
    got = st.session_state.get("bulk_zip")
    if got and got[0] == tid:
        _, blob, index = got
        st.success(f"{len(index)} letters ready.")
        t = data.tournament(con, tid)
        st.download_button("⬇️ Download all letters (.zip)", blob,
                           file_name=f"exemption_letters_{logic._safe(t['name'])}.zip", mime="application/zip",
                           key="bulk_dl")
        st.dataframe(pd.DataFrame(index), hide_index=True, width="stretch")


# ----------------------------------------------------------------- tab: Attendance risk

def render_attendance(con, user):
    floor = settings.get(con, "attendance_min_pct")
    start, end, known = logic.semester_window(con, date.today())
    if user.role == "athlete":
        _athlete_attendance(con, user, floor, start, end, known)
        return
    st.subheader("Attendance risk from sport absences")
    st.caption(f"Projected end-of-semester attendance in each athlete's worst subject once tournament days are "
               f"taken out. Floor: {floor}%. Semester {start:%d %b} to {end:%d %b %Y}."
               + ("" if known else " These dates are a guess until the admin sets them in Settings.")
               + (" Absences with an HoD-approved letter count as present." if settings.get(con, "on_duty_counts_as_present")
                  else " Sport absences count as absent even with a letter."))
    c1, c2, c3 = st.columns(3)
    margin = c1.slider("Warn within (points of the floor)", 0, 15, 5, key="att_margin")
    other = c2.slider("Allow for other absences (%)", 0, 20, 5, key="att_other",
                      help="The planner only knows sport absences. This adds an allowance for illness and the rest.")
    only = c3.toggle("Only athletes at risk", value=True, key="att_only")
    df = logic.attendance_risk(con, user, date.today(), margin=margin, other_absence_pct=other, only_at_risk=only)
    if df.empty:
        st.success("Nobody is near the attendance floor." if only else "No sport absences this semester.")
        return
    k1, k2 = st.columns(2)
    k1.metric("Below the floor", int((df["Status"] == "Below floor").sum()))
    k2.metric("Near the floor", int((df["Status"] == "Near floor").sum()))

    def colour(row):
        c = {"Below floor": "#fde2e2", "Near floor": "#fff4d6"}.get(row["Status"], "")
        return [f"background-color: {c}" if c else ""] * len(row)

    show = df.drop(columns=["athlete_id"])
    st.dataframe(show.style.apply(colour, axis=1).format({"Projected (%)": "{:.1f}",
                                                          "With all letters approved (%)": "{:.1f}"}),
                 hide_index=True, width="stretch")
    st.download_button("⬇️ Download as CSV", show.to_csv(index=False), file_name="attendance_risk.csv",
                       mime="text/csv", key="att_csv")


def _athlete_attendance(con, user, floor, start, end, known):
    st.subheader("Your attendance after sport absences")
    p = logic.attendance_projection(con, user.athlete_id, date.today())
    if not p["subjects"]:
        st.info("Your timetable isn't in the planner yet, so attendance can't be projected.")
        return
    w = p["worst"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Lowest subject", f"{w['pct']:.1f}%", w["subject"], delta_color="off")
    c2.metric("Days away for sport", p["sport_days"])
    c3.metric("Counted as on-duty", p["on_duty_days"])
    if w["pct"] < floor:
        st.error(f"{w['subject']} would drop below the {floor}% floor from sport alone. Talk to your proctor now.")
    elif p["uncovered_days"]:
        st.warning(f"{p['uncovered_days']} sport days don't have an approved letter yet, so they count as absent. "
                   "Get your exemption letters signed.")
    st.caption(f"Floor {floor}%. Semester {start:%d %b} to {end:%d %b %Y}"
               + ("" if known else " (estimated)") + ". Other absences aren't included.")
    df = pd.DataFrame(p["subjects"]).rename(columns={
        "subject": "Subject", "held": "Classes held", "sport_missed": "Missed for sport",
        "on_duty": "Given back (on-duty)", "pct": "Projected (%)"})
    st.dataframe(df, hide_index=True, width="stretch")
