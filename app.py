"""
Dual-Career Planner for College Athletes - Streamlit prototype.
Run:  streamlit run app.py

Everyone signs in. Athletes use their USN; staff use their username. What
each person sees depends on their role (see auth.py). Feature modules under
features/ add their own tabs and public pages (see INTERFACES.md).
"""
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import RerunException, StopException

import auth
import core
import data
import features
import letterdoc
import nav
import roster
import settings

st.set_page_config(page_title="Dual-Career Planner", page_icon="🏅", layout="wide")

# Phones: keep metric tiles two to a row and smaller, instead of one giant tile per screen.
st.html("""<style>
@media (max-width: 640px) {
  [data-testid="stMetricValue"] { font-size: 1.4rem; }
  [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: 0.75rem !important; }
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    flex: 1 1 calc(50% - 0.75rem) !important; min-width: calc(50% - 0.75rem) !important; width: auto !important; }
  h2 { font-size: 1.4rem !important; }
  .block-container { padding-left: 1rem; padding-right: 1rem; }
}
</style>""")

con = data.connect()
if data.is_empty(con):
    data.seed(con)
data.refresh_demo_dates(con)
today = date.today()
# The verification feature builds QR links from PLANNER_BASE_URL; point it at the resolved address.
os.environ["PLANNER_BASE_URL"] = settings.base_url(con)

# ----------------------------------------------------------------- public pages (no login)
route = features.public_route(st.query_params)
if route:
    fn, value = route
    fn(con, value)
    st.stop()


# ----------------------------------------------------------------- login
def login_page():
    st.title("🏅 Dual-Career Planner")
    col, _ = st.columns([1, 1])
    with col:
        auth.login_form()
    if data.get_meta(con, "demo") == "1":
        with col.expander("Demo accounts"):
            st.markdown(
                "Demo mode: these accounts disappear when an admin switches to real data (Settings tab).\n\n"
                "- **Athlete**: USN `1RV25CS012`, password `1RV25CS012` (any demo athlete: password = USN)\n"
                f"- **Staff** (password `{data.DEMO_PASSWORD}` for all): `coach.cricket`, `ped` "
                "(Physical Education Director), `proctor.cse`, `hod.cse`, `admin`")


def change_password_page(user):
    st.title("Set a new password")
    st.info("This is your first sign-in. Please choose your own password.")
    with st.form("pw"):
        p1 = st.text_input("New password", type="password")
        p2 = st.text_input("Repeat it", type="password")
        if st.form_submit_button("Save", type="primary"):
            if p1 != p2:
                st.error("The two passwords don't match.")
            else:
                try:
                    auth.set_password(con, user.id, p1)
                    st.session_state["user"] = auth.get_user(con, user.id)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


user = auth.restore_session(con)
if user is not None:
    user = auth.get_user(con, user.id)  # pick up role/scope changes; None if the account was deleted
    if user is None:
        auth.logout_session(con)
    else:
        st.session_state["user"] = user
if user is None:
    login_page()
    st.stop()
if user.must_change_password:
    change_password_page(user)
    st.stop()


# ===================================================================
#                           SHARED BUILDING BLOCKS
# ===================================================================
def run_feature_tab(label, render):
    """A bug in one feature tab shows an error in that tab instead of breaking the whole page."""
    try:
        render(con, user)
    except Exception as err:  # noqa: BLE001
        if isinstance(err, (StopException, RerunException)):
            raise
        st.error(f"The {label} tab hit an error and couldn't load: {err}")
        if auth.can(user, "manage_users"):
            st.exception(err)


def feature_call(feature, module, fn, *args, **kwargs):
    """Call features.<feature>.<module>.<fn> if that feature is installed; otherwise do nothing."""
    import importlib
    try:
        mod = importlib.import_module(f"features.{feature}.{module}")
    except ImportError:
        return None
    f = getattr(mod, fn, None)
    return f(*args, **kwargs) if f else None


def athlete_summary(aid):
    """Clashes per tournament (keyed by tournament id), load series and zone for one athlete."""
    tt, ev = data.timetable(con, aid), data.events(con, aid)
    tours = data.tournaments_for(con, aid)
    clash_map = {int(t["id"]): core.find_clashes(t.to_dict(), tt, ev) for _, t in tours.iterrows()}
    loads = core.daily_loads(data.sessions(con, aid), today)
    acwr_df = core.acwr_series(loads)
    ratio = acwr_df["acwr"].iloc[-1] if not acwr_df.empty else None
    ratio = None if ratio is None or pd.isna(ratio) else float(ratio)
    zone, advice = core.acwr_zone(ratio)
    return {"tours": tours, "clash_map": clash_map, "loads": loads, "acwr_df": acwr_df,
            "ratio": ratio, "zone": zone, "advice": advice}


def upcoming_clashes(s):
    """Clashes for tournaments that are not over yet."""
    ids = set(core.upcoming(s["tours"], today)["id"].astype(int)) if not s["tours"].empty else set()
    return [c for tid, cl in s["clash_map"].items() if tid in ids for c in cl]

def attention_list(items):
    """Draw 'needs your attention' rows: (tone, text, page key), each with a button to that page."""
    if not items:
        st.success("You're all caught up. Nothing needs your attention right now.")
        return
    for i, (tone, text, key) in enumerate(items):
        with st.container(border=True, key=f"att_{i}"):
            c1, c2 = st.columns([3, 1], vertical_alignment="center")
            c1.markdown(f"{TONE_ICON[tone]} {text}")
            target = next((p for p in pages if p.key == key), None)
            if target is not None:
                c2.button(target.title, key=f"att_go_{i}", icon=target.icon, on_click=nav.go, args=(key,),
                          width="stretch")


def start_here(steps):
    """
    A short getting-started list: (done, text, page key). done=None is a plain step with no tick.
    Open while any tickable step is left.
    """
    left = sum(1 for done, _, _ in steps if done is False)
    tickable = sum(1 for done, _, _ in steps if done is not None)
    title = f"Start here · {tickable - left} of {tickable} done" if tickable else "Start here"
    with st.expander(title, expanded=left > 0):
        for i, (done, text, key) in enumerate(steps, 1):
            c1, c2 = st.container(key=f"step_{i}").columns([5, 1], vertical_alignment="center")
            c1.markdown(f"{i}. " + ("✅ ~~" + text + "~~" if done else ("⬜ " if done is False else "") + text))
            if key in page_keys and not done:
                c2.button("Go", key=f"start_{i}_{key}", on_click=nav.go, args=(key,), width="stretch")


