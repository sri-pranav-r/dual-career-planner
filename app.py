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
    st.caption("RVCE Design Thinking Lab prototype")
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

# ----------------------------------------------------------------- sidebar
ROLE_LABEL = {"athlete": "Athlete", "coach": "Coach", "faculty": "Faculty", "admin": "Admin"}
st.sidebar.title("🏅 Dual-Career Planner")
st.sidebar.caption("RVCE Design Thinking Lab prototype")
role_label = "Physical Education Director" if user.is_ped else ROLE_LABEL[user.role]
if user.role == "coach" and user.sport:
    role_label += f" · {user.sport}"
if user.role == "faculty":
    role_label += f" · {(user.title or 'faculty').upper() if user.title == 'hod' else (user.title or 'faculty').title()} · {user.dept or ''}"
st.sidebar.markdown(f"**{user.name}**  \n{role_label}")
if st.sidebar.button("Sign out"):
    auth.logout_session(con)
    st.rerun()
with st.sidebar.expander("Change password"):
    with st.form("chpw"):
        p1 = st.text_input("New password", type="password")
        if st.form_submit_button("Update"):
            try:
                auth.set_password(con, user.id, p1)
                st.success("Password updated.")
            except ValueError as e:
                st.error(str(e))
if auth.can(user, "manage_users") and data.get_meta(con, "demo") == "1":
    st.sidebar.divider()
    if st.sidebar.button("Reset demo data"):
        auth.logout_session(con)
        data.reset(con)
        st.rerun()
if features.load_errors and auth.can(user, "manage_users"):
    with st.sidebar.expander("⚠️ Feature load errors"):
        for name, err in features.load_errors.items():
            st.code(f"{name}\n{err}")


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


