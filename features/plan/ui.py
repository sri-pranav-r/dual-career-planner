"""Streamlit tabs: Next 14 days (athlete, coach, admin) and Pilot results (coach, admin)."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import auth
import data

from . import logic

_ZONE_ICON = {"High risk": "🔴", "Caution": "🟠", "Sweet spot": "🟢", "Under-trained": "🔵", "No data yet": "⚪"}
_RISK_ICON = {"high": "🔴", "moderate": "🟠", "low": "🟢"}
_AREA_ICON = {"training": "🏋️", "study": "📖", "academic": "🎓"}


def _today() -> date:
    return date.today()


def _pick_athlete(con, user, key) -> dict | None:
    if user.role == "athlete":
        return data.athlete(con, user.athlete_id) if user.athlete_id else None
    ids = set(auth.visible_athlete_ids(con, user))
    if not ids:
        st.info("You don't have any athletes in scope yet.")
        return None
    people = {int(r["id"]): r for _, r in data.athletes(con).iterrows() if int(r["id"]) in ids}
    choice = st.selectbox("Plan for", list(people), key=key,
                          format_func=lambda i: f"{people[i]['name']} · {people[i]['usn']} · {people[i]['sport']}")
    return data.athlete(con, choice)


def _days(n: int) -> str:
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"


def _span(a: int, b: int) -> str:
    return _days(a) if a == b else f"in {a} to {b} days" if a > 0 else f"now until {_days(b)}"


# ===================================================================== Next 14 days

def _summary(plan: dict, today: date):
    acad, sport = plan["academic"], plan["sports"]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Academic**")
        lines = []
        for t in acad["tests"]:
            name = "Lab exam: " + t["title"] if t["kind"] == "lab" else t["title"]
            extra = " (you'll be away: request a make-up test)" if t.get("missed") else ""
            lines.append(f"- {name}: {_days((t['date'] - today).days)}{extra}")
        if not lines:
            lines.append("- No tests in the next 14 days")
        a = acad["attendance"]
        if a:
            guess = "" if a["dates_known"] else " (semester dates not set, so an estimate)"
            lines.append(f"- Attendance risk: {_RISK_ICON[a['risk']]} {a['risk']}, lowest is {a['subject']} at "
                         f"{a['pct']:.0f}% against {a['floor']:.0f}%{guess}")
        st.markdown("\n".join(lines))
    with c2:
        st.markdown("**Sports**")
        lines = []
        for t in sport["tournaments"]:
            a, b = (t["start_date"] - today).days, (t["end_date"] - today).days
            if b < 0:
                lines.append(f"- {t['name']}: finished, recovery days are in the plan")
            else:
                lines.append(f"- {t['name']}: {_span(max(a, 0), b)}")
        if not sport["tournaments"]:
            lines.append("- No tournaments in the next 14 days")
        ld = sport["load"]
        ratio = "" if ld["ratio"] is None else f" (ACWR {ld['ratio']:.2f}, {ld['trend']})"
        lines.append(f"- Training load: {_ZONE_ICON[ld['zone']]} {ld['zone'].lower()}{ratio}")
        rc = sport["recovery"]
        cmp = "" if rc["baseline"] is None else f" ({rc['score']:.0f} vs {rc['baseline']:.0f} out of 15)"
        rtext = {"below": "below your usual" + cmp, "above": "better than your usual" + cmp,
                 "normal": "about your usual" + cmp, "stale": "no check-in in the last 10 days",
                 "no_baseline": "needs a few more weekly check-ins to compare",
                 "no_data": "no wellness check-ins yet"}[rc["state"]]
        lines.append(f"- Recovery: {rtext}")
        if sport["injured"]:
            lines.append("- 🩹 Currently out injured")
        st.markdown("\n".join(lines))


def _answer_form(con, user, pending: list[dict], today: date):
    st.subheader("Did you follow it?")
    st.caption("This is for the 4-week pilot: your answers show which suggestions help and which get ignored. "
               "Be honest, \"didn't follow\" is useful too.")
    for s in pending:
        d = date.fromisoformat(s["day"])
        when = "Today" if d == today else "Yesterday" if d == today - timedelta(days=1) else d.strftime("%a %d %b")
        with st.form(f"plan_fu_{s['id']}", border=True):
            st.markdown(f"**{when}** · {_AREA_ICON.get(s['area'], '')} {s['area'].title()} · rule {s['rule_id']}  \n"
                        f"{s['text']}")
            status = st.radio("Answer", list(logic.FOLLOW_STATUSES), horizontal=True, key=f"plan_st_{s['id']}",
                              format_func=logic.FOLLOW_STATUSES.get, label_visibility="collapsed")
            c1, c2 = st.columns(2)
            reason = c1.selectbox("If not, why?", [""] + logic.REASONS, key=f"plan_rs_{s['id']}")
            note = c2.text_input("Note (optional)", key=f"plan_nt_{s['id']}")
            if st.form_submit_button("Save"):
                try:
                    logic.log_followup(con, user, s["id"], status, reason, note, today)
                except (ValueError, PermissionError) as err:
                    st.error(str(err))
                else:
                    st.toast("Saved")
                    st.rerun()


def _day_cards(plan: dict, today: date):
    """One short card per day: what's on, then the training and study suggestion with the rule behind each."""
    for d in plan["days"]:
        on = []
        if d["away"] == "tournament":
            on.append("🏆 Tournament")
        elif d["away"] == "travel":
            on.append("✈️ Travel")
        on += [f"📝 {t['title']}" for t in d["tests"]]
        if d["classes"]:
            on.append(f"📚 {len(d['classes'])} class{'es' if len(d['classes']) > 1 else ''}")
        head = "Today" if d["date"] == today else "Tomorrow" if d["date"] == today + timedelta(days=1) else \
            d["date"].strftime("%a %d %b")
        lines = [f"**{head}** · {', '.join(on) or 'Free day'}"]
        tr, sd, ac = d["training"], d["study"], d["academic"]
        if tr:
            lines.append(f"🏋️ **{logic.LEVEL_LABELS[tr['level']]}**: {tr['text']} `{tr['rule_id']}`")
            if d["also"]:
                lines.append(f"<small>Overrides: {', '.join(sorted({a['rule'] for a in d['also']}))}</small>")
        elif d["away"] is None:
            lines.append("🏋️ Train as your coach planned")
        if sd:
            lines.append(f"📖 {sd['text']} `{sd['rule_id']}`")
        if ac:
            lines.append(f"🎓 {ac['text']} `{ac['rule_id']}`")
        with st.container(border=True):
            st.markdown("  \n".join(lines), unsafe_allow_html=True)


