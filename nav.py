"""
Navigation: which pages each role gets, how they are grouped, and the one-line
explanation at the top of every page.

A page is made of one or more parts. A part is either a core screen drawn by
app.py ("core:<name>") or a feature tab by its label ("feature:<label>", see
INTERFACES.md). A page shows only the parts the signed-in role can use, as
sub-tabs when there are several. A page with no usable parts is left out.
Any feature tab that no page claims is still shown, under "More", so a new
feature is never unreachable.

The page in view is kept in the URL (?p=<key>), next to the login token.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PARAM = "p"


@dataclass
class Page:
    key: str
    title: str
    icon: str                                # a Material icon, e.g. ":material/home:"
    blurb: str                               # what the page shows and what to do there
    parts: list[tuple[str, str]]             # [(sub-tab label, "core:<name>" | "feature:<label>")]
    section: str = ""
    roles: set[str] = field(default_factory=lambda: {"athlete", "coach", "faculty", "admin"})


def _p(key, title, icon, blurb, parts, roles=None):
    if isinstance(parts, str):
        parts = [(title, parts)]
    return Page(key, title, icon, blurb, parts, roles=set(roles) if roles else {"athlete", "coach", "faculty", "admin"})


# --------------------------------------------------------------------------- athlete
ATHLETE = [
    ("Today", [
        _p("home", "Home", ":material/home:",
           "Your week at a glance: what needs doing, your next tournament, clashes and training load.", "core:athlete_home"),
        _p("plan", "Next 14 days", ":material/event_note:",
           "A day-by-day plan that balances tests, travel and training. Tick whether you followed yesterday's advice.",
           "feature:Next 14 days"),
        _p("calendar", "Calendar", ":material/calendar_month:",
           "Classes, tests, tournaments and training on one month view. Pick a day to see what's on it.",
           "feature:Calendar"),
        _p("inbox", "Notifications", ":material/notifications:",
           "Messages about your tournaments and letters, such as a date change or a signature.",
           "feature:Notifications"),
    ]),
    ("Academics", [
        _p("clashes", "Clashes", ":material/warning:",
           "Every class, lab, CIE and SEE that falls inside a tournament window, travel days included.",
           "core:clashes"),
        _p("letters", "Exemption letters", ":material/description:",
           "Download the pre-filled exemption letter, then follow it through the PED, proctor and HoD sign-off.",
           [("Download", "core:letter"), ("Sign-off progress", "feature:Letter status"),
            ("Faculty decisions", "feature:Letter approvals")]),
        _p("makeups", "Make-up tests", ":material/assignment_late:",
           "Ask for a make-up for each test a tournament makes you miss, and see when it's scheduled.",
           "feature:Make-up tests"),
        _p("attendance", "Attendance", ":material/percent:",
           "Your projected attendance once tournament days are counted, against the college's minimum.",
           "feature:Attendance risk"),
        _p("timetable", "Timetable and tests", ":material/table_view:",
           "Import your weekly timetable and add CIE, SEE or lab dates, so clashes are found automatically.",
           [("Timetable", "feature:Import timetable"), ("Add a test date", "core:add_test")]),
    ]),
    ("Training and health", [
        _p("log", "Log training", ":material/edit_note:",
           "Record each session (minutes and how hard it felt) and a 30-second weekly wellness check-in.",
           "core:log"),
        _p("load", "Training load", ":material/monitoring:",
           "Your daily load and the acute-to-chronic ratio (ACWR) that signals injury risk, plus wellness trends.",
           [("Load", "core:load"), ("Wellness", "core:wellness")]),
        _p("injuries", "Injuries", ":material/healing:",
           "Log an injury and follow a safe return-to-play ramp. Your coach sees these; faculty don't.",
           "feature:Injuries"),
        _p("taper", "Taper plan", ":material/trending_down:",
           "How to ease training before your next tournament so you arrive fresh.", "feature:Taper plan"),
    ]),
    ("You", [
        _p("summary", "Semester summary", ":material/summarize:",
           "Tournaments, missed classes, letters and training for the semester, as a downloadable report.",
           "feature:Semester summary"),
        _p("reminders", "Reminder settings", ":material/phone_iphone:",
           "Choose whether reminders also reach your phone by WhatsApp or SMS.", "feature:Reminder settings"),
        _p("privacy", "Privacy and data", ":material/shield_person:",
           "The data notice you agreed to, a download of everything stored about you, and deletion.",
           "feature:Privacy & data"),
        _p("help", "Help", ":material/help:",
           "What every page does and how the numbers are worked out.", "core:help"),
    ]),
]

# --------------------------------------------------------------------------- staff (coach, PED, faculty, admin)
STAFF = [
    ("Overview", [
        _p("home", "Home", ":material/home:",
           "What needs your attention today, with a shortcut to each item.", "core:staff_home"),
        _p("squad", "Squad", ":material/groups:",
           "Every athlete's next tournament, test clashes and training-load zone. Red rows need a conversation this week.",
           "core:squad", roles={"coach", "admin"}),
        _p("absences", "Upcoming absences", ":material/event_busy:",
           "Students away for sport in the coming weeks and the tests they will miss, to plan make-ups.",
           [("Upcoming absences", "feature:Upcoming absences"), ("Class list", "core:class_view")],
           roles={"faculty", "admin"}),
        _p("detail", "Athlete detail", ":material/person_search:",
           "Pick one athlete to see their clashes, letter, training load and wellness.", "core:detail"),
        _p("calendar", "Calendar", ":material/calendar_month:",
           "One athlete's classes, tests, tournaments and training on a month view.", "feature:Calendar"),
    ]),
    ("Tournaments and letters", [
        _p("tournaments", "Tournaments", ":material/emoji_events:",
           "Add tournaments and change dates or entries. Entered athletes' clashes update and they're notified.",
           "core:tournaments", roles={"coach", "admin"}),
        _p("selection", "Squad selection", ":material/how_to_reg:",
           "Choose who to enter with each athlete's clashes and load side by side, cleanest picks first.",
           "feature:Squad selection"),
        _p("letters", "Letters", ":material/description:",
           "Where every exemption letter is in the sign-off chain, with the step you can take next.",
           [("Sign-off board", "feature:Letter status"), ("Faculty decisions", "feature:Letter approvals"),
            ("Bulk download", "feature:Bulk letters")]),
        _p("reminders", "Reminders", ":material/notifications_active:",
           "Tournament reminders and logging nudges sent to athletes. Nothing leaves the app unless it's switched on.",
           "feature:Reminders"),
    ]),
    ("Academics", [
        _p("makeups", "Make-up tests", ":material/assignment_late:",
           "Make-up requests for tests missed for sport. Schedule or reject each one.", "feature:Make-up tests"),
        _p("attendance", "Attendance risk", ":material/percent:",
           "Who could fall below the attendance minimum once tournament days are counted.",
           "feature:Attendance risk"),
        _p("exam_import", "Import exam calendar", ":material/upload_file:",
           "Upload the college calendar once a semester; CIE, SEE and lab dates go to every student in scope.",
           "feature:Import exam calendar"),
        _p("timetable", "Import timetable", ":material/table_view:",
           "Upload a class timetable for a whole class or one student.", "feature:Import timetable"),
    ]),
    ("Training and health", [
        _p("plan", "Next 14 days", ":material/event_note:",
           "One athlete's day-by-day plan balancing tests, travel and training, with the rule behind each tip.",
           "feature:Next 14 days"),
        _p("teamload", "Team load report", ":material/monitoring:",
           "This week's load per athlete: who spiked and who stopped logging.", "feature:Team load report"),
        _p("injuries", "Injuries", ":material/healing:",
           "Who is injured or returning, and the squad's injury pattern by month and body part.",
           "feature:Injuries"),
        _p("taper", "Taper plans", ":material/trending_down:",
           "Taper targets for athletes leaving for a tournament in the next 14 days.", "feature:Taper plan"),
        _p("pilot", "Pilot results", ":material/science:",
           "Whether athletes follow the Next 14 days suggestions, by rule and by athlete.",
           "feature:Pilot results"),
    ]),
    ("Reports and setup", [
        _p("summary", "Semester summary", ":material/summarize:",
           "Semester totals per athlete: tournaments, missed classes, letters and training.",
           "feature:Semester summary"),
        _p("export", "Export", ":material/download:",
           "Download everything you're allowed to see as CSV files.", "feature:Export"),
        _p("roster", "Roster import", ":material/group_add:",
           "Upload the sports roster spreadsheet. New athletes get a login with their USN as first password.",
           "core:roster"),
        _p("users", "Users", ":material/manage_accounts:",
           "Staff accounts and roles: add, change or remove them.", "core:users"),
        _p("settings", "Settings", ":material/settings:",
           "College rules, letter wording and the app address used in QR codes.", "core:settings"),
        _p("privacy", "Privacy and consent", ":material/shield_person:",
           "Which athletes agreed to the data notice, and deletion requests to carry out.",
           "feature:Privacy & data"),
        _p("help", "Help", ":material/help:",
           "What every page does and how the numbers are worked out.", "core:help"),
    ]),
]

MORE = "More"


def catalog(role: str):
    return ATHLETE if role == "athlete" else STAFF


def build(role: str, feature_tabs: dict, core_ok) -> list[Page]:
    """
    The pages for a role, in menu order, with parts resolved.
    feature_tabs: {label: render_fn} for the role (features.tabs_for).
    core_ok(name) -> bool: whether a core part is available to this user.
    Each returned Page's parts are [(label, kind, target)] with kind "core" or "feature".
    """
    pages, claimed = [], set()
    for section, items in catalog(role):
        for p in items:
            if role not in p.roles:
                continue
            parts = []
            for label, src in p.parts:
                kind, name = src.split(":", 1)
                if kind == "feature" and name in feature_tabs:
                    parts.append((label, "feature", name))
                    claimed.add(name)
                elif kind == "core" and core_ok(name):
                    parts.append((label, "core", name))
            if parts:
                pages.append(Page(p.key, p.title, p.icon, p.blurb, parts, section, p.roles))
    for label in feature_tabs:
        if label not in claimed:
            key = "x-" + "".join(c if c.isalnum() else "-" for c in label.lower())
            pages.append(Page(key, label, ":material/extension:", f"{label}.", [(label, "feature", label)], MORE))
    return pages


def sections(pages: list[Page]) -> list[tuple[str, list[Page]]]:
    out: list[tuple[str, list[Page]]] = []
    for p in pages:
        if not out or out[-1][0] != p.section:
            out.append((p.section, []))
        out[-1][1].append(p)
    return out


def current(pages: list[Page], params) -> Page:
    key = params.get(PARAM)
    for p in pages:
        if p.key == key:
            return p
    return pages[0]


# --------------------------------------------------------------------------- drawing (Streamlit)
CSS = """<style>
.block-container { padding-top: 2.5rem; }
/* Sidebar menu: compact, left-aligned rows; the current page is highlighted. */
.st-key-sidenav { gap: 0.05rem; }
.st-key-sidenav button { justify-content: flex-start !important; padding: 0.15rem 0.7rem !important;
  min-height: 2.1rem; border-radius: 0.5rem; }