def render_athlete(athlete, read_only=False):
    aid = int(athlete["id"])
    s = athlete_summary(aid)
    tours, clash_map, loads, acwr_df = s["tours"], s["clash_map"], s["loads"], s["acwr_df"]
    zone, advice, ratio = s["zone"], s["advice"], s["ratio"]
    sess = data.sessions(con, aid)

    labels = ["Overview", "Clashes", "Exemption letter", "Training load", "Wellness"]
    if not read_only:
        labels.append("Log / add")
    extra = features.tabs_for("athlete") if not read_only else []
    tabs = st.tabs(labels + [lbl for lbl, _ in extra])
    tab = dict(zip(labels, tabs))

    # ---------------- Overview
    with tab["Overview"]:
        first = athlete["name"].split()[0]
        st.subheader(f"Hi {first}, here is your week" if not read_only else f"{athlete['name']} · {athlete['usn']}")
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

        if zone == "High risk":
            st.error(f"**Training load warning.** {advice}")
        elif zone == "Caution":
            st.warning(f"**Load climbing.** {advice}")
        if serious:
            st.warning(f"**{serious}** CIE or SEE assessments clash with upcoming tournaments. "
                       "Generate the exemption letter early: the Clashes tab shows exactly which ones.")
        else:
            st.success("No tests or exams clash with upcoming tournaments right now.")

        st.markdown("#### Tournaments entered")
        if tours.empty:
            st.info("Not entered in any tournament yet.")
        else:
            show = tours[["name", "venue", "start_date", "end_date", "travel_before", "travel_after"]].copy()
            show["clashes"] = [len(clash_map[int(t)]) for t in tours["id"]]
            show["status"] = ["over" if e < today else "on now" if st_ <= today else "upcoming"
                              for st_, e in zip(tours["start_date"], tours["end_date"])]
            st.dataframe(show, hide_index=True, width="stretch")

    # ---------------- Clashes
    with tab["Clashes"]:
        st.subheader("Where sport and academics collide")
        st.caption("Tournament window (including travel days) checked against the weekly timetable and one-off CIE / SEE dates.")
        up = core.upcoming(tours, today)
        if up.empty:
            st.info("No upcoming tournaments.")
        for _, t in up.iterrows():
            cl = clash_map[int(t["id"])]
            summ = core.clash_summary(cl)
            with st.expander(f"{t['name']}  ·  {t['start_date']} to {t['end_date']}  ·  {len(cl)} clashes over {summ['days_affected']} days",
                             expanded=bool(summ["CIE"] or summ["SEE"])):
                if not cl:
                    st.success("Clean. Nothing academic falls inside this window.")
                    continue
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("SEE exams", summ["SEE"]); m2.metric("CIE tests", summ["CIE"])
                m3.metric("Labs", summ["lab"]); m4.metric("Classes", summ["class"])
                st.dataframe(pd.DataFrame([c.as_dict() for c in cl]).drop(columns=["Severity"]), hide_index=True, width="stretch")

    # ---------------- Exemption letter
    with tab["Exemption letter"]:
        st.subheader("Attendance exemption / make-up request")
        st.caption("Pre-filled from the profile and the clash list. Download, get the PED's signature, submit to the department.")
        up = core.upcoming(tours, today)
        if up.empty:
            st.info("No upcoming tournament to write a letter for.")
        else:
            opts = {int(t["id"]): f"{t['name']} ({t['start_date']})" for _, t in up.iterrows()}
            tid = st.selectbox("Tournament", list(opts), format_func=opts.get, key=f"letter_t_{aid}")
            t = up[up["id"] == tid].iloc[0].to_dict()
            cl = clash_map[tid]
            first_day, last_day = core.away_window(t)
            st.write(f"Away from **{first_day}** to **{last_day}**, missing **{len(cl)}** items.")
            if cl:
                st.dataframe(pd.DataFrame([c.as_dict() for c in cl]).drop(columns=["Severity", "Tournament"]),
                             hide_index=True, width="stretch")
            docx = letterdoc.make_letter(con, athlete, t, cl)
            st.download_button("⬇️ Download letter (.docx)", docx,
                               file_name=f"exemption_{athlete['usn']}_{t['start_date']}.docx",
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               key=f"dl_{aid}_{tid}",
                               on_click=None if read_only else feature_call,
                               args=None if read_only else ("letters", "logic", "create_letter", con, aid, tid),
                               kwargs=None if read_only else {"by": user.name})

    # ---------------- Training load
    with tab["Training load"]:
        st.subheader("Training load and injury-risk signal")
        st.caption("Session load = minutes × RPE. ACWR = 7-day average ÷ 28-day average. Above 1.5 is the high-risk zone "
                   "in the sports-science literature; 0.8 to 1.3 is the sweet spot.")
        {"High risk": st.error, "Caution": st.warning, "No data yet": st.info}.get(zone, st.success)(
            advice if zone == "No data yet" else f"**{zone}** · {advice}")
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

    # ---------------- Wellness
    with tab["Wellness"]:
        st.subheader("Weekly check-in")
        w = data.wellness(con, aid)
        if w.empty:
            st.info("No check-ins yet.")
        else:
            w["date"] = pd.to_datetime(w["date"])
            st.line_chart(w.set_index("date")[["sleep", "soreness", "stress"]])
            last = w.iloc[-1]
            if last["soreness"] >= 4 and zone in ("High risk", "Caution"):
                st.error("High soreness **and** a load spike in the same week. This is the pattern that precedes most "
                         "overuse injuries. Flag it to the coach.")
            elif last["stress"] >= 4:
                st.warning("Stress is high this week. Check the Clashes tab: an exam or exemption issue may be behind it.")

    # ---------------- Data entry (athlete only)
    if not read_only:
        with tab["Log / add"]:
            st.subheader("Log a session")
            with st.form("session"):
                d = st.date_input("Date", today, max_value=today)
                minutes = st.number_input("Minutes", 10, 300, 75, step=5)
                rpe = st.slider("How hard was it? (RPE 1 = very easy, 10 = maximal)", 1, 10, 6)
                stype = st.selectbox("Type", ["Skills", "Conditioning", "Match practice", "Match", "Gym", "Recovery"])
                if st.form_submit_button("Save session"):
                    data.add_session(con, aid, d, minutes, rpe, stype)
                    st.success(f"Saved: {minutes} min × RPE {rpe} = {int(core.session_load(minutes, rpe))} AU")
                    st.rerun()
            st.divider()
            st.subheader("Weekly wellness check-in (30 seconds)")
            with st.form("wellness"):
                d2 = st.date_input("Week of", today, key="wd")
                sleep = st.slider("Sleep quality this week", 1, 5, 3)
                sore = st.slider("Muscle soreness", 1, 5, 2)
                stress = st.slider("Academic / life stress", 1, 5, 2)
                if st.form_submit_button("Save check-in"):
                    data.add_wellness(con, aid, d2, sleep, sore, stress)
                    st.success("Saved.")
                    st.rerun()
            st.divider()
            st.subheader("Add a CIE / SEE / lab date")
            with st.form("event"):
                d3 = st.date_input("Date", today + timedelta(days=7), key="ed")
                kind = st.selectbox("Type", ["CIE", "SEE", "lab"])
                title = st.text_input("Title", "Data Structures CIE-3")
                if st.form_submit_button("Save date"):
                    data.add_event(con, aid, d3, kind, title)
                    st.success("Saved.")
                    st.rerun()

    for (lbl, render), t in zip(extra, tabs[len(labels):]):
        with t:
            run_feature_tab(lbl, render)


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
    st.subheader("Squad overview")
    st.caption("Every athlete's next tournament, academic clashes, and training-load zone in one table. "
               "Red rows need a conversation this week.")
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
    st.subheader("Tournaments")
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
    st.subheader("Import athlete roster")
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
    st.subheader("Users and roles")
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
        if b.form_submit_button("Restore RVCE draft wording"):
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


