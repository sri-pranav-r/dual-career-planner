"""
Test fixtures: made-up athletes, tournaments, training logs and logins.

Only the tests use this module. The app never imports it, so a deployed planner
starts empty and is set up by its first admin (see README).
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import auth
import data


# Every fixture login uses this password. It only ever exists in test databases.
PASSWORD = "fixture-pass-2468"
STAFF = [
    # username, name, role, title, dept, sport
    ("admin", "Sports Office Admin", "admin", "", None, None),
    ("ped", "Dr. Prakash (PED)", "coach", "ped", None, None),
    ("coach.cricket", "Coach Ramesh", "coach", "coach", None, "Cricket"),
    ("coach.football", "Coach Joseph", "coach", "coach", None, "Football"),
    ("proctor.cse", "Dr. Meena R", "faculty", "proctor", "CSE", None),
    ("hod.cse", "Dr. Ramakanth (HoD CSE)", "faculty", "hod", "CSE", None),
]


def seed(con, today: date | None = None):
    """Ten made-up athletes, six weeks of logs, five tournaments, a few clashes, and logins."""

    today = today or date.today()
    rng = random.Random(42)

    people = [
        ("Aarav Kulkarni", "1RV25CS012", "CSE", "3rd", "Cricket", "Dr. Meena R"),
        ("Diya Shetty", "1RV25CS034", "CSE", "3rd", "Basketball", "Dr. Meena R"),
        ("Rohan Hegde", "1RV25EC077", "ECE", "3rd", "Football", "Prof. Anil K"),
        ("Sneha Patil", "1RV25ME021", "ME", "3rd", "Athletics", "Prof. Ravi S"),
        ("Karthik Nair", "1RV24CS110", "CSE", "5th", "Cricket", "Dr. Suresh B"),
        ("Ananya Rao", "1RV25IS045", "ISE", "3rd", "Badminton", "Prof. Latha M"),
        ("Vikram Gowda", "1RV24CV019", "CV", "5th", "Football", "Prof. Ravi S"),
        ("Priya Menon", "1RV25CS088", "CSE", "3rd", "Basketball", "Dr. Meena R"),
        ("Arjun Reddy", "1RV24EE003", "EEE", "5th", "Athletics", "Prof. Anil K"),
        ("Meghana S", "1RV25CS101", "CSE", "3rd", "Badminton", "Dr. Meena R"),
    ]
    for p in people:
        con.execute("INSERT INTO athletes(name,usn,dept,sem,sport,proctor,phone,section) VALUES (?,?,?,?,?,?,?,?)",
                    (*p, "98XXXXXXXX", "A"))
    ids = [r[0] for r in con.execute("SELECT id FROM athletes ORDER BY id")]

    # Weekly timetable: 3rd-sem CSE-ish subjects; two labs a week
    subjects = ["Data Structures", "Digital Logic & CO", "Operating Systems", "Quantum Computing", "Linear Algebra"]
    for aid in ids:
        for wd in range(6):  # Mon-Sat
            for sub in rng.sample(subjects, 3):
                con.execute("INSERT INTO timetable(athlete_id,weekday,subject,kind) VALUES (?,?,?,?)", (aid, wd, sub, "class"))
        con.execute("INSERT INTO timetable(athlete_id,weekday,subject,kind) VALUES (?,?,?,?)", (aid, 1, "Data Structures Lab", "lab"))
        con.execute("INSERT INTO timetable(athlete_id,weekday,subject,kind) VALUES (?,?,?,?)", (aid, 3, "DLCO Lab", "lab"))

    # CIE dates: CIE-1 in ~2 weeks, CIE-2 in ~6 weeks, SEE in ~10 weeks
    cie1 = today + timedelta(days=(7 - today.weekday()) % 7 + 14)   # a Monday
    cie2 = cie1 + timedelta(days=28)
    see = cie1 + timedelta(days=56)
    for aid in ids:
        for i, sub in enumerate(subjects[:3]):
            con.execute("INSERT INTO events(athlete_id,date,kind,title) VALUES (?,?,?,?)", (aid, (cie1 + timedelta(days=i)).isoformat(), "CIE", f"{sub} CIE-1"))
            con.execute("INSERT INTO events(athlete_id,date,kind,title) VALUES (?,?,?,?)", (aid, (cie2 + timedelta(days=i)).isoformat(), "CIE", f"{sub} CIE-2"))
            con.execute("INSERT INTO events(athlete_id,date,kind,title) VALUES (?,?,?,?)", (aid, (see + timedelta(days=i * 2)).isoformat(), "SEE", f"{sub} SEE"))

    # Tournaments: one lands on CIE-1 week, one clean, one during SEE
    data.add_tournament(con, "VTU Inter-Collegiate Cricket (Zonal)", "Cricket", "Mysuru",
                   cie1 - timedelta(days=1), cie1 + timedelta(days=2), 1, 1, [ids[0], ids[4]])
    data.add_tournament(con, "Inter-Engineering Basketball Cup", "Basketball", "PES University, Bengaluru",
                   today + timedelta(days=9), today + timedelta(days=10), 0, 0, [ids[1], ids[7]])
    data.add_tournament(con, "AIU South Zone Football", "Football", "Kochi",
                   see + timedelta(days=1), see + timedelta(days=5), 2, 1, [ids[2], ids[6]])
    data.add_tournament(con, "Khelo India University Games - Athletics Trials", "Athletics", "Sree Kanteerava Stadium",
                   today + timedelta(days=21), today + timedelta(days=22), 0, 0, [ids[3], ids[8]])
    data.add_tournament(con, "College Open Badminton", "Badminton", "College Sports Complex",
                   today + timedelta(days=5), today + timedelta(days=5), 0, 0, [ids[5], ids[9]])

    # Six weeks of training logs. Athlete 0 spikes in the last week (ACWR > 1.5).
    for k, aid in enumerate(ids):
        base = rng.choice([60, 75, 90])
        for back in range(42, -1, -1):
            d = today - timedelta(days=back)
            if d.weekday() == 6 and rng.random() < 0.7:
                continue  # most Sundays off
            minutes = base + rng.randint(-15, 15)
            rpe = rng.randint(4, 7)
            if k == 0 and back <= 6:          # pre-tournament overload
                minutes, rpe = base + 60, rng.randint(8, 10)
            if k == 3 and 14 <= back <= 24:   # injury layoff then return
                continue
            con.execute("INSERT INTO sessions(athlete_id,date,minutes,rpe,type) VALUES (?,?,?,?,?)",
                        (aid, d.isoformat(), minutes, rpe, rng.choice(["Skills", "Conditioning", "Match practice"])))
        # Weekly wellness for 6 weeks
        for w in range(6, -1, -1):
            d = today - timedelta(days=7 * w)
            sleep = rng.randint(2, 5)
            soreness = rng.randint(1, 4) + (1 if (k == 0 and w == 0) else 0)
            stress = rng.randint(1, 4) + (1 if w <= 1 else 0)
            con.execute("INSERT INTO wellness(athlete_id,date,sleep,soreness,stress) VALUES (?,?,?,?,?)",
                        (aid, d.isoformat(), sleep, min(soreness, 5), min(stress, 5)))

    # Logins (athletes sign in with their USN as the username)
    for aid, p in zip(ids, people):
        auth.create_user(con, p[1], p[0], "athlete", PASSWORD, athlete_id=aid, dept=p[2], sem=p[3], section="A",
                         sport=p[4])
    for username, name, role, title, dept, sport in STAFF:
        auth.create_user(con, username, name, role, PASSWORD, title=title, dept=dept, sport=sport)
    con.commit()


def seeded_db(path, today: date | None = None) -> str:
    """Create an SQLite file at `path` holding the fixture records (for the headless UI tests)."""
    con = data.connect(str(path))
    seed(con, today)
    con.close()
    return str(path)