.st-key-sidenav button > div { justify-content: flex-start !important; }
.st-key-sidenav button p { font-size: 0.93rem; }
.st-key-sidenav button[data-testid="stBaseButton-tertiary"]:hover { background: rgba(128,128,128,0.12); }
.st-key-sidenav button[data-testid="stBaseButton-secondary"] { background: rgba(255,75,75,0.10);
  border-color: transparent; color: rgb(214,40,40); font-weight: 600; }
.nav-section { font-size: 0.72rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
  opacity: 0.55; margin: 0.9rem 0 0.1rem 0.7rem; }
[data-testid="stMainBlockContainer"] h3 { font-size: 1.3rem; }
[data-testid="stMainBlockContainer"] h4 { font-size: 1.1rem; }
[class*="st-key-att_"] { padding: 0.55rem 0.9rem; }
.page-kicker { font-size: 0.75rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; opacity: 0.55;
  margin-bottom: -0.9rem; }
.page-blurb { opacity: 0.75; margin: -0.6rem 0 0.4rem 0; }
/* Phones: the sidebar is tucked away, so a "Go to" menu sits at the top of the page instead. */
.st-key-topnav { display: none; }
@media (max-width: 640px) {
  .st-key-topnav { display: block; }
  .block-container { padding-top: 3.5rem; }
  [class*="st-key-att_"] [data-testid="stColumn"], [class*="st-key-step_"] [data-testid="stColumn"] {
    flex: 1 1 100% !important; min-width: 100% !important; }
}
</style>"""


def go(key: str) -> None:
    import streamlit as st
    st.query_params[PARAM] = key


def draw_sidebar(parent, pages: list[Page], here: Page) -> None:
    """The grouped menu, drawn into `parent` (a container in the sidebar)."""
    import streamlit as st
    with parent.container(key="sidenav"):
        for section, items in sections(pages):
            st.html(f'<div class="nav-section">{section}</div>')
            for p in items:
                st.button(p.title, key=f"nav_{p.key}", icon=p.icon, on_click=go, args=(p.key,),
                          type="secondary" if p.key == here.key else "tertiary", width="stretch")


def draw_topnav(pages: list[Page], here: Page) -> None:
    import streamlit as st

    def pick():
        go(st.session_state[f"topnav_{here.key}"])

    keys = [p.key for p in pages]
    names = {p.key: f"{p.section} · {p.title}" for p in pages}
    with st.container(key="topnav"):
        st.selectbox("Go to", keys, index=keys.index(here.key), format_func=names.get,
                     key=f"topnav_{here.key}", on_change=pick)


def draw_header(page: Page, title: str | None = None) -> None:
    """The kicker (menu section), the page title and its one-line explanation."""
    import html

    import streamlit as st
    if page.section:
        st.html(f'<div class="page-kicker">{html.escape(page.section)}</div>')
    st.header(title or page.title, anchor=False)
    st.html(f'<div class="page-blurb">{html.escape(page.blurb)}</div>')