# ===================================================================
#                              ROUTING BY ROLE
# ===================================================================
ids = auth.visible_athlete_ids(con, user)
# Daily reminders run on the first page load of the day (the feature dedupes; a failure never blocks the page).
try:
    feature_call("reminders", "logic", "run_if_due", con)
except Exception as err:  # noqa: BLE001
    features.load_errors["reminders (daily run)"] = repr(err)
badge = feature_call("letters", "ui", "unread_badge", con, user)   # draws itself or returns text
if isinstance(badge, str) and badge:
    st.sidebar.info(badge)

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


if user.role == "athlete":
    a = data.athlete(con, user.athlete_id) if user.athlete_id else None
    if a is not None and not consent_gate():
        st.stop()
    if a is None:
        st.error("Your account isn't linked to an athlete record yet. Ask the sports office to import you in the roster.")
    else:
        st.sidebar.markdown(f"**{a['usn']}** · {a['dept']} · {a['sem']} sem · {a['sport']}")
        render_athlete(a)
else:
    feature_tabs = features.tabs_for(user.role)
    # The verification feature's "Upcoming absences" supersedes the basic class view; show it first.
    absences = [ft for ft in feature_tabs if ft[0] == "Upcoming absences"]
    sections = []
    if auth.can(user, "view_squad"):
        sections += [("Squad", lambda: render_squad(ids))]
    if auth.can(user, "view_class") and not auth.can(user, "view_squad"):
        if absences:
            sections += [(absences[0][0], lambda: run_feature_tab(*absences[0]))]
            feature_tabs = [ft for ft in feature_tabs if ft is not absences[0]]
        else:
            sections += [("Class", lambda: render_class_view(ids))]
    sections += [("Athlete detail", None)]
    if auth.can(user, "manage_tournaments"):
        sections += [("Tournaments", lambda: render_tournaments(ids))]
    if auth.can(user, "import_roster"):
        sections += [("Roster import", render_roster_import)]
    if auth.can(user, "manage_users"):
        sections += [("Users", render_users), ("Settings", render_settings)]
    tabs = st.tabs([s[0] for s in sections] + [lbl for lbl, _ in feature_tabs])
    for (label, fn), t in zip(sections, tabs):
        with t:
            if label == "Athlete detail":
                a = athlete_picker(ids, "detail_pick")
                if a is not None:
                    render_athlete(a, read_only=True)
            else:
                fn()
    for (lbl, render), t in zip(feature_tabs, tabs[len(sections):]):
        with t:
            run_feature_tab(lbl, render)