TONE_ICON = {"red": "🔴", "amber": "🟠", "blue": "🔵"}


def athlete_metrics(athlete, s):
    tours, loads, zone, ratio = s["tours"], s["loads"], s["zone"], s["ratio"]
    c1, c2, c3, c4 = st.columns(4)
    up = core.upcoming(tours, today)
    nxt = up.iloc[0] if not up.empty else None
    if nxt is None:
        c1.metric("Next tournament", "None")
    else:
        short = nxt["name"] if len(nxt["name"]) <= 28 else nxt["name"][:28] + "…"
        days = (nxt["start_date"] - today).days
        c1.metric("Next tournament", short, "on now" if days <= 0 else f"in {days} days")
    ahead = upcoming_clashes(s)
    serious = sum(1 for c in ahead if c.kind in ("CIE", "SEE"))
    c2.metric("Clashes ahead", len(ahead), f"{serious} tests/exams", delta_color="inverse")
    c3.metric("Training load zone", zone, f"ACWR {ratio:.2f}" if ratio is not None else "n/a", delta_color="off")
    wk = loads.iloc[-7:].sum()
    c4.metric("Load this week (AU)", int(wk), f"{int(wk - loads.iloc[-14:-7].sum()):+d} vs last week", delta_color="off")
    return serious


def tournaments_entered(s):
    tours, clash_map = s["tours"], s["clash_map"]
    st.markdown("#### Tournaments entered")
    if tours.empty:
        st.info("Not entered in any tournament yet. Your coach adds you when a squad is picked.")
        return
    show = tours[["name", "venue", "start_date", "end_date", "travel_before", "travel_after"]].copy()
    show["clashes"] = [len(clash_map[int(t)]) for t in tours["id"]]
    show["status"] = ["over" if e < today else "on now" if st_ <= today else "upcoming"
                      for st_, e in zip(tours["start_date"], tours["end_date"])]
    st.dataframe(show.rename(columns={"name": "Tournament", "venue": "Venue", "start_date": "Start", "end_date": "End",
                                      "travel_before": "Travel days before", "travel_after": "Travel days after",
                                      "clashes": "Clashes", "status": "Status"}),
                 hide_index=True, width="stretch")


def athlete_attention(athlete, s):
    """What an athlete should do next, most urgent first."""
    aid = int(athlete["id"])
    items = []
    up_ids = set(core.upcoming(s["tours"], today)["id"].astype(int)) if not s["tours"].empty else set()
    for r in feature_call("letters", "logic", "letters_for_athlete", con, aid) or []:
        if r["tournament_id"] not in up_ids:
            continue
        if r["rejected"] or r["needs_redo"]:
            items.append(("red", f"Your letter for **{r['tournament']}** needs a fresh copy.", "letters"))
        elif r["stage"] is None and any(c.kind in ("CIE", "SEE") for c in s["clash_map"].get(r["tournament_id"], [])):
            items.append(("red", f"Tests clash with **{r['tournament']}**. Download the exemption letter and start "
                                 "the sign-off.", "letters"))
    attn = feature_call("plan", "logic", "attendance_status", con, aid, today)
    if attn and attn["risk"] == "high":
        items.append(("red", f"Attendance in **{attn['subject']}** could drop to {attn['pct']:.0f}% "
                             f"(minimum {attn['floor']}%).", "attendance"))
    open_tests = [t for t in feature_call("squad", "logic", "missed_tests", con, aid) or []
                  if t["request"] is None and t["test_date"] >= today - timedelta(days=30)]
    if open_tests:
        items.append(("amber", f"{len(open_tests)} missed test{'s' if len(open_tests) > 1 else ''} without a make-up "
                               "request.", "makeups"))
    if s["zone"] == "High risk":
        items.append(("red", f"Training load is in the high-risk zone. {s['advice']}", "load"))
    elif s["zone"] == "Caution":
        items.append(("amber", f"Training load is climbing. {s['advice']}", "load"))
    unread = feature_call("letters", "logic", "unread_count", con, aid) or 0
    if unread:
        items.append(("blue", f"{unread} unread notification{'s' if unread > 1 else ''}.", "inbox"))
    pending = feature_call("plan", "logic", "to_answer", con, aid, today, 3) or []
    if pending:
        items.append(("blue", "Tell us whether you followed the last few days' plan (one tap each).", "plan"))
    sess = data.sessions(con, aid)
    last = pd.to_datetime(sess["date"]).max().date() if not sess.empty else None
    if last is None or (today - last).days >= 3:
        items.append(("amber", "No training logged for 3+ days. Log your sessions so the load numbers stay right."
                      if last else "Log your first training session.", "log"))
    w = data.wellness(con, aid)
    lastw = pd.to_datetime(w["date"]).max().date() if not w.empty else None
    if lastw is None or (today - lastw).days >= 7:
        items.append(("blue", "Your weekly wellness check-in is due (30 seconds).", "log"))
    order = {"red": 0, "amber": 1, "blue": 2}
    return sorted(items, key=lambda i: order[i[0]])


def part_athlete_home(athlete):
    aid = int(athlete["id"])
    s = athlete_summary(aid)
    athlete_metrics(athlete, s)
    st.markdown("#### Needs your attention")
    attention_list(athlete_attention(athlete, s))
    sess = data.sessions(con, aid)
    steps = [
        (not data.timetable(con, aid).empty, "Import your weekly timetable so clashes can be found.", "timetable"),
        (bool(len(s["tours"])), "Check your clashes once your coach enters you in a tournament.", "clashes"),
        (bool(feature_call("letters", "logic", "letters_for_athlete", con, aid)
              and any(r["stage"] for r in feature_call("letters", "logic", "letters_for_athlete", con, aid))),
         "Download an exemption letter and track its signatures.", "letters"),
        (not sess.empty, "Log a training session after practice (minutes and how hard it felt).", "log"),
        (not data.wellness(con, aid).empty, "Do the weekly wellness check-in.", "log"),
    ]
    start_here(steps)
    tournaments_entered(s)


