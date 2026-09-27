"""Streamlit tabs for the letter tracker and the athlete's notification inbox."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from . import logic

ICONS = {"done": "✅", "next": "⏳", "todo": "▫️"}


def _stepper(stage: str | None) -> str:
    reached = -1 if stage is None else logic.STAGES.index(stage)
    parts = []
    for i, s in enumerate(logic.STAGES):
        icon = ICONS["done"] if i <= reached else ICONS["next"] if i == reached + 1 else ICONS["todo"]
        parts.append(f"{icon} {logic.STAGE_LABELS[s]}")
    return "  →  ".join(parts)


def _waiting_on(stage: str | None) -> str:
    return {
        None: "you: download the letter from the Exemption letter tab",
        "drafted": "the Physical Education Director's signature",
        "ped_signed": "your proctor or class teacher's signature",
        "proctor_signed": "the HoD's approval",
        "hod_approved": "you: hand it in at the department office, then mark it submitted",
        "submitted": "nothing, all done",
    }[stage]


def _history(con, letter_id):
    h = logic.letter_history(con, letter_id)
    if h:
        df = pd.DataFrame(h)
        df["stage"] = df["stage"].map(logic.STAGE_LABELS)
        st.dataframe(df.rename(columns={"stage": "Stage", "changed_at": "When", "changed_by": "By", "note": "Note"}),
                     hide_index=True, width="stretch")


# ----------------------------------------------------------------- tab: Letter status

def render_status(con, user):
    if user.role == "athlete":
        _athlete_status(con, user)
    else:
        _staff_board(con, user)


def _athlete_status(con, user):
    aid = user.athlete_id
    st.subheader("Where your exemption letters are")
    rows = logic.letters_for_athlete(con, aid)
    if not rows:
        st.info("You are not entered in any tournament yet.")
        return
    for r in rows:
        with st.container(border=True):
            st.markdown(f"**{r['tournament']}** · {r['start_date']} to {r['end_date']}")
            if r["rejected"]:
                why = next((h["note"] for h in reversed(logic.letter_history(con, r["letter_id"]))
                            if (h["note"] or "").startswith("Rejected")), "Rejected")
                st.error(f"{why.rstrip('.')}. Fix it, download a fresh letter, then press the button below.")
                if st.button("I've downloaded the corrected letter", key=f"rej_{r['tournament_id']}"):
                    logic.create_letter(con, aid, r["tournament_id"], by=user.name)
                    st.rerun()
                continue
            if r["needs_redo"]:
                st.error("The tournament dates changed after you drafted this letter. "
                         "Download a fresh one and collect signatures again.")
                if st.button("I've downloaded the new letter", key=f"redo_{r['tournament_id']}"):
                    logic.create_letter(con, aid, r["tournament_id"], by=user.name)
                    st.rerun()
                continue
            st.caption(_stepper(r["stage"]))
            st.progress(logic.progress(r["stage"]))
            st.markdown(f"Waiting on {_waiting_on(r['stage'])}.")
            c1, c2 = st.columns(2)
            if r["stage"] is None:
                if c1.button("I've downloaded it, start tracking", key=f"start_{r['tournament_id']}"):
                    logic.create_letter(con, aid, r["tournament_id"], by=user.name)
                    st.rerun()
            elif r["stage"] == "hod_approved":
                if c1.button("Mark as submitted", key=f"sub_{r['letter_id']}", type="primary"):
                    logic.advance_as(con, user, r["letter_id"])
                    st.rerun()
            if r["letter_id"]:
                with st.expander("History"):
                    _history(con, r["letter_id"])


def _staff_board(con, user):
    import auth
    import data

    st.subheader("Exemption letters by tournament")
    visible = auth.visible_athlete_ids(con, user)
    tours = data.all_tournaments(con)
    if not tours.empty:   # only tournaments with one of your athletes entered; ones not over yet first
        tours = tours[[bool(logic.letters_for_tournament(con, int(t), athlete_ids=visible)) for t in tours["id"]]]
    if tours.empty:
        st.info("None of your athletes are entered in a tournament yet.")
        return
    today = date.today()
    tours = tours.assign(_over=[pd.Timestamp(e).date() < today for e in tours["end_date"]]).sort_values(
        ["_over", "start_date"])
    options = {f"{t['name']} ({t['start_date']})" + (" · over" if t["_over"] else ""): int(t["id"])
               for _, t in tours.iterrows()}
    label = st.selectbox("Tournament", list(options))
    rows = logic.letters_for_tournament(con, options[label], athlete_ids=visible)
    if not rows:
        st.info("None of your athletes are entered in this tournament.")
        return

    mine_only = st.toggle("Only letters waiting on me", value=user.role != "coach")
    counts = {s: 0 for s in logic.STAGES}
    for r in rows:
        if r["stage"]:
            counts[r["stage"]] += 1
    cols = st.columns(len(logic.STAGES) + 1)
    cols[0].metric("Not drafted", sum(1 for r in rows if r["stage"] is None))
    for c, s in zip(cols[1:], logic.STAGES):
        c.metric(logic.STAGE_LABELS[s], counts[s])

    for r in rows:
        nxt = logic.next_stage(r["stage"]) if r["stage"] else None
        can = bool(nxt) and not r["needs_redo"] and not r["rejected"] and logic.can_advance_to(user, nxt, r["athlete_id"], con)
        if mine_only and not can:
            continue
        with st.container(border=True):
            left, right = st.columns([3, 1])
            left.markdown(f"**{r['name']}** · {r['usn']} · {r['dept']} {r['sem']} · proctor {r['proctor'] or '-'}")
            if r["stage"] is None:
                left.caption("Letter not drafted yet")
            elif r["rejected"]:
                left.caption("❌ Rejected, waiting for the athlete to redraft")
            elif r["needs_redo"]:
                left.caption("⚠️ Dates changed, waiting for the athlete to redraft")
            else:
                left.caption(_stepper(r["stage"]))
            if can and right.button(f"Mark {logic.STAGE_LABELS[nxt]}", key=f"adv_{r['letter_id']}", type="primary"):
                logic.advance_as(con, user, r["letter_id"])
                st.rerun()
            if can and nxt != "submitted":
                with right.popover("Reject"):
                    reason = st.text_input("Reason", key=f"why_{r['letter_id']}")
                    if st.button("Send back to athlete", key=f"rejbtn_{r['letter_id']}", disabled=not reason.strip()):
                        logic.reject_as(con, user, r["letter_id"], reason)
                        st.rerun()
            if r["letter_id"] and r["stage"] != "drafted" and user.role in ("coach", "faculty", "admin"):
                last = logic.letter_history(con, r["letter_id"])[-1]
                if last["changed_by"] == user.name and right.button("Undo", key=f"undo_{r['letter_id']}"):
                    logic.undo_last_step(con, r["letter_id"], by=user.name)
                    st.rerun()


# ----------------------------------------------------------------- tab: Notifications

def render_inbox(con, user):
    aid = user.athlete_id
    unread = logic.unread_count(con, aid)
    head, btn = st.columns([4, 1])
    head.subheader(f"Notifications ({unread} new)" if unread else "Notifications")
    if unread and btn.button("Mark all read"):
        logic.mark_all_read(con, aid)
        st.rerun()
    items = logic.notifications(con, aid)
    if not items:
        st.info("Nothing yet. You'll hear here when a tournament you're entered in changes.")
        return
    for n in items:
        fresh = n["read_at"] is None
        with st.container(border=True):
            st.markdown(("🔴 " if fresh else "") + n["message"])
            c1, c2 = st.columns([4, 1])
            c1.caption(n["created_at"].replace("T", " "))
            if fresh and c2.button("Mark read", key=f"read_{n['id']}"):
                logic.mark_read(con, n["id"])
                st.rerun()


def unread_badge(con, user) -> str:
    """Core shows this in the sidebar. Empty string when there is nothing to say."""
    if getattr(user, "role", None) != "athlete" or not getattr(user, "athlete_id", None):
        return ""
    n = logic.unread_count(con, user.athlete_id)
    return f"🔔 {n} new notification{'s' if n != 1 else ''}. See Notifications in the menu." if n else ""