def render_plan(con, user):
    today = _today()
    a = _pick_athlete(con, user, "plan_pick")
    if a is None:
        return
    if not logic.can_view_plan(con, user, a["id"]):
        st.error("You can't see this athlete's plan.")
        return
    plan = logic.build_plan(con, a["id"], today)
    who = "Your" if user.role == "athlete" else f"{a['name']}'s"
    st.subheader(f"{who} next 14 days")
    st.caption(f"{plan['start'].strftime('%a %d %b')} to {plan['end'].strftime('%a %d %b')}. Built from the "
               "timetable, tests, tournaments, training log and wellness check-ins. Each suggestion names the "
               "rule behind it.")
    _summary(plan, today)

    st.markdown("**Recommended plan**")
    if plan["headlines"]:
        st.markdown("\n".join(f"- {h['text']} · *{h['when']}* · `{h['rule_id']}`" for h in plan["headlines"]))
    else:
        st.markdown("- Nothing special coming up: train as your coach planned and keep up with classes.")

    st.markdown("**Day by day**")
    _day_cards(plan, today)

    if user.role == "athlete":
        logic.record_plan(con, a["id"], plan, today)
        pending = logic.to_answer(con, a["id"], today, lookback=3)
        if pending:
            _answer_form(con, user, pending, today)
        own = logic.own_adherence(con, a["id"], today)
        if own["Answered"]:
            st.caption(f"Last 4 weeks: you followed {own['Followed']} of the {own['Answered']} suggestions you "
                       f"answered, and partly followed {own['Partly']}.")

    with st.expander("How the rules work"):
        st.caption("These numbers are placeholders until the PED and coaches confirm them. When two rules "
                   "disagree about a day's training, the lighter one wins; competition days always say compete.")
        st.dataframe(pd.DataFrame([{"Rule": rid, "Name": r["name"], "Why": r["why"]}
                                   for rid, r in sorted(logic.RULES.items(), key=lambda kv: int(kv[0][1:]))]),
                     hide_index=True, width="stretch")


# ===================================================================== Pilot results

def render_pilot(con, user):
    if user.role not in ("coach", "admin"):
        st.error("Only coaches and admins see pilot results.")
        return
    today = _today()
    ids = auth.visible_athlete_ids(con, user)
    st.subheader("Pilot: are athletes following the plan?")
    st.caption("Every suggestion an athlete saw on their Next 14 days page is saved with the rule that made it. "
               "Athletes answer Followed, Partly or Didn't follow. Training suggestions are also checked against "
               "the training log, which doesn't depend on the athlete answering.")
    first = logic.pilot_start(con, ids) or today
    c1, c2 = st.columns(2)
    start = c1.date_input("From", value=first, key="plan_pilot_s")
    end = c2.date_input("To", value=today, key="plan_pilot_e")
    if end < start:
        st.error("The end date is before the start date.")
        return
    rep = logic.pilot_report(con, ids, start, end, today)
    o = rep["overall"]
    if not o["Shown"]:
        st.info("No suggestions have been shown yet. They're saved when athletes open their Next 14 days page.")
        return
    pct = lambda v: "-" if v is None else f"{v:.0f}%"  # noqa: E731
    m = st.columns(4)
    m[0].metric("Athletes", rep["athletes"])
    m[1].metric("Suggestions shown", o["Shown"])
    m[2].metric("Answered", pct(o["Answer rate (%)"]))
    m[3].metric("Followed", pct(o["Followed (%)"]), help="Out of the suggestions answered. "
                f"Followed or partly: {pct(o['Followed or partly (%)'])}.")
    st.markdown("**By rule**")
    st.dataframe(rep["by_rule"], hide_index=True, width="stretch")
    st.markdown("**By athlete**")
    st.dataframe(rep["by_athlete"], hide_index=True, width="stretch")
    if len(rep["reasons"]):
        st.markdown("**Why suggestions weren't followed**")
        st.dataframe(rep["reasons"], hide_index=True, width="stretch")
    st.download_button("Download every suggestion and answer (.csv)",
                       rep["rows"].drop(columns=["suggestion_id", "athlete_id"]).to_csv(index=False).encode(),
                       f"pilot-{start}-to-{end}.csv", "text/csv")