def part_clashes(athlete, s=None):
    s = s or athlete_summary(int(athlete["id"]))
    st.caption("Tournament window (including travel days) checked against the weekly timetable and one-off "
               "CIE / SEE dates.")
    up = core.upcoming(s["tours"], today)
    if up.empty:
        st.info("No upcoming tournaments, so nothing can clash yet.")
    for _, t in up.iterrows():
        cl = s["clash_map"][int(t["id"])]
        summ = core.clash_summary(cl)
        with st.expander(f"{t['name']}  ·  {t['start_date']} to {t['end_date']}  ·  {len(cl)} clashes over "
                         f"{summ['days_affected']} days", expanded=bool(summ["CIE"] or summ["SEE"])):
            if not cl:
                st.success("Clean. Nothing academic falls inside this window.")
                continue
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("SEE exams", summ["SEE"]); m2.metric("CIE tests", summ["CIE"])
            m3.metric("Labs", summ["lab"]); m4.metric("Classes", summ["class"])
            st.dataframe(pd.DataFrame([c.as_dict() for c in cl]).drop(columns=["Severity"]), hide_index=True,
                         width="stretch")


def part_letter(athlete, s=None, read_only=False):
    aid = int(athlete["id"])
    s = s or athlete_summary(aid)
    st.caption("Pre-filled from the profile and the clash list. Download it, get the PED's signature, then the "
               "proctor and HoD sign by scanning its QR code.")
    up = core.upcoming(s["tours"], today)
    if up.empty:
        st.info("No upcoming tournament to write a letter for.")
        return
    opts = {int(t["id"]): f"{t['name']} ({t['start_date']})" for _, t in up.iterrows()}
    tid = st.selectbox("Tournament", list(opts), format_func=opts.get, key=f"letter_t_{aid}")
    t = up[up["id"] == tid].iloc[0].to_dict()
    cl = s["clash_map"][tid]
    first_day, last_day = core.away_window(t)
    st.write(f"Away from **{first_day}** to **{last_day}**, missing **{len(cl)}** items.")
    if cl:
        st.dataframe(pd.DataFrame([c.as_dict() for c in cl]).drop(columns=["Severity", "Tournament"]),
                     hide_index=True, width="stretch")
    docx = letterdoc.make_letter(con, athlete, t, cl)
    st.download_button("Download letter (.docx)", docx, icon=":material/download:", type="primary",
                       file_name=f"exemption_{athlete['usn']}_{t['start_date']}.docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       key=f"dl_{aid}_{tid}",
                       on_click=None if read_only else feature_call,
                       args=None if read_only else ("letters", "logic", "create_letter", con, aid, tid),
                       kwargs=None if read_only else {"by": user.name})
    if not read_only:
        st.caption("Downloading starts tracking the letter. Follow its signatures on the Sign-off progress tab.")


def part_load(athlete, s=None):
    aid = int(athlete["id"])
    s = s or athlete_summary(aid)
    zone, advice, acwr_df = s["zone"], s["advice"], s["acwr_df"]
    st.caption("Session load = minutes × RPE. ACWR = 7-day average ÷ 28-day average. Above 1.5 is the high-risk zone "
               "in the sports-science literature; 0.8 to 1.3 is the sweet spot.")
    {"High risk": st.error, "Caution": st.warning, "No data yet": st.info}.get(zone, st.success)(
        advice if zone == "No data yet" else f"**{zone}** · {advice}")
    sess = data.sessions(con, aid)
    if sess.empty:
        st.info("No sessions logged yet. The charts fill in as sessions are logged.")
        return
    st.markdown("**Daily load (AU)**")
    st.bar_chart(acwr_df["daily_load"])
    st.markdown("**Acute (7-day) vs chronic (28-day) average load**")
    st.line_chart(acwr_df[["acute_7d", "chronic_28d"]].dropna(how="all"))
    st.markdown("**ACWR over time** (1.5 = high-risk line)")
    r = acwr_df[["acwr"]].dropna()
    if not r.empty:
        r["high-risk line"] = 1.5
        r["sweet-spot floor"] = 0.8
        st.line_chart(r)
    st.markdown("**Recent sessions**")
    st.dataframe(sess.tail(10).iloc[::-1], hide_index=True, width="stretch")


def part_wellness(athlete, s=None):
    aid = int(athlete["id"])
    s = s or athlete_summary(aid)
    st.caption("Weekly check-ins, 1 (low) to 5 (high). High soreness during a load spike is an early warning.")
    w = data.wellness(con, aid)
    if w.empty:
        st.info("No check-ins yet." + ("" if user.role != "athlete" else " Add one on the Log training page."))
        return
    w["date"] = pd.to_datetime(w["date"])
    st.line_chart(w.set_index("date")[["sleep", "soreness", "stress"]])
    last = w.iloc[-1]
    if last["soreness"] >= 4 and s["zone"] in ("High risk", "Caution"):
        st.error("High soreness **and** a load spike in the same week. This is the pattern that precedes most "
                 "overuse injuries. Flag it to the coach.")
    elif last["stress"] >= 4:
        st.warning("Stress is high this week. Check Clashes: an exam or exemption issue may be behind it.")


def part_log(athlete):
    aid = int(athlete["id"])
    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Log a session", anchor=False)
        with st.form("session"):
            d = st.date_input("Date", today, max_value=today)
            minutes = st.number_input("Minutes", 10, 300, 75, step=5)
            rpe = st.slider("How hard was it? (RPE 1 = very easy, 10 = maximal)", 1, 10, 6)
            stype = st.selectbox("Type", ["Skills", "Conditioning", "Match practice", "Match", "Gym", "Recovery"])
            if st.form_submit_button("Save session", type="primary"):
                data.add_session(con, aid, d, minutes, rpe, stype)
                st.toast(f"Saved: {minutes} min × RPE {rpe} = {int(core.session_load(minutes, rpe))} AU")
                st.rerun()
    with right:
        st.subheader("Weekly wellness check-in", anchor=False)
        with st.form("wellness"):
            d2 = st.date_input("Week of", today, key="wd")
            sleep = st.slider("Sleep quality this week", 1, 5, 3)
            sore = st.slider("Muscle soreness", 1, 5, 2)
            stress = st.slider("Academic / life stress", 1, 5, 2)
            if st.form_submit_button("Save check-in", type="primary"):
                data.add_wellness(con, aid, d2, sleep, sore, stress)
                st.toast("Check-in saved.")
                st.rerun()
    sess = data.sessions(con, aid)
    if not sess.empty:
        st.markdown("**Last few sessions**")
        st.dataframe(sess.tail(5).iloc[::-1], hide_index=True, width="stretch")


