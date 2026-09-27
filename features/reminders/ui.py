"""Streamlit tabs: the reminders outbox, the weekly team load report, and an athlete's reminder settings."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import auth

from . import logic, senders

STATUS_LABELS = {"sent": "Sent", "dry_run": "Dry run (not sent)", "failed": "Failed", "skipped": "Skipped"}
CHANNEL_LABELS = {"in_app": "In-app", "whatsapp": "WhatsApp", "sms": "SMS"}


def _sender_banner(sender):
    if sender.live:
        st.warning(f"Live sending is ON through {sender.name}. Athletes who opted in will get real messages.")
    else:
        st.info(f"{sender.name}. Messages still reach the in-app inbox; WhatsApp/SMS copies are only listed below.")


# ----------------------------------------------------------------- tab: Reminders (coach, admin)

def render_outbox(con, user):
    ids = auth.visible_athlete_ids(con, user)
    sender = senders.from_env()
    st.subheader("Reminders and nudges")
    st.caption(f"Athletes get a reminder {' and '.join(str(d) for d in logic.REMINDER_DAYS)} days before a tournament "
               f"(clashes plus letter status), and a nudge after {logic.NUDGE_AFTER_DAYS} days without logging training.")
    _sender_banner(sender)

    last = logic.last_run(con)
    c1, c2 = st.columns([1, 2])
    # The run covers every sport, so only the PED and admin get the button; coaches see the results.
    if auth.can(user, "sign_letters_ped") and c1.button("Run today's reminders", type="primary", key="rem_run"):
        out = logic.run_all(con, sender=sender, report=False)
        st.success(f"Sent {out['tournament']} tournament reminders and {out['nudges']} logging nudges across all "
                   "sports. Anyone already reminded was skipped.")
    c2.caption(f"Last daily run: {last['ran_at']}" if last else "The daily run hasn't happened yet.")

    rows = logic.outbox(con, ids)
    if not rows:
        st.write("Nothing sent yet.")
        return
    df = pd.DataFrame(rows)
    kinds = st.multiselect("Show", ["tournament", "nudge"], default=["tournament", "nudge"], key="rem_kinds")
    df = df[df["kind"].isin(kinds)]
    df["status"] = df["status"].map(STATUS_LABELS).fillna(df["status"])
    df["channel"] = df["channel"].map(CHANNEL_LABELS).fillna(df["channel"])
    st.dataframe(df[["created_at", "athlete", "usn", "kind", "channel", "to_addr", "status", "message", "detail"]]
                 .rename(columns={"created_at": "When", "athlete": "Athlete", "usn": "USN", "kind": "Type",
                                  "channel": "Channel", "to_addr": "To", "status": "Status",
                                  "message": "Message", "detail": "Note"}),
                 hide_index=True, width="stretch")


# ----------------------------------------------------------------- tab: Team load report (coach, admin)

def _highlight(row):
    if row["Spiked"]:
        return ["background-color: #f8d7da; color: #58151c"] * len(row)
    if row["Not logging"]:
        return ["background-color: #fff3cd; color: #664d03"] * len(row)
    return [""] * len(row)


def _report_table(report):
    df = pd.DataFrame(report["rows"])
    if df.empty:
        st.write("No athletes in this sport.")
        return
    df = df.rename(columns={"name": "Athlete", "usn": "USN", "sessions_7d": "Sessions (7d)", "load_7d": "Load (7d, AU)",
                            "avg_week_before": "Avg week before", "acwr": "ACWR", "zone": "Zone",
                            "last_logged": "Last logged", "days_silent": "Days silent",
                            "spiked": "Spiked", "not_logging": "Not logging"}).drop(columns=["athlete_id"])
    st.dataframe(df.style.apply(_highlight, axis=1), hide_index=True, width="stretch")
    st.download_button("Download CSV", df.to_csv(index=False).encode(), key=f"rep_csv_{report['sport']}",
                       file_name=f"load-report-{report['sport'].lower()}-{report['week_end']}.csv", mime="text/csv")


def render_team_report(con, user):
    ids = auth.visible_athlete_ids(con, user)
    visible = sorted({a for a in pd.read_sql_query(
        f"SELECT sport AS a FROM athletes WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", con,
        params=[int(i) for i in ids])["a"].dropna()})
    st.subheader("Weekly team load report")
    st.caption(f"Red: load spiked (ACWR above {logic.SPIKE_ACWR}, or the week is {logic.SPIKE_WEEK_RATIO}x the "
               f"weeks before when there's under 4 weeks of data). Yellow: nothing logged for "
               f"{logic.NUDGE_AFTER_DAYS}+ days. A report is saved every Monday.")
    if not visible:
        st.info("No athletes in your squad yet.")
        return
    sport = st.selectbox("Sport", visible, key="rep_sport") if len(visible) > 1 else visible[0]
    week_end = st.date_input("Week ending", value=date.today(), key="rep_end")
    report = logic.team_report(con, sport, week_end, ids)
    st.markdown(f"**{report['summary']}**")
    _report_table(report)
    if st.button("Save this report", key="rep_save"):
        logic.save_report(con, report)
        st.success("Saved.")

    past = [r for r in logic.saved_reports(con, [sport]) if r["week_end"] != week_end.isoformat()]
    if past:
        with st.expander("Earlier reports"):
            for r in past:
                st.markdown(f"- {r['summary']}")


# ----------------------------------------------------------------- tab: Reminder settings (athlete)

def render_athlete_settings(con, user):
    aid = user.athlete_id
    if aid is None:
        st.info("Your login isn't linked to an athlete record.")
        return
    a = logic.data.athlete(con, aid) or {}
    p = logic.prefs(con, aid)
    st.subheader("Reminders on your phone")
    st.write(f"You always get reminders in the Notifications tab. You can also get them on WhatsApp or SMS: "
             f"a reminder {' and '.join(str(d) for d in logic.REMINDER_DAYS)} days before each tournament, and a "
             f"nudge if you haven't logged training for {logic.NUDGE_AFTER_DAYS} days.")
    phone = senders.normalise_phone(a.get("phone"))
    if phone:
        st.caption(f"Phone on record: {phone}")
    else:
        st.warning(f"The phone number on record ({a.get('phone') or 'blank'}) isn't a valid mobile number. "
                   "Ask the sports office to correct it.")
    ok = st.checkbox("Send my reminders on WhatsApp or SMS. I agree to my phone number being used for this.",
                     value=p["external_ok"], key="rem_ok")
    channel = st.radio("How", senders.CHANNELS, index=senders.CHANNELS.index(p["channel"]),
                       format_func=CHANNEL_LABELS.get, horizontal=True, key="rem_ch", disabled=not ok)
    if (ok, channel) != (p["external_ok"], p["channel"]):
        logic.set_prefs(con, aid, ok, channel)
        st.success("Saved.")

    mine = [r for r in logic.outbox(con, [aid], limit=20) if r["channel"] == "in_app"]
    if mine:
        st.markdown("**Recent reminders**")
        for r in mine:
            st.markdown(f"- {r['created_at'][:10]}: {r['message']}")
