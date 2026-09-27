"""Roster import tests. Run: python3 -m pytest -q"""
import io
from datetime import date

import pandas as pd
import pytest

import auth
import data
import roster


@pytest.fixture
def con():
    c = data.connect(":memory:")
    data.seed(c, date(2026, 9, 28))
    yield c
    c.close()


SHEET = """Sl No,Reg No,Name of the Student,Branch,Semester,Sec,Game,Mentor,Mobile No,Email ID
1,1rv25cs200,Nisha Rao,Computer Science and Engineering,III,b,table tennis,Dr. Meena R,9000000001,n@x.edu
2,1RV25CS012,Aarav K.,CSE,3,A,Cricket,,,
3,BADUSN,Someone,CSE,3,A,Chess,,,
4,1RV25ME300,,ME,3,A,Football,,,
5,1RV25CS200,Nisha Dup,CSE,3,B,Chess,,,
6,1RV25EC301,Kiran,ECE,twelfth,A,Football,,,
"""


def test_parse_maps_loose_headers():
    df = roster.parse(SHEET.encode(), "roster.csv")
    assert {"usn", "name", "dept", "sem", "section", "sport", "proctor", "phone", "email"} <= set(df.columns)


def test_validate_normalises_and_reports_problems():
    rows, problems = roster.validate(roster.parse(SHEET.encode(), "roster.csv"))
    by_usn = {r["usn"]: r for r in rows}
    assert set(by_usn) == {"1RV25CS200", "1RV25CS012"}
    n = by_usn["1RV25CS200"]
    assert (n["dept"], n["sem"], n["section"], n["sport"]) == ("CSE", "3rd", "B", "Table Tennis")
    text = " ".join(problems)
    assert "BADUSN" in text and "blank name" in text and "appears twice" in text and "twelfth" in text


def test_missing_required_column():
    rows, problems = roster.validate(roster.parse(b"USN,Name\n1RV25CS001,A\n", "r.csv"))
    assert rows == [] and "Missing column" in problems[0]


def test_xlsx_roundtrip():
    buf = io.BytesIO()
    pd.DataFrame({"USN": ["1RV25IS400"], "Name": ["Tara"], "Department": ["ISE"], "Sem": ["5"],
                  "Sport": ["Kabaddi"]}).to_excel(buf, index=False)
    rows, problems = roster.validate(roster.parse(buf.getvalue(), "r.xlsx"))
    assert not problems and rows[0]["sem"] == "5th"


def test_apply_creates_and_updates_with_logins(con):
    rows, _ = roster.validate(roster.parse(SHEET.encode(), "roster.csv"))
    res = roster.apply(con, rows)
    assert (res["created"], res["updated"], res["accounts"]) == (1, 1, 1)
    # new athlete can sign in with USN and must change password
    u = auth.authenticate(con, "1RV25CS200", "1RV25CS200")
    assert u.role == "athlete" and u.must_change_password and u.section == "B"
    # existing athlete: name updated, blank proctor did not wipe the stored one
    a = data.athlete_by_usn(con, "1RV25CS012")
    assert a["name"] == "Aarav K." and a["proctor"] == "Dr. Meena R"


def test_reimport_is_idempotent(con):
    rows, _ = roster.validate(roster.parse(SHEET.encode(), "roster.csv"))
    roster.apply(con, rows)
    res = roster.apply(con, rows)
    assert (res["created"], res["accounts"]) == (0, 0)
    assert len(data.athletes(con)) == 11