def part_add_test(athlete):
    aid = int(athlete["id"])
    st.caption("For a CIE, SEE or lab test that isn't in the imported calendar yet.")
    with st.form("event"):
        d3 = st.date_input("Date", today + timedelta(days=7), key="ed")
        kind = st.selectbox("Type", ["CIE", "SEE", "lab"])
        title = st.text_input("Title", "Data Structures CIE-3")
        if st.form_submit_button("Save date", type="primary"):
            data.add_event(con, aid, d3, kind, title)
            st.toast("Saved.")
            st.rerun()
    ev = data.events(con, aid)
    if not ev.empty:
        st.markdown("**Test dates on record**")
        st.dataframe(ev.drop(columns=[c for c in ("id", "athlete_id") if c in ev.columns]), hide_index=True,
                     width="stretch")


def render_athlete_detail(athlete):
    """Staff drill-down on one athlete: the athlete's own screens, read-only, as tabs."""
    aid = int(athlete["id"])
    s = athlete_summary(aid)
    st.subheader(f"{athlete['name']} · {athlete['usn']}", anchor=False)
    st.caption(f"{athlete['sport']} · {athlete['dept']} · {athlete['sem']} sem")
    health = user.role != "faculty"   # faculty never see training load or wellness
    tabs = st.tabs(["Overview", "Clashes", "Exemption letter"] + (["Training load", "Wellness"] if health else []))
    with tabs[0]:
        if health:
            athlete_metrics(athlete, s)
        tournaments_entered(s)
    with tabs[1]:
        part_clashes(athlete, s)
    with tabs[2]:
        part_letter(athlete, s, read_only=True)
    if health:
        with tabs[3]:
            part_load(athlete, s)
        with tabs[4]:
            part_wellness(athlete, s)


def squad_table(ids):
    rows = []
    for aid in ids:
        a = data.athlete(con, aid)
        s = athlete_summary(aid)
        ahead = upcoming_clashes(s)
        up = core.upcoming(s["tours"], today)
        nxt = up.iloc[0] if not up.empty else None
        rows.append({
            "Athlete": a["name"], "USN": a["usn"], "Sport": a["sport"], "Dept": a["dept"], "Sem": a["sem"],
            "Next tournament": nxt["name"] if nxt is not None else "-",
            "Days to go": max((nxt["start_date"] - today).days, 0) if nxt is not None else None,
            "Tests/exams clashing": sum(1 for c in ahead if c.kind in ("CIE", "SEE")), "All clashes": len(ahead),
            "ACWR": round(s["ratio"], 2) if s["ratio"] is not None else None, "Load zone": s["zone"],
            "Week load (AU)": int(s["loads"].iloc[-7:].sum()),
        })
    return pd.DataFrame(rows)


def colour(row):
    if row["Load zone"] == "High risk" or row["Tests/exams clashing"] > 0:
        return ["background-color: #fde2e2"] * len(row)
    if row["Load zone"] == "Caution":
        return ["background-color: #fff4d6"] * len(row)
    return [""] * len(row)


def visible_tournaments():
    tours = data.all_tournaments(con)
    if user.role == "coach" and user.sport:
        tours = tours[tours["sport"].str.lower() == user.sport.lower()]
    return tours


def render_squad(ids):
    if not ids:
        st.info("No athletes in your scope yet. Import the roster first.")
        return
    squad = squad_table(ids)
    st.dataframe(squad.style.apply(colour, axis=1).format({"ACWR": "{:.2f}"}, na_rep="-"), hide_index=True, width="stretch")
    k1, k2, k3 = st.columns(3)
    k1.metric("Athletes with exam clashes", int((squad["Tests/exams clashing"] > 0).sum()))
    k2.metric("Athletes in high-risk load zone", int((squad["Load zone"] == "High risk").sum()))
    k3.metric("Tournaments starting in next 30 days", len(core.starting_within(visible_tournaments(), today, 30)))


def athlete_picker(ids, key):
    if not ids:
        st.info("No athletes in your scope.")
        return None
    names = {aid: f"{a['name']} · {a['usn']}" for aid in ids for a in [data.athlete(con, aid)]}
    aid = st.selectbox("Athlete", ids, format_func=names.get, key=key)
    return data.athlete(con, aid)


def render_tournaments(ids):
    tours = visible_tournaments()
    athletes = data.athletes(con)
    athletes = athletes[athletes["id"].isin(ids)]
    names = dict(zip(athletes["id"].astype(int), athletes["name"] + " · " + athletes["usn"]))

    if not tours.empty:
        show = tours[["name", "sport", "venue", "start_date", "end_date", "travel_before", "travel_after"]].copy()
        show["athletes"] = [len(data.entries(con, t)) for t in tours["id"]]
        st.dataframe(show, hide_index=True, width="stretch")

        st.markdown("#### Change a tournament")
        st.caption("Date changes are saved once and every entered athlete's clashes update automatically."
                   + (" To choose athletes with each one's clash count side by side, use the Squad selection tab."
                      if any(lbl == "Squad selection" for lbl, _ in features.tabs_for(user.role)) else ""))
        opts = {int(t["id"]): f"{t['name']} ({t['start_date']})" for _, t in tours.iterrows()}
        tid = st.selectbox("Tournament", list(opts), format_func=opts.get, key="edit_t")
        t = data.tournament(con, tid)
        with st.form("edit_tourn"):
            name = st.text_input("Name", t["name"])
            venue = st.text_input("Venue", t["venue"])
            c1, c2, c3, c4 = st.columns(4)
            s = c1.date_input("Start", t["start_date"]); e = c2.date_input("End", t["end_date"])
            tb = c3.number_input("Travel days before", 0, 5, int(t["travel_before"]))
            ta = c4.number_input("Travel days after", 0, 5, int(t["travel_after"]))
            current = [a for a in data.entries(con, tid) if a in names]
            who = st.multiselect("Athletes", list(names), default=current, format_func=names.get)
            if st.form_submit_button("Save changes"):
                try:
                    changed = data.update_tournament(con, tid, name=name, venue=venue, start_date=s, end_date=e,
                                                     travel_before=int(tb), travel_after=int(ta))
                    # keep entries outside this coach's scope untouched
                    outside = [a for a in data.entries(con, tid) if a not in names]
                    data.set_entries(con, tid, outside + list(who))
                    st.success("Saved." + (f" Changed: {', '.join(changed)}." if changed else ""))
                    st.rerun()
                except ValueError as err:
                    st.error(str(err))

    st.markdown("#### Add a tournament")
    with st.form("tourn"):
        name = st.text_input("Name", "VTU Inter-Collegiate Badminton")
        if user.role == "coach" and user.sport:
            sport = user.sport
            st.text_input("Sport", sport, disabled=True)
        else:
            sports = sorted(set(athletes["sport"].dropna())) or ["Cricket"]
            sport = st.selectbox("Sport", sports)
        venue = st.text_input("Venue", "Bengaluru")
        c1, c2 = st.columns(2)
        s = c1.date_input("Start", today + timedelta(days=14)); e = c2.date_input("End", today + timedelta(days=15))
        c3, c4 = st.columns(2)
        tb = c3.number_input("Travel days before", 0, 5, 0); ta = c4.number_input("Travel days after", 0, 5, 0)
        who = st.multiselect("Athletes", list(names), format_func=names.get)
        if st.form_submit_button("Save tournament"):
            try:
                data.add_tournament(con, name, sport, venue, s, e, int(tb), int(ta), who, created_by=user.id)
                st.success("Saved. Entered athletes see the clashes on their next sign-in.")
                st.rerun()
            except ValueError as err:
                st.error(str(err))


