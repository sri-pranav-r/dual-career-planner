"""Streamlit tabs: Calendar, Injuries, Taper plan, Semester summary, Export, Privacy & data."""
from __future__ import annotations

import calendar as _cal
import html
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import auth
import data

from . import logic


def _today() -> date:
    return date.today()


def _pick_athlete(con, user, key, label="Athlete") -> dict | None:
    """Athletes get themselves; staff pick from the athletes they can see."""
    if user.role == "athlete":
        return data.athlete(con, user.athlete_id) if user.athlete_id else None
    ids = auth.visible_athlete_ids(con, user)
    if not ids:
        st.info("You don't have any athletes in scope yet.")
        return None
    people = {int(r["id"]): r for _, r in data.athletes(con).iterrows() if int(r["id"]) in set(ids)}
    choice = st.selectbox(label, list(people), key=key,
                          format_func=lambda i: f"{people[i]['name']} · {people[i]['usn']} · {people[i]['sport']}")
    return data.athlete(con, choice)


def _can_see_health(user) -> bool:
    return user.role in ("athlete", "coach", "admin")


# ===================================================================== Calendar

_CAL_STYLE = {
    "tournament": ("🏆", "#f59e0b"), "travel": ("✈️", "#a78bfa"), "injury": ("🩹", "#ef4444"),
    "SEE": ("🎓", "#dc2626"), "CIE": ("📝", "#ea580c"), "lab": ("🧪", "#0ea5e9"),
    "training": ("🏋️", "#16a34a"), "classes": ("📚", "#64748b"),
}
_CAL_NAMES = {"tournament": "Tournament", "travel": "Travel", "injury": "Out injured", "SEE": "SEE exam",
              "CIE": "CIE test", "lab": "Lab exam", "training": "Training logged", "classes": "Classes"}

_CSS = """
<style>
.rc-cal{width:100%;border-collapse:collapse;table-layout:fixed;font-size:0.78rem}
.rc-cal th{padding:4px 2px;text-align:center;font-weight:600;opacity:.7}
.rc-cal td{vertical-align:top;border:1px solid rgba(128,128,128,.25);padding:3px;height:84px;overflow:hidden}
.rc-cal td.rc-out{opacity:.35}
.rc-cal td.rc-today{outline:2px solid #2563eb;outline-offset:-2px}
.rc-n{font-weight:600;margin-bottom:2px}
.rc-chip{display:block;border-left:3px solid;padding:0 3px;margin:1px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media (max-width:640px){.rc-cal td{height:56px;padding:2px}.rc-lbl{display:none}.rc-chip{display:inline;border:0;padding:0}}
</style>
"""


