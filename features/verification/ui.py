"""Streamlit screens for letter verification. Logic lives in logic.py."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import auth

from . import logic


def _fmt(d) -> str:
    return f"{d:%d %b %Y (%a)}"


# ---------------------------------------------------------------------------
# Public route: ?verify=<token>   (what the QR code on the letter opens)
# ---------------------------------------------------------------------------

def render_verify(con, token: str) -> None:
    st.title("Exemption letter check")
    v = logic.verify_token(con, token)
    if not v.valid:
        st.error(f"**Not verified.** {v.reason}")
        st.caption("Ask the athlete to download the letter again from the planner, or contact the Physical Education Department.")
        return

    st.success(f"**Verified.** {v.reason}")
    for w in v.warnings:
        st.warning(w)

    a, t = v.athlete, v.tournament
    first, last = v.current_window
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Student")
        st.markdown(f"**{a['name']}**  \nUSN {a['usn']}  \n{a['dept']} · {a['sem']} sem"
                    + (f" · section {a['section']}" if a.get("section") else "")
                    + f"  \nSport: {a['sport']}")
    with c2:
        st.markdown("#### Tournament, as confirmed by the PED")
        st.markdown(f"**{t['name']}**  \n{t.get('venue') or ''}  \n"
                    f"Plays {logic._d(t['start_date']):%d %b} to {logic._d(t['end_date']):%d %b %Y}  \n"
                    f"Away incl. travel: **{_fmt(first)} to {_fmt(last)}**")
        st.markdown(("✅ On the entry list" if v.entered else "❌ Not on the entry list")
                    + ("  \n✅ PED confirmed" if v.ped_confirmed else "  \n⏳ Awaiting PED signature"))
        if v.created_by:
            st.caption(f"Entered by {v.created_by}")

    st.markdown("#### What the student misses")
    if v.missed:
        df = pd.DataFrame([c.as_dict() for c in sorted(v.missed, key=lambda c: c.date)]).drop(columns=["Severity", "Tournament"])
        tests = sum(1 for c in v.missed if c.kind in ("CIE", "SEE"))
        st.caption(f"{len(v.missed)} slots, including {tests} CIE / SEE assessments needing a make-up.")
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.caption("Nothing academic falls in this window.")

    st.markdown(f"#### Status: **{v.status or 'not started'}**")
    if v.decisions:
        st.dataframe(pd.DataFrame(v.decisions).rename(columns={
            "reviewer_name": "Reviewer", "reviewer_title": "As", "decision": "Decision", "note": "Note", "decided_at": "When"}),
            hide_index=True, use_container_width=True)

    _decision_box(con, v)


def _decision_box(con, v: logic.Verification) -> None:
    st.divider()
    flash = st.session_state.pop("ver_flash", None)
    if flash:
        st.success(flash)
    user = auth.current_user()
    if user is None or not auth.can(user, "approve_letters"):
        st.info("Proctors, class teachers and HoDs: sign in to the planner to approve or reject this request.")
        login = getattr(auth, "login_form", None)
        if callable(login):
            login()
        return
    aid, tid = v.athlete["id"], v.tournament["id"]
    if not auth.can_view_athlete(con, user, aid):
        st.info("This student is outside your department or class, so you can view but not sign this letter.")
        return
    if not v.approvable:
        st.info("This letter can't be signed as it stands. See the warning above.")
        return

    title = "admin" if user.role == "admin" else user.title
    who = logic.REVIEWER_TITLES.get(title, title)
    st.markdown(f"Signed in as **{user.name}** ({who})")
    need = logic.STAGE_BEFORE.get(title)
    if v.letter["rejected"]:
        st.info("This letter was rejected. It can be signed again once the student downloads a fresh one.")
        return
    if need and v.letter["stage"] != need:
        if logic._stage_at_least(v.letter, need):
            st.info(f"Your step is done. The letter is at {v.status}.")
        else:
            st.info(f"Nothing for you to sign yet. The letter is at {v.status}.")
        return
    note = st.text_input("Note to the student (required to reject)", key="ver_note")
    b1, b2 = st.columns(2)
    for col, decision, label, kind in ((b1, "approved", "✅ Approve", "primary"), (b2, "rejected", "❌ Reject", "secondary")):
        if col.button(label, type=kind, use_container_width=True, key=f"ver_{decision}"):
            try:
                status = logic.record_decision(con, user, aid, tid, decision, note)
            except (PermissionError, ValueError, logic.letters.LetterError) as e:
                st.error(str(e))
            else:
                st.session_state["ver_flash"] = f"Recorded. Letter status is now **{status}**. The student and coach can see it."
                st.rerun()


# ---------------------------------------------------------------------------
# Tab for athletes and coaches: faculty decisions on their letters
# ---------------------------------------------------------------------------

def render_decisions(con, user) -> None:
    st.subheader("Faculty decisions on letters")
    ids = auth.visible_athlete_ids(con, user)
    df = logic.decisions_table(con, ids)
    if df.empty:
        st.info("No proctor or HoD has approved or rejected a letter yet. Each letter carries a QR code they scan to do this.")
        return
    rejected = df[df["Decision"] == "rejected"]
    if not rejected.empty:
        st.error(f"{len(rejected)} rejection(s). Read the note, fix the letter and download it again.")
    st.dataframe(df.drop(columns=["USN"] if user.role == "athlete" else []), hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Tab for faculty: department view of upcoming absences
# ---------------------------------------------------------------------------

def render_department(con, user) -> None:
    auth.require(user, "view_class")
    st.subheader("Athletes away in the coming weeks")
    horizon = st.slider("Look ahead (days)", 7, 120, 45, step=7, key="ver_horizon")
    ids = auth.visible_athlete_ids(con, user)
    today = date.today()
    df = logic.upcoming_absences(con, ids, today, horizon)
    if df.empty:
        st.success("No athlete in your class or department is away in this period.")
        return
    k1, k2, k3 = st.columns(3)
    k1.metric("Athletes away", df["athlete_id"].nunique())
    k2.metric("Missing a CIE / SEE", int((df["Tests missed"] != "").sum()))
    k3.metric("Letters not yet approved", int((~df["Letter status"].map(logic._norm).isin({"hodapproved", "submitted"})).sum()))
    st.dataframe(df.drop(columns=["athlete_id", "tournament_id"]), hide_index=True, use_container_width=True)

    st.markdown("#### Make-up tests to schedule")
    plan = logic.makeup_plan(con, ids, today, horizon)
    if plan.empty:
        st.caption("No CIE or SEE falls inside these absences.")
    else:
        st.dataframe(plan, hide_index=True, use_container_width=True)
        st.download_button("⬇️ Download make-up list (.csv)", plan.to_csv(index=False).encode(),
                           file_name=f"makeup_tests_{today}.csv", mime="text/csv")