def render_roster_import():
    st.caption("Upload the sports department spreadsheet (.csv or .xlsx). Column names are matched loosely "
               "(USN / Reg No, Name, Department / Branch, Semester, Section, Sport / Game, Proctor, Phone, Email). "
               "Existing athletes are updated by USN; new ones get a login with their USN as the first password.")
    st.download_button("Download a template", roster.TEMPLATE_CSV, file_name="roster_template.csv", mime="text/csv")
    up = st.file_uploader("Roster file", type=["csv", "xlsx"])
    if not up:
        return
    try:
        df = roster.parse(up)
    except Exception as err:  # noqa: BLE001 - show any parse failure to the user
        st.error(f"Couldn't read that file: {err}")
        return
    rows, problems = roster.validate(df)
    st.write(f"**{len(rows)}** athletes ready to import, **{len(problems)}** rows need attention.")
    if problems:
        with st.expander("Rows that will be skipped", expanded=True):
            st.markdown("\n".join(f"- {p}" for p in problems))
    if rows:
        preview = pd.DataFrame(rows)
        preview["status"] = ["update" if data.athlete_by_usn(con, r["usn"]) else "new" for r in rows]
        if user.role == "coach" and user.sport:
            other = preview[preview["sport"].str.lower() != user.sport.lower()]
            if not other.empty:
                st.warning(f"{len(other)} rows are for other sports. They will be imported, but only the PED sees them.")
        st.dataframe(preview, hide_index=True, width="stretch")
        if st.button(f"Import {len(rows)} athletes", type="primary"):
            res = roster.apply(con, rows)
            st.success(f"Imported: {res['created']} new, {res['updated']} updated, {res['accounts']} logins created.")


def render_users():
    st.dataframe(auth.users(con), hide_index=True, width="stretch")
    st.markdown("#### Add a staff account")
    with st.form("add_user"):
        c1, c2 = st.columns(2)
        username = c1.text_input("Username")
        name = c2.text_input("Full name")
        role = c1.selectbox("Role", ["coach", "faculty", "admin"])
        title = c2.selectbox("Title", ["", "coach", "ped", "teacher", "proctor", "hod"],
                             help="Coach with no sport = Physical Education Director.")
        sport = c1.text_input("Sport (coaches; leave blank for PED)")
        dept = c2.text_input("Department (faculty)")
        sem = c1.text_input("Semester (faculty, optional)", placeholder="3rd")
        section = c2.text_input("Section (faculty, optional)")
        pw = st.text_input("First password (8+ characters)", type="password", help="They will be asked to change it.")
        if st.form_submit_button("Create account"):
            try:
                if not username:
                    raise ValueError("Username is required.")
                auth.check_password_strength(pw, username)
                auth.create_user(con, username, name or username, role, pw, title=title, dept=dept or None,
                                 sem=sem or None, section=section or None, sport=sport or None, must_change_password=True)
                st.success(f"Created {username}.")
                st.rerun()
            except Exception as err:  # noqa: BLE001 - e.g. duplicate username
                st.error(str(err))
    st.markdown("#### Change or remove an account")
    all_users = auth.users(con)
    opts = dict(zip(all_users["id"].astype(int), all_users["username"] + " (" + all_users["role"] + ")"))
    uid = st.selectbox("Account", list(opts), format_func=opts.get)
    u = auth.get_user(con, uid)
    with st.form("edit_user"):
        c1, c2 = st.columns(2)
        role = c1.selectbox("Role", list(auth.ROLES), index=list(auth.ROLES).index(u.role))
        title = c2.text_input("Title", u.title or "")
        sport = c1.text_input("Sport", u.sport or "")
        dept = c2.text_input("Department", u.dept or "")
        sem = c1.text_input("Semester", u.sem or "")
        section = c2.text_input("Section", u.section or "")
        newpw = st.text_input("Reset password to (optional)", type="password")
        a, b = st.columns(2)
        save = a.form_submit_button("Save")
        delete = b.form_submit_button("Delete account", disabled=(u.id == user.id))
        if save:
            try:
                auth.update_user(con, uid, role=role, title=title, sport=sport, dept=dept, sem=sem, section=section)
                if newpw:
                    auth.set_password(con, uid, newpw)
                    con.execute("UPDATE users SET must_change_password=1 WHERE id=?", (uid,)); con.commit()
                st.success("Saved.")
                st.rerun()
            except ValueError as err:
                st.error(str(err))
        if delete:
            auth.delete_user(con, uid)
            st.rerun()