def calendar_html(items: dict, year: int, month: int, today: date, show: set[str]) -> str:
    weeks = _cal.Calendar(firstweekday=0).monthdatescalendar(year, month)
    out = [_CSS, '<table class="rc-cal"><tr>'] + [f"<th>{d}</th>" for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")]
    out.append("</tr>")
    for week in weeks:
        out.append("<tr>")
        for d in week:
            cls = []
            if d.month != month:
                cls.append("rc-out")
            if d == today:
                cls.append("rc-today")
            cell = [f'<div class="rc-n">{d.day}</div>']
            for it in items.get(d, []):
                if it["kind"] not in show:
                    continue
                icon, colour = _CAL_STYLE[it["kind"]]
                title = html.escape(it.get("subjects") or it["label"], quote=True)
                cell.append(f'<span class="rc-chip" style="border-color:{colour}" title="{title}">{icon}'
                            f'<span class="rc-lbl"> {html.escape(it["label"])}</span></span>')
            out.append(f'<td class="{" ".join(cls)}">{"".join(cell)}</td>')
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def render_calendar(con, user):
    a = _pick_athlete(con, user, "rec_cal_pick")
    if a is None:
        return
    today = _today()
    months = []
    y, m = today.year, today.month
    for off in range(-3, 7):
        mm = (m - 1 + off) % 12 + 1
        yy = y + (m - 1 + off) // 12
        months.append((yy, mm))
    c1, c2 = st.columns([1, 2])
    year, month = c1.selectbox("Month", months, index=3, key="rec_cal_month",
                               format_func=lambda ym: date(ym[0], ym[1], 1).strftime("%B %Y"))
    kinds = logic.CAL_KINDS if _can_see_health(user) else [k for k in logic.CAL_KINDS if k != "injury"]
    show = set(c2.multiselect("Show", kinds, default=kinds, key="rec_cal_show",
                              format_func=lambda k: f"{_CAL_STYLE[k][0]} {_CAL_NAMES[k]}"))
    items = logic.month_items(con, a["id"], year, month, today)
    st.markdown(calendar_html(items, year, month, today, show), unsafe_allow_html=True)
    st.caption("On a phone only the icons show; pick a day below to see what's on it. "
               "Classes come from the weekly timetable" +
               (", inside the semester dates set by the admin." if logic._semester_dates_set(con) else "."))

    first = date(year, month, 1)
    last = date(year, month, _cal.monthrange(year, month)[1])
    default = today if first <= today <= last else first
    day = st.date_input("Day", value=default, min_value=first, max_value=last, key="rec_cal_day")
    rows = [it for it in items.get(day, []) if it["kind"] in show]
    if not rows:
        st.write("Nothing on this day.")
    for it in rows:
        extra = f": {it['subjects']}" if it.get("subjects") else ""
        st.markdown(f"{_CAL_STYLE[it['kind']][0]} **{_CAL_NAMES[it['kind']]}** · {it['label']}{extra}")


# ===================================================================== Injuries

_STATUS = {"out": "🔴 Out", "returning": "🟡 Returning", "cleared": "🟢 Cleared"}


def _injury_form(con, user, aid, key):
    with st.form(key, clear_on_submit=True):
        st.markdown("**Log an injury**")
        c1, c2, c3 = st.columns(3)
        when = c1.date_input("When", value=_today(), max_value=_today())
        part = c2.selectbox("Body part", ["Ankle", "Knee", "Hamstring", "Calf", "Quadriceps", "Groin", "Hip",
                                          "Lower back", "Shoulder", "Elbow", "Wrist", "Hand / finger", "Neck",
                                          "Head", "Other"])
        days_out = c3.number_input("Days out (physio's estimate)", min_value=0, max_value=365, value=7)
        what = st.text_input("What happened", placeholder="e.g. rolled it landing from a rebound")
        if st.form_submit_button("Save injury", type="primary"):
            try:
                logic.add_injury(con, aid, when, part, what, days_out, logged_by=user.name)
                st.success("Saved. The return-to-play plan is below.")
                st.rerun()
            except logic.RecordsError as e:
                st.error(str(e))


def _ramp_view(con, user, inj: dict, today: date, key: str):
    r = logic.return_ramp(con, inj, today)
    with st.container(border=True):
        top, right = st.columns([3, 1])
        top.markdown(f"**{inj['body_part']}** · {inj['what']}  \n"
                     f"{pd.Timestamp(inj['injured_on']).strftime('%d %b %Y')} · {int(inj['days_out'])} day(s) out · "
                     f"back from {r['return_on'].strftime('%d %b')}")
        right.markdown(_STATUS[r["status"]])
        if r["weeks"] and r["status"] != "cleared":
            if r["baseline"] is None:
                st.caption("No training was logged in the 4 weeks before the injury, so targets are shown as % only.")
            rows = []
            for w in r["weeks"]:
                rows.append({"Week": w["week"], "From": w["start"].strftime("%d %b"), "To": w["end"].strftime("%d %b"),
                             "% of usual load": f"{w['pct']}%",
                             "Target (AU)": "" if w["target"] is None else int(w["target"]),
                             "Logged (AU)": "" if w["logged"] is None else int(w["logged"]),
                             "": "⚠️ over target" if w["flag"] else ""})
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            if r["current"] and r["current"]["flag"]:
                st.warning("This week's logged load is already above the return plan. Ease off until next week.")
        with st.expander("Update"):
            c1, c2 = st.columns(2)
            new = c1.number_input("Days out", min_value=0, max_value=365, value=int(inj["days_out"]), key=f"{key}_d")
            if c1.button("Save", key=f"{key}_s"):
                logic.update_days_out(con, inj["id"], new)
                st.rerun()
            if c2.button("Delete this entry", key=f"{key}_x"):
                logic.delete_injury(con, inj["id"])
                st.rerun()


def render_injuries(con, user):
    today = _today()
    if user.role == "athlete":
        st.subheader("Your injuries")
        st.caption("Your coach can see these. Faculty can't.")
        _injury_form(con, user, user.athlete_id, "rec_inj_self")
        df = logic.injuries(con, [user.athlete_id])
        if df.empty:
            st.info("No injuries logged.")
        for _, r in df.iterrows():
            _ramp_view(con, user, r.to_dict(), today, f"rec_inj_{r['id']}")
        return

    if not _can_see_health(user):
        st.info("Injury records are only visible to the athlete, their coach and the sports office.")
        return
    ids = auth.visible_athlete_ids(con, user)
    dash, one = st.tabs(["Squad dashboard", "One athlete"])
    with dash:
        d = logic.squad_injury_dashboard(con, ids, today)
        cur = d["current"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Out now", sum(1 for c in cur if c["status"] == "out"))
        c2.metric("On return ramp", sum(1 for c in cur if c["status"] == "returning"))
        c3.metric("Ramp overshoots", sum(1 for c in cur if c["over_target"]))
        if cur:
            st.dataframe(pd.DataFrame([{
                "Athlete": c["name"], "Sport": c["sport"], "Injury": c["body_part"], "Status": _STATUS[c["status"]],
                "Back from": c["return_on"].strftime("%d %b"),
                "This week": "" if c["this_week_pct"] is None else f"{c['this_week_pct']}% load",
                "": "⚠️ over target" if c["over_target"] else ""} for c in cur]),
                hide_index=True, use_container_width=True)
        if d["by_month"].empty:
            st.info("No injuries logged for your athletes yet.")
        else:
            st.markdown("**Injuries by sport and month**")
            pivot = d["by_month"].pivot_table(index="month", columns="sport", values="injuries",
                                              aggfunc="sum", fill_value=0)
            st.bar_chart(pivot)
            st.dataframe(d["by_month"].rename(columns={"sport": "Sport", "month": "Month", "injuries": "Injuries",
                                                       "days_out": "Days out"}),
                         hide_index=True, use_container_width=True)
            st.markdown("**Most common**")
            st.dataframe(d["body_parts"].rename(columns={"body_part": "Body part", "injuries": "Injuries"}),
                         hide_index=True, use_container_width=True)
    with one:
        a = _pick_athlete(con, user, "rec_inj_pick")
        if a is None:
            return
        _injury_form(con, user, int(a["id"]), "rec_inj_staff")
        df = logic.injuries(con, [a["id"]])
        if df.empty:
            st.info("No injuries logged for this athlete.")
        for _, r in df.iterrows():
            _ramp_view(con, user, r.to_dict(), today, f"rec_inj_{r['id']}")


# ===================================================================== Taper

_ZONE_ICON = {"High risk": "🔴", "Caution": "🟠", "Sweet spot": "🟢", "Under-trained": "🔵", "No data yet": "⚪"}
_TREND = {"rising": "↑ rising", "falling": "↓ falling", "steady": "→ steady"}


def _plan_view(con, aid, t, today):
    p = logic.taper_plan(con, aid, t, today)
    st.markdown(f"**{p['tournament']}** · you leave on {p['depart'].strftime('%a %d %b')} "
                f"({p['days_to_depart']} day(s) from today)")
    c1, c2, c3 = st.columns(3)
    c1.metric("Load ratio (ACWR)", "-" if p["ratio"] is None else f"{p['ratio']:.2f}")
    c2.metric("Zone", f"{_ZONE_ICON[p['zone']]} {p['zone']}")
    c3.metric("Trend", _TREND[p["trend"]])
    st.info(p["advice"])
    rows = []
    for d in p["days"]:
        rows.append({"Day": d["date"].strftime("%a %d %b"), "Suggestion": d["plan"],
                     "Target (AU)": "" if d["target_load"] in (None, 0) else int(d["target_load"]),
                     "Logged (AU)": "" if d["logged_load"] is None else int(d["logged_load"])})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption(f"Usual week ≈ {int(p['normal_daily'] * 7)} AU. Taper week target ≈ "
               f"{int(p['normal_daily'] * 7 * p['factor'])} AU ({int(p['factor'] * 100)}%). "
               "Volume drops; intensity stays. A suggestion, not a prescription: your coach has the final say.")


def render_taper(con, user):
    today = _today()
    if user.role == "athlete":
        st.subheader("Taper plan for your next tournament")
        t = logic.next_tournament(con, user.athlete_id, today)
        if t is None:
            st.info("You're not entered in an upcoming tournament.")
            return
        days = (logic.core.away_window(t)[0] - today).days
        if days > 21:
            st.info(f"Your next tournament, {t['name']}, is {days} days away. The taper plan appears three weeks out; "
                    "until then, train normally.")
            return
        _plan_view(con, user.athlete_id, t, today)
        return

    ids = auth.visible_athlete_ids(con, user)
    st.subheader("Athletes leaving for a tournament in the next 14 days")
    rows = logic.squad_taper(con, ids, today)
    if not rows:
        st.info("None of your athletes leave for a tournament in the next 14 days.")
    else:
        st.dataframe(pd.DataFrame([{
            "Athlete": r["name"], "Sport": r["sport"], "Tournament": r["tournament"],
            "Leaves": r["depart"].strftime("%a %d %b"), "ACWR": "-" if r["acwr"] is None else round(r["acwr"], 2),
            "Zone": f"{_ZONE_ICON[r['zone']]} {r['zone']}", "Trend": _TREND[r["trend"]],
            "Cut volume to": f"{r['cut_to_pct']}%", "Rest days": r["rest_days"]} for r in rows]),
            hide_index=True, use_container_width=True)
    a = _pick_athlete(con, user, "rec_taper_pick", "Day-by-day plan for")
    if a is None:
        return
    t = logic.next_tournament(con, a["id"], today)
    if t is None:
        st.info("This athlete isn't entered in an upcoming tournament.")
        return
    _plan_view(con, int(a["id"]), t, today)


# ===================================================================== Semester summary

def _period_picker(con, key):
    s, e = logic.semester_window(con, _today())
    c1, c2 = st.columns(2)
    start = c1.date_input("From", value=s, key=f"{key}_s")
    end = c2.date_input("To", value=e, key=f"{key}_e")
    if not logic._semester_dates_set(con):
        st.caption("Semester dates aren't set in Settings yet, so this defaults to the last 120 days.")
    return start, end


def _college(con):
    try:
        import settings
        return settings.get(con, "college_name")
    except Exception:  # noqa: BLE001
        return None


def render_summary(con, user):
    health = _can_see_health(user)
    if user.role != "athlete":
        mode = st.radio("Show", ["One athlete", "Everyone I can see"], horizontal=True, key="rec_sum_mode")
    else:
        mode = "One athlete"
    start, end = _period_picker(con, "rec_sum")
    if end < start:
        st.error("The end date is before the start date.")
        return
    if mode == "Everyone I can see":
        ids = auth.visible_athlete_ids(con, user)
        df = logic.summaries_table(con, ids, start, end, include_health=health)
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.download_button("Download as CSV", df.to_csv(index=False).encode(), f"semester-summary-{start}-{end}.csv",
                           "text/csv", key="rec_sum_all_csv")
        return

    a = _pick_athlete(con, user, "rec_sum_pick")
    if a is None:
        return
    s = logic.semester_summary(con, a["id"], start, end, include_health=health)
    tot, tr = s["totals"], s["training"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tournaments", tot["tournaments"])
    c2.metric("Days away", tot["days_away"])
    c3.metric("Sessions logged", tr["sessions"])
    c4.metric("Weeks logged", f"{tr['weeks_logged']}/{tr['weeks_in_period']}")
    if s["tournaments"]:
        st.dataframe(pd.DataFrame([{
            "Tournament": t["tournament"], "Dates": f"{t['start'].strftime('%d %b')} to {t['end'].strftime('%d %b')}",
            "Days away": t["days_away"], "Classes missed": t["classes_missed"], "Tests missed": t["tests_missed"],
            "Letter": t["letter"]} for t in s["tournaments"]]), hide_index=True, use_container_width=True)
    else:
        st.info("No tournaments in this period.")
    st.markdown(f"Training: {tr['minutes']} minutes in total, average weekly load {tr['avg_weekly_load']} AU"
                f"{', average RPE ' + str(tr['avg_rpe']) if tr['avg_rpe'] is not None else ''}. "
                f"Load ratio at the end of the period: "
                f"{tr['acwr_at_end'] if tr['acwr_at_end'] is not None else 'not enough data'} ({tr['zone_at_end']}).")
    if health:
        if s["injuries"]:
            st.markdown("Injuries: " + "; ".join(f"{i['body_part']} ({i['days_out']} days out)" for i in s["injuries"]))
        if s.get("wellness"):
            w = s["wellness"]
            st.markdown(f"Wellness averages (1 to 5): sleep {w['sleep']}, soreness {w['soreness']}, stress {w['stress']}.")
    st.download_button("Download for sports-quota renewal (.docx)", logic.summary_docx(s, _college(con)),
                       f"semester-summary-{a['usn']}.docx",
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       key="rec_sum_docx", type="primary")


# ===================================================================== Export

def render_export(con, user):
    if not logic.can_export(user):
        st.info("Your account can't export data.")
        return
    st.subheader("Export everything as CSV")
    scope = {"admin": "every athlete and table",
             "coach": "the athletes in your sport",
             "faculty": "the athletes in your department or class, without wellness or injury data"}.get(user.role, "")
    st.write(f"One .zip with a CSV per table, covering {scope}. Passwords and login sessions are never included.")
    tables = logic.export_tables(con, user)
    st.dataframe(pd.DataFrame([{"Table": k, "Rows": len(v)} for k, v in tables.items()]),
                 hide_index=True, use_container_width=True)
    st.download_button("Download .zip", logic.export_zip(con, user), f"planner-export-{_today()}.zip",
                       "application/zip", type="primary", key="rec_export_zip")


# ===================================================================== Privacy & data

def render_privacy(con, user):
    if user.role == "athlete":
        _privacy_athlete(con, user)
    elif auth.can(user, "manage_users"):
        _privacy_admin(con, user)
    else:
        st.info("Only athletes and the sports office admin use this page.")


def _privacy_athlete(con, user):
    aid = user.athlete_id
    st.subheader("Your data and consent")
    st.markdown(logic.CONSENT_NOTICE)
    s = logic.consent_status(con, aid)
    if s["current"]:
        st.success(f"You agreed on {s['decided_at'][:10]}.")
        if st.button("Withdraw my consent", key="rec_consent_no"):
            logic.set_consent(con, aid, False)
            st.rerun()
    else:
        if s["answered"] and not s["accepted"]:
            st.warning(f"You withdrew consent on {s['decided_at'][:10]}. Your data stays until you ask for it to be "
                       "deleted below.")
        elif s["answered"]:
            st.info("The notice changed since you last agreed. Please read it again.")
        if st.button("I agree", type="primary", key="rec_consent_yes"):
            logic.set_consent(con, aid, True)
            st.rerun()

    st.divider()
    st.markdown("**Download everything the app has about you**")
    st.download_button("Download my data (.zip of CSVs)", logic.export_zip(con, user), f"my-data-{user.username}.zip",
                       "application/zip", key="rec_mydata")

    st.divider()
    st.markdown("**Delete my data**")
    pending = logic.pending_deletion(con, aid)
    if pending:
        st.warning(f"You asked for deletion on {pending['requested_at'][:10]}. The sports office will remove "
                   "everything, including your login. Download your data first if you want a copy.")
        if st.button("Cancel my request", key="rec_del_cancel"):
            logic.cancel_deletion(con, aid)
            st.rerun()
    else:
        st.caption("This removes your profile, timetable, logs, letters, injuries and your login. It can't be undone. "
                   "The sports office carries it out.")
        with st.form("rec_del_form"):
            reason = st.text_input("Reason (optional)")
            sure = st.checkbox("I understand this deletes everything and can't be undone")
            if st.form_submit_button("Ask for my data to be deleted"):
                if not sure:
                    st.error("Tick the box to confirm.")
                else:
                    logic.request_deletion(con, aid, reason)
                    st.rerun()


def _privacy_admin(con, user):
    st.subheader("Consent")
    ids = auth.visible_athlete_ids(con, user)
    df = logic.consent_overview(con, ids)
    if not df.empty:
        counts = df["Consent"].value_counts()
        cols = st.columns(4)
        for c, label in zip(cols, ["Agreed", "Not answered", "Withdrawn", "Agreed to an older notice"]):
            c.metric(label, int(counts.get(label, 0)))
        st.dataframe(df.drop(columns=["athlete_id"]), hide_index=True, use_container_width=True)
        st.caption("Don't use data from athletes who haven't agreed in the project report.")

    st.subheader("Deletion requests")
    reqs = logic.deletion_requests(con, "pending")
    if not reqs:
        st.info("No pending requests.")
    for r in reqs:
        with st.container(border=True):
            st.markdown(f"**{r['name']}** · {r['usn']} · {r['sport']} · asked {r['requested_at'][:10]}"
                        + (f"  \nReason: {r['reason']}" if r["reason"] else ""))
            _delete_control(con, user, r["athlete_id"], r["usn"], f"rec_del_{r['id']}")

    with st.expander("Delete an athlete without a request"):
        people = {int(x["id"]): x for _, x in data.athletes(con).iterrows()}
        if people:
            aid = st.selectbox("Athlete", list(people), key="rec_del_pick",
                               format_func=lambda i: f"{people[i]['name']} · {people[i]['usn']}")
            _delete_control(con, user, aid, people[aid]["usn"], "rec_del_manual")

    done = logic.deletion_requests(con, "done")
    if done:
        st.caption(f"{len(done)} athlete record(s) deleted so far. Only the date and who did it are kept.")


def _delete_control(con, user, aid, usn, key):
    typed = st.text_input(f"Type {usn} to confirm", key=f"{key}_t")
    if st.button("Delete permanently", key=f"{key}_b", type="primary", disabled=typed.strip().upper() != str(usn).upper()):
        logic.delete_athlete_everywhere(con, user, aid)
        st.success(f"{usn} deleted.")
        st.rerun()
