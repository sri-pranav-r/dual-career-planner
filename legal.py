"""
The privacy policy and terms of use. Both are public (reachable from the login
page with ?doc=privacy or ?doc=terms) and also sit under Help once signed in.
The college's name comes from Settings, so the text never names a real college.
"""
from __future__ import annotations

PARAM = "doc"
TITLES = {"privacy": "Privacy policy", "terms": "Terms of use"}

PRIVACY = """\
This planner is run by the sports office of {college} for its student athletes, their coaches, the Physical \
Education Director and the faculty who approve their leave. This page says what it keeps, why, who can see it \
and what you can do about it.

#### What the planner keeps
- **Profile:** name, USN, department, semester, section, sport, proctor and phone number.
- **Academic schedule:** your class timetable and the dates of tests, labs and exams.
- **Sport:** the tournaments you are entered in, your leave letters and where each one is in its approval.
- **Training and wellbeing:** the training sessions you log (minutes and how hard they felt), weekly wellness \
check-ins (sleep, soreness, stress) and any injuries you or your coach record. This is health-related \
information, so it is kept away from faculty.
- **Sign-in:** your username and a scrambled (hashed) password.

#### Why
To find clashes between tournaments and classes or tests early, prepare the leave letter to the Dean of Student \
Affairs and track its approval, estimate attendance, and keep training load at a safe level. Figures may be used \
without names in project reports about the planner.

#### Who can see it
- **You** see everything about yourself.
- **Your coach** sees athletes in their sport. **The Physical Education Director** sees all sports.
- **Faculty** (class teacher, proctor, HoD) see the schedule, clashes, letters, attendance estimate and make-up \
requests of students in their department or class. They never see training, wellness or injuries.
- **The sports office admin** sees everything, to manage accounts and settings.

Nobody else is given access. The planner shows no adverts, sells no data and does not use tracking or analytics \
services.

#### Where it is stored
In a single database on the server the college runs the planner on. Letters you download are saved on your own \
device. Messages (SMS or WhatsApp reminders) are only sent if the college turns them on and you agree to them, and \
then only your phone number and the message text go to the messaging service.

#### How long
For as long as you are part of the sports programme, or until you ask for your data to be deleted.

#### Your choices
Taking part is voluntary and does not affect your marks, selection or letters. Athletes can download everything \
the planner has about them, withdraw consent or ask for deletion at any time on the Privacy and data page. \
Staff can ask the sports office admin.

#### Questions
Ask the sports office or the Physical Education Director of {college}.
"""

TERMS = """\
These terms apply to everyone who signs in to the planner at {college}.

#### What the planner is for
Helping student athletes balance sport and studies: spotting clashes, preparing leave letters, following their \
approval, estimating attendance and logging training.

#### What it is not
- **Not the official attendance record.** The college's own register, and the decisions of the Dean of Student \
Affairs, the Principal and faculty, always take precedence. Attendance figures here are estimates from the \
timetable and tournament dates.
- **Not an approval.** A letter only counts once the people it is addressed to have approved it. Marking a step \
in the planner records it; it does not replace their signature.
- **Not medical advice.** Training-load zones and day-by-day suggestions are general guidance. Follow your coach, \
physio or doctor, especially after an injury.

#### Your account
Keep your password to yourself and change the first one you are given. Only enter information that is true and \
that you are allowed to share. Do not upload other people's data without their permission.

#### Demo mode
While the planner is in demo mode, every athlete, tournament and number in it is made up for trying the app out. \
Demo accounts have public passwords and are removed when the admin switches to real data.

#### Availability and changes
The planner is a student project run by the college. It may be unavailable at times, and its features and these \
terms may change. Significant changes to what data is kept will be shown to athletes before they continue.
"""

DOCS = {"privacy": PRIVACY, "terms": TERMS}


def text(doc: str, college: str) -> str:
    return DOCS[doc].replace("{college}", college or "the college")


def render(con, doc: str, show_title: bool = True) -> None:
    import streamlit as st

    import settings
    if show_title:
        st.title(TITLES[doc])
    st.markdown(text(doc, settings.get(con, "college_name")))