def render_settings():
    st.subheader("College rules")
    st.caption("Placeholders until the department confirms them. Other screens (attendance risk, letters) read these.")
    cur = settings.all(con)
    with st.form("rules"):
        c1, c2 = st.columns(2)
        pct = c1.number_input(settings.LABELS["attendance_min_pct"], 0, 100, int(cur["attendance_min_pct"]))
        cap = c2.number_input(settings.LABELS["max_on_duty_days"], 0, 180, int(cur["max_on_duty_days"]))
        od = st.checkbox(settings.LABELS["on_duty_counts_as_present"], bool(cur["on_duty_counts_as_present"]))
        c3, c4 = st.columns(2)
        s_start = c3.date_input(settings.LABELS["semester_start"],
                                date.fromisoformat(cur["semester_start"]) if cur["semester_start"] else None)
        s_end = c4.date_input(settings.LABELS["semester_end"],
                              date.fromisoformat(cur["semester_end"]) if cur["semester_end"] else None)
        if st.form_submit_button("Save rules"):
            if s_start and s_end and s_end < s_start:
                st.error("Semester end is before its start.")
            else:
                for k, v in [("attendance_min_pct", pct), ("max_on_duty_days", cap), ("on_duty_counts_as_present", od),
                             ("semester_start", s_start.isoformat() if s_start else ""),
                             ("semester_end", s_end.isoformat() if s_end else "")]:
                    settings.set(con, k, v)
                st.success("Saved.")

    st.subheader("Exemption letter wording")
    st.caption("You can use {name} {usn} {dept} {sem} {sport} {proctor} {tournament} {venue}. "
               "The student details, dates and the table of missed classes are filled in automatically.")
    with st.form("letter"):
        vals = {"college_name": st.text_input(settings.LABELS["college_name"], cur["college_name"])}
        for k in ("letter_to", "letter_through", "letter_subject", "letter_salutation"):
            vals[k] = st.text_input(settings.LABELS[k], cur[k])
        vals["letter_request"] = st.text_area(settings.LABELS["letter_request"], cur["letter_request"], height=120)
        vals["letter_closing"] = st.text_input(settings.LABELS["letter_closing"], cur["letter_closing"])
        vals["letter_signatures"] = st.text_input(settings.LABELS["letter_signatures"], cur["letter_signatures"])
        a, b = st.columns(2)
        if a.form_submit_button("Save wording"):
            for k, v in vals.items():
                settings.set(con, k, v)
            st.success("Saved. New downloads use this wording.")
        if b.form_submit_button("Restore default wording"):
            for k in vals:
                settings.set(con, k, settings.DEFAULTS[k])
            st.rerun()
    sample = data.athletes(con)
    tours = data.all_tournaments(con)
    if not sample.empty and not tours.empty:
        a = sample.iloc[0].to_dict()
        t = tours.iloc[0].to_dict()
        st.download_button("Download a sample letter", letterdoc.make_letter(con, a, t), file_name="sample_letter.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    st.subheader("App address for QR codes")
    detected_ip = settings.lan_ip()
    st.write(f"QR codes on letters currently open **{settings.base_url(con)}**.")
    st.caption("Leave blank to detect it automatically. For a demo on a laptop, phones must be on the same Wi-Fi, and "
               f"this machine's address is {'`http://' + detected_ip + ':' + str(settings._port()) + '`' if detected_ip else 'not detected'}. "
               "If the app is deployed (e.g. Streamlit Cloud), enter its https address.")
    with st.form("baseurl"):
        url = st.text_input(settings.LABELS["base_url"], cur["base_url"], placeholder="http://192.168.1.20:8501")
        if st.form_submit_button("Save address"):
            if url and not url.startswith(("http://", "https://")):
                st.error("Start the address with http:// or https://")
            else:
                settings.set(con, "base_url", url)
                st.rerun()
    try:
        import io

        import qrcode
        img = qrcode.make(settings.base_url(con))
        buf = io.BytesIO(); img.save(buf, format="PNG")
        st.image(buf.getvalue(), width=160, caption="Scan with a phone to test the address")
    except ImportError:
        pass

    if data.get_meta(con, "demo") == "1":
        st.divider()
        st.subheader("Switch to real data")
        st.warning("This deletes every demo athlete, tournament, letter and demo login (their passwords are public), "
                   "keeps the rules and wording above, and creates one admin account with a password you choose. "
                   "Then import the real roster. Everyone else must set their own password on first sign-in. "
                   "This cannot be undone.")
        with st.form("fresh"):
            c1, c2 = st.columns(2)
            uname = c1.text_input("New admin username", "sports.office")
            pw = c2.text_input("New admin password (8+ characters)", type="password")
            confirm = st.text_input('Type "DELETE DEMO DATA" to confirm')
            if st.form_submit_button("Switch to real data", type="primary"):
                if confirm.strip() != "DELETE DEMO DATA":
                    st.error("Type the confirmation text exactly.")
                else:
                    try:
                        auth.logout_session(con)
                        data.start_fresh(con, uname, pw)
                        st.success("Done. Sign in with the new admin account.")
                        st.rerun()
                    except ValueError as err:
                        st.error(str(err))


def render_class_view(ids):
    st.subheader("Athletes in your class" if user.sem else f"Athletes in {user.dept or 'your department'}")
    st.caption("Upcoming sport absences, for planning make-up tests.")
    rows = []
    for aid in ids:
        a = data.athlete(con, aid)
        s = athlete_summary(aid)
        for _, t in core.upcoming(s["tours"], today).iterrows():
            cl = s["clash_map"][int(t["id"])]
            first_day, last_day = core.away_window(t.to_dict())
            rows.append({"Athlete": a["name"], "USN": a["usn"], "Sem": a["sem"], "Section": a["section"],
                         "Tournament": t["name"], "Away from": first_day, "Away to": last_day,
                         "CIE/SEE missed": ", ".join(c.title for c in cl if c.kind in ("CIE", "SEE")) or "-",
                         "Labs missed": sum(1 for c in cl if c.kind == "lab")})
    if not rows:
        st.info("No upcoming sport absences for athletes in your scope.")
    else:
        st.dataframe(pd.DataFrame(rows).sort_values("Away from"), hide_index=True, width="stretch")

def staff_attention():
    """What a coach, PED, faculty member or admin should look at, most urgent first."""
    items = []
    tours = visible_tournaments()
    upcoming = core.upcoming(tours, today) if not tours.empty else tours
    # Letters waiting for this person's signature or approval.
    waiting = 0
    letters_logic = None
    try:
        from features.letters import logic as letters_logic
    except ImportError:
        pass
    if letters_logic is not None and not upcoming.empty:
        for tid in upcoming["id"].astype(int):
            for r in letters_logic.letters_for_tournament(con, tid, athlete_ids=ids):
                if not r["letter_id"] or r["rejected"] or r["needs_redo"]:
                    continue
                nxt = letters_logic.next_stage(r["stage"])
                if nxt and nxt != "submitted" and letters_logic.can_advance_to(user, nxt, r["athlete_id"], con):
                    waiting += 1
    if waiting:
        items.append(("red", f"{waiting} exemption letter{'s' if waiting > 1 else ''} waiting for your "
                             "signature or approval.", "letters"))
    reqs = feature_call("squad", "logic", "requests_table", con, ids, ["requested"])
    if reqs is not None and not reqs.empty:
        mine = [r for _, r in reqs.iterrows()
                if feature_call("squad", "logic", "can_decide", con, user, int(r["athlete_id"]))]
        if mine:
            items.append(("amber", f"{len(mine)} make-up test request{'s' if len(mine) > 1 else ''} to schedule.",
                          "makeups"))
    if auth.can(user, "view_squad"):
        soon = core.starting_within(tours, today, 14) if not tours.empty else tours
        risky, high = 0, []
        for aid in ids:
            s = athlete_summary(aid)
            if any(c.kind in ("CIE", "SEE") for c in upcoming_clashes(s)):
                risky += 1
            if s["zone"] == "High risk":
                high.append(data.athlete(con, aid)["name"])
        if high:
            items.append(("red", f"High-risk training load: {', '.join(high[:4])}"
                                 f"{f' and {len(high) - 4} more' if len(high) > 4 else ''}.", "squad"))
        if risky:
            items.append(("amber", f"{risky} athlete{'s' if risky > 1 else ''} have tests clashing with an upcoming "
                                   "tournament.", "squad"))
        if len(soon):
            items.append(("blue", f"{len(soon)} tournament{'s start' if len(soon) > 1 else ' starts'} in the next 14 days. "
                                  "Check taper plans and letters.", "taper"))
        silent = feature_call("reminders", "logic", "silent_athletes", con, today, ids) or []
        if silent:
            items.append(("blue", f"{len(silent)} athlete{'s' if len(silent) > 1 else ''} haven't logged training "
                                  "for 3+ days.", "teamload"))
        inj = feature_call("records", "logic", "squad_injury_dashboard", con, ids, today)
        if inj and inj["current"]:
            out = sum(1 for c in inj["current"] if c["status"] == "out")
            items.append(("amber" if out else "blue", f"{out} injured and {len(inj['current']) - out} returning "
                                                      "from injury.", "injuries"))
    if auth.can(user, "view_class"):
        away = feature_call("verification", "logic", "upcoming_absences", con, ids, today, 14)
        if away is not None and not away.empty:
            tests = int((away["Tests missed"] != "").sum())
            items.append(("blue", f"{away['Athlete'].nunique()} student{'s' if away['Athlete'].nunique() > 1 else ''} "
                                  f"away for sport in the next 14 days"
                                  f"{f', {tests} with tests to make up' if tests else ''}.", "absences"))
    if auth.can(user, "manage_users"):
        dels = feature_call("records", "logic", "deletion_requests", con) or []
        if dels:
            items.append(("red", f"{len(dels)} data deletion request{'s' if len(dels) > 1 else ''} to carry out.",
                          "privacy"))
        if data.get_meta(con, "demo") == "1":
            items.append(("blue", "The app is in demo mode. Switch to real data in Settings before a pilot.",
                          "settings"))
    order = {"red": 0, "amber": 1, "blue": 2}
    return sorted(items, key=lambda i: order[i[0]])


def part_staff_home():
    if auth.can(user, "view_squad"):
        k1, k2, k3 = st.columns(3)
        k1.metric("Athletes", len(ids))
        k2.metric("Upcoming tournaments", len(core.upcoming(visible_tournaments(), today))
                  if not visible_tournaments().empty else 0)
        k3.metric("Starting in 30 days", len(core.starting_within(visible_tournaments(), today, 30))
                  if not visible_tournaments().empty else 0)
    else:
        st.caption(f"You see {len(ids)} athlete{'s' if len(ids) != 1 else ''} in your scope.")
    st.markdown("#### Needs your attention")
    attention_list(staff_attention())
    if user.role == "faculty":
        steps = [
            (None, "Once a semester, import the exam calendar so every student's tests are known.", "exam_import"),
            (None, "See who is away for sport and which tests they will miss.", "absences"),
            (None, "Approve or reject letters, by scanning the QR code on a letter or on the Letters page.", "letters"),
            (None, "Schedule make-up tests for students who missed one.", "makeups"),
        ]
    elif auth.can(user, "manage_users"):
        steps = [
            (data.get_meta(con, "demo") != "1", "Set the college rules and letter wording, then switch to real data.",
             "settings"),
            (len(auth.users(con)) > 1, "Add staff accounts for coaches and faculty.", "users"),
            (bool(ids), "Import the athlete roster.", "roster"),
            (not data.all_tournaments(con).empty, "Add tournaments and pick squads.", "tournaments"),
        ]
    else:
        steps = [
            (bool(ids), "Import your athletes with Roster import.", "roster"),
            (not visible_tournaments().empty, "Add a tournament, then pick the squad with its clash preview.",
             "selection"),
            (None, "Download every athlete's exemption letter in one go.", "letters"),
            (None, "Check the Squad table and the Team load report every week.", "squad"),
        ]
    steps = [s for s in steps if s[2] in page_keys]
    start_here(steps)


def part_help():
    st.markdown("#### What each page is for")
    for section, items in nav.sections(pages):
        st.markdown(f"**{section}**")
        st.markdown("\n".join(f"- **{p.title}**: {p.blurb}" for p in items if p.key != "help"))
    st.markdown("#### How the numbers work")
    st.markdown(
        "- **Session load (AU)** = minutes × RPE, where RPE is how hard the session felt from 1 to 10 "
        "(Foster's session-RPE).\n"
        "- **ACWR** = average daily load of the last 7 days ÷ average of the last 28 days. Above 1.5 is high risk, "
        "1.3 to 1.5 caution, 0.8 to 1.3 the sweet spot, and below 0.8 under-trained.\n"
        "- A **clash** is any timetable slot or CIE / SEE / lab date inside a tournament's window, travel days "
        "included.\n"
        f"- **Attendance risk** uses the college minimum ({settings.get(con, 'attendance_min_pct')}%), the on-duty "
        "rule and the semester dates set by the admin in Settings.")
    st.markdown("#### Who sees what")
    st.markdown(
        "- **Athletes** see only their own data.\n"
        "- **Coaches** see athletes in their sport; the **Physical Education Director** sees every sport and signs "
        "letters at the PED step.\n"
        "- **Faculty** (teacher, proctor, HoD) see students in their department or class. They never see injuries, "
        "wellness or training load.\n"
        "- **Admins** see everything and manage users and settings.")


# ===================================================================
#                              ROUTING BY ROLE
# ===================================================================
ids = auth.visible_athlete_ids(con, user)
# Daily reminders run on the first page load of the day (the feature dedupes; a failure never blocks the page).
try:
    feature_call("reminders", "logic", "run_if_due", con)
except Exception as err:  # noqa: BLE001
    features.load_errors["reminders (daily run)"] = repr(err)


def consent_gate():
    """
    Athletes must agree to the data notice (features/records) before using the app. Until they do,
    or after they withdraw, they only get the privacy page (read the notice, download, ask for deletion).
    Returns True when the normal app may be shown.
    """
    status = feature_call("records", "logic", "consent_status", con, user.athlete_id)
    if status is None or status["current"]:
        return True
    from features.records import logic as records_logic, ui as records_ui
    if not status["answered"] or status["accepted"]:   # never answered, or the notice changed since
        st.title("Before you start")
        if status["answered"]:
            st.info("The data notice changed since you last agreed. Please read it again.")
        st.markdown(records_logic.CONSENT_NOTICE)
        a, b = st.columns(2)
        if a.button("I agree", type="primary", key="gate_yes"):
            records_logic.set_consent(con, user.athlete_id, True)
            st.rerun()
        if b.button("Not now", key="gate_no"):
            st.session_state["consent_declined"] = True
            st.rerun()
        if not st.session_state.get("consent_declined"):
            return False
        st.divider()
    else:
        st.warning("You've withdrawn consent, so the planner is paused for you. You can agree again below, "
                   "download your data, or ask for it to be deleted.")
    run_feature_tab("Privacy & data", records_ui.render_privacy)
    return False


athlete = data.athlete(con, user.athlete_id) if user.role == "athlete" and user.athlete_id else None

feature_tabs = dict(features.tabs_for(user.role))


def core_ok(name):
    """Which core screens this user can open."""
    if name in ("athlete_home", "clashes", "letter", "load", "wellness", "log", "add_test"):
        return user.role == "athlete"
    if name == "staff_home":
        return user.role != "athlete"
    if name == "detail":
        return user.role != "athlete"
    if name == "squad" or name == "tournaments":
        return auth.can(user, "view_squad" if name == "squad" else "manage_tournaments")
    if name == "class_view":   # the basic class list, when the verification feature isn't installed
        return auth.can(user, "view_class") and "Upcoming absences" not in feature_tabs
    if name == "roster":
        return auth.can(user, "import_roster")
    if name in ("users", "settings"):
        return auth.can(user, "manage_users")
    return name == "help"


def page_detail():
    a = athlete_picker(ids, "detail_pick")
    if a is not None:
        render_athlete_detail(a)


CORE = {
    "athlete_home": lambda: part_athlete_home(athlete),
    "clashes": lambda: part_clashes(athlete),
    "letter": lambda: part_letter(athlete),
    "load": lambda: part_load(athlete),
    "wellness": lambda: part_wellness(athlete),
    "log": lambda: part_log(athlete),
    "add_test": lambda: part_add_test(athlete),
    "staff_home": part_staff_home,
    "squad": lambda: render_squad(ids),
    "class_view": lambda: render_class_view(ids),
    "detail": lambda: page_detail(),
    "tournaments": lambda: render_tournaments(ids),
    "roster": render_roster_import,
    "users": render_users,
    "settings": render_settings,
    "help": part_help,
}

pages = nav.build(user.role, feature_tabs, core_ok)
page_keys = {p.key for p in pages}
page = nav.current(pages, st.query_params)

# ----------------------------------------------------------------- sidebar
ROLE_LABEL = {"athlete": "Athlete", "coach": "Coach", "faculty": "Faculty", "admin": "Admin"}
st.html(nav.CSS)
st.sidebar.markdown("### 🏅 Dual-Career Planner")
role_label = "Physical Education Director" if user.is_ped else ROLE_LABEL[user.role]
if user.role == "coach" and user.sport:
    role_label += f" · {user.sport}"
if user.role == "faculty":
    role_label += f" · {(user.title or 'faculty').upper() if user.title == 'hod' else (user.title or 'faculty').title()} · {user.dept or ''}"
if athlete is not None:
    role_label = f"{athlete['usn']} · {athlete['sport']} · {athlete['dept']} {athlete['sem']} sem"
st.sidebar.markdown(f"**{user.name}**  \n{role_label}")
badge = feature_call("letters", "ui", "unread_badge", con, user)   # draws itself or returns text
if isinstance(badge, str) and badge:
    st.sidebar.info(badge)
nav_slot = st.sidebar.container()
st.sidebar.divider()
with st.sidebar.expander("Account"):
    with st.form("chpw"):
        p1 = st.text_input("New password", type="password")
        if st.form_submit_button("Change password"):
            try:
                auth.set_password(con, user.id, p1)
                st.success("Password updated.")
            except ValueError as e:
                st.error(str(e))
    if auth.can(user, "manage_users") and data.get_meta(con, "demo") == "1":
        if st.button("Reset demo data", help="Puts every demo account and record back to how it started."):
            auth.logout_session(con)
            data.reset(con)
            st.rerun()
if st.sidebar.button("Sign out", icon=":material/logout:"):
    auth.logout_session(con)
    st.rerun()
if features.load_errors and auth.can(user, "manage_users"):
    with st.sidebar.expander("⚠️ Feature load errors"):
        for name, err in features.load_errors.items():
            st.code(f"{name}\n{err}")

# ----------------------------------------------------------------- the page
if user.role == "athlete":
    if athlete is None:
        st.error("Your account isn't linked to an athlete record yet. Ask the sports office to import you in the roster.")
        st.stop()
    if not consent_gate():
        st.stop()
nav.draw_sidebar(nav_slot, pages, page)
nav.draw_topnav(pages, page)
if page.key == "home":   # Home greets by name instead of repeating "Home"
    if athlete is not None:
        nav.draw_header(page, f"Hi {athlete['name'].split()[0]}, here is your week")
    else:
        nav.draw_header(page, f"Hi {user.name or user.username}")
else:
    nav.draw_header(page)


def draw_part(kind, name, label):
    if kind == "core":
        CORE[name]()
    else:
        run_feature_tab(label, feature_tabs[name])


if len(page.parts) == 1:
    draw_part(page.parts[0][1], page.parts[0][2], page.title)
else:
    for (label, kind, name), t in zip(page.parts, st.tabs([p[0] for p in page.parts])):
        with t:
            draw_part(kind, name, label)
