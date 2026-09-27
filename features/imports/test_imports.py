"""Tests for timetable and exam calendar imports. Run from the planner folder: python3 -m pytest -q"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import core
import data
from features.imports import logic, parsers

SAMPLES = Path(__file__).parent / "samples"


@pytest.fixture
def con():
    c = data.connect(":memory:")
    data.seed(c, today=date(2026, 9, 28))
    return c


def _slots(res):
    return {(int(r.weekday), r.subject, r.kind) for r in res.rows.itertuples()}


# ---------------------------------------------------------------- timetable parsing

def test_grid_csv_with_legend_expands_codes_and_skips_breaks():
    res = parsers.parse_timetable_file(parsers.timetable_template_csv(), "tt.csv")
    s = _slots(res)
    assert (0, "Data Structures", "class") in s
    assert (0, "Operating Systems", "class") in s
    assert (1, "DS LAB (B1)", "lab") in s and (1, "DLCO LAB (B2)", "lab") in s
    assert not any(sub.upper() in ("BREAK", "LUNCH") for _, sub, _ in s)
    assert any("legend" in w for w in res.warnings)


def test_long_format_csv():
    csv = b"Day,Time,Subject,Type\nMonday,9:00,Data Structures,Theory\nTue,2:00,DS Lab,Lab\nWED,9:00,,\n"
    res = parsers.parse_timetable_file(csv, "tt.csv")
    assert _slots(res) == {(0, "Data Structures", "class"), (1, "DS Lab", "lab")}


def test_xlsx_sample_has_six_days_and_merged_labs():
    res = parsers.parse_timetable_file((SAMPLES / "timetable-cse-3a.xlsx").read_bytes(), "t.xlsx")
    assert set(res.rows["weekday"]) == {0, 1, 2, 3, 4, 5}
    assert (res.rows["kind"] == "lab").sum() == 4
    assert "Digital Logic & CO" in set(res.rows["subject"])
    # one row per subject per day, even when it repeats in the day
    assert not res.rows.duplicated().any()


@pytest.mark.skipif(not parsers.ocr_available(), reason="tesseract not installed")
def test_image_matches_sheet_for_clean_screenshot():
    img = parsers.parse_timetable_file((SAMPLES / "timetable-cse-3a.png").read_bytes(), "t.png")
    sheet = parsers.parse_timetable_file((SAMPLES / "timetable-cse-3a.xlsx").read_bytes(), "t.xlsx")
    assert _slots(img) == _slots(sheet)
    assert any("image" in w.lower() for w in img.warnings)


@pytest.mark.skipif(not parsers.ocr_available(), reason="tesseract not installed")
def test_image_survives_a_slightly_tilted_jpeg():
    from PIL import Image
    im = Image.open(SAMPLES / "timetable-cse-3a.png").convert("RGB").rotate(1.5, expand=True, fillcolor="white")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=60)
    img = parsers.parse_timetable_file(buf.getvalue(), "t.jpg")
    sheet = parsers.parse_timetable_file((SAMPLES / "timetable-cse-3a.xlsx").read_bytes(), "t.xlsx")
    assert len(_slots(img) & _slots(sheet)) >= 0.85 * len(_slots(sheet))


def test_unsupported_file_type():
    with pytest.raises(ValueError):
        parsers.parse_timetable_file(b"x", "tt.docx")


# ---------------------------------------------------------------- calendar parsing

@pytest.mark.parametrize("text,expected", [
    ("CIE-1 | 12/10/2026 to 15/10/2026", [(date(2026, 10, 12), date(2026, 10, 15))]),
    ("14-12-26", [(date(2026, 12, 14), date(2026, 12, 14))]),
    ("Quiz on 23rd Nov 2026", [(date(2026, 11, 23), date(2026, 11, 23))]),
    ("SEE 14 - 24 December 2026", [(date(2026, 12, 14), date(2026, 12, 24))]),
    ("CIE 3rd Nov to 6th Nov", [(date(2026, 11, 3), date(2026, 11, 6))]),
    ("12.10.2026", [(date(2026, 10, 12), date(2026, 10, 12))]),
])
def test_find_dates(text, expected):
    spans, _ = parsers.find_dates(text, 2026)
    assert spans == expected


def test_event_kind():
    assert parsers.event_kind("SEE (Theory)") == "SEE"
    assert parsers.event_kind("Semester End Examination") == "SEE"
    assert parsers.event_kind("CIE - II") == "CIE"
    assert parsers.event_kind("Lab CIE") == "lab"
    assert parsers.event_kind("Internal Assessment Test 1") == "CIE"
    assert parsers.event_kind("Dasara Holidays") is None
    assert parsers.event_kind("Make-up test for CIE-1") is None


def test_pdf_sample_gives_cie_see_lab_and_drops_holidays():
    res = parsers.parse_exam_calendar_file((SAMPLES / "exam-calendar-odd-sem.pdf").read_bytes(), "cal.pdf")
    rows = {(r.date, r.kind, r.title) for r in res.rows.itertuples()}
    assert (date(2026, 10, 12), "CIE", "CIE-1") in rows
    assert (date(2026, 11, 9), "lab", "Lab CIE") in rows
    assert (date(2026, 11, 23), "CIE", "Quiz-2") in rows
    # subject-wise rows replace the generic CIE-2 week
    assert (date(2026, 11, 16), "CIE", "Data Structures CIE-2") in rows
    assert not any(t == "CIE-2" for _, _, t in rows)
    # SEE range skips the Sunday in the middle
    see = res.rows[res.rows["kind"] == "SEE"]["date"]
    assert date(2026, 12, 14) in set(see) and date(2026, 12, 20) not in set(see)
    assert len(see) == 10
    # holidays and non-exam events are reported, not imported
    assert not any(date(2026, 10, 19) == d for d, _, _ in rows)
    assert any("Holidays" in s for s in res.skipped)


def test_heading_gives_kind_to_rows_under_it():
    lines = ["INTERNAL ASSESSMENT TEST - II", "Date | Subject", "03/11/2026 | Linear Algebra",
             "04/11/2026 | Operating Systems", "Ganesh Chaturthi holiday", "05/11/2026 | Sports Day"]
    res = parsers.parse_calendar_lines(lines)
    assert list(res.rows["title"]) == ["Linear Algebra CIE-2", "Operating Systems CIE-2"]
    assert "05/11/2026 | Sports Day" in res.skipped


def test_missing_year_uses_default_and_warns():
    res = parsers.parse_calendar_lines(["CIE-1 | 12 Oct to 14 Oct"], default_year=None)
    assert len(res.rows) == 3
    res2 = parsers.parse_calendar_lines(["CIE-1 | 12 Oct to 14 Oct"], default_year=2027)
    assert res2.rows["date"].iloc[0] == date(2027, 10, 12)


def test_tidy_sheet_calendar():
    csv = b"Date,Type,Title\n12/10/2026,CIE,Data Structures CIE-1\n2026-12-14,SEE,DS SEE\n,,\n"
    res = parsers.parse_exam_calendar_file(csv, "cal.csv")
    assert list(res.rows["kind"]) == ["CIE", "SEE"]
    assert res.rows["date"].iloc[0] == date(2026, 10, 12)


def test_scanned_pdf_without_text_warns():
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.rect(10, 10, 100, 100)
    c.save()
    res = parsers.parse_exam_calendar_file(buf.getvalue(), "scan.pdf")
    assert res.rows.empty and "scan" in res.warnings[0]


# ---------------------------------------------------------------- saving

def test_athlete_timetable_replaces_own_only(con):
    a, b = [int(i) for i in data.athletes(con)["id"][:2]]
    before_b = data.timetable(con, b)
    res = parsers.parse_timetable_file(parsers.timetable_template_csv(), "tt.csv")
    n = logic.save_athlete_timetable(con, a, res.rows)
    assert n == len(res.rows) and len(data.timetable(con, a)) == n
    pd.testing.assert_frame_equal(data.timetable(con, b), before_b)
    assert logic.import_history(con).iloc[0]["kind"] == "timetable"


def test_class_timetable_reaches_every_athlete_in_class(con):
    cse3 = data.athletes_in_class(con, "CSE", "3rd")
    other = data.athletes(con)
    other = other[~other["id"].isin(cse3["id"])].iloc[0]
    res = parsers.parse_timetable_file((SAMPLES / "timetable-cse-3a.xlsx").read_bytes(), "t.xlsx")
    r = logic.save_class_timetable(con, "CSE", "3rd", "A", res.rows, "t.xlsx")
    assert r["athletes"] == len(cse3) > 1
    for aid in cse3["id"]:
        assert len(data.timetable(con, aid)) == r["slots"]
    assert len(data.timetable(con, other["id"])) != r["slots"]


def test_exam_calendar_applies_to_class_and_is_idempotent(con):
    res = parsers.parse_exam_calendar_file((SAMPLES / "exam-calendar-odd-sem.pdf").read_bytes(), "cal.pdf")
    ids = [int(i) for i in data.athletes_in_class(con, "CSE", "3rd")["id"]]
    before = len(data.events(con, ids[0]))
    r = logic.apply_exam_calendar(con, [("CSE", "3rd", None)], res.rows, "cal.pdf")
    assert r == {"events": len(res.rows), "athletes": len(ids)}
    after = len(data.events(con, ids[0]))
    assert after == before + len(res.rows)
    logic.apply_exam_calendar(con, [("CSE", "3rd", None)], res.rows, "cal.pdf")
    assert len(data.events(con, ids[0])) == after
    assert len(logic.class_events(con, "CSE", "3rd", "A")) == len(res.rows)


def test_imported_dates_feed_clash_detection(con):
    aid = int(data.athletes_in_class(con, "CSE", "3rd")["id"].iloc[0])
    logic.apply_exam_calendar(con, [("CSE", "3rd", None)],
                              [{"date": date(2027, 1, 12), "kind": "CIE", "title": "Maths CIE-3"}])
    t = {"name": "Test Cup", "start_date": date(2027, 1, 11), "end_date": date(2027, 1, 13),
         "travel_before": 0, "travel_after": 0}
    clashes = core.find_clashes(t, data.timetable(con, aid), data.events(con, aid))
    assert any(c.kind == "CIE" and c.title == "Maths CIE-3" for c in clashes)


def test_new_athlete_from_roster_gets_class_calendar_and_timetable(con):
    res = parsers.parse_timetable_file(parsers.timetable_template_csv(), "tt.csv")
    logic.save_class_timetable(con, "CSE", "3rd", None, res.rows)
    logic.apply_exam_calendar(con, [("CSE", "3rd", None)],
                              [{"date": date(2026, 10, 12), "kind": "CIE", "title": "CIE-1"}])
    aid, _ = data.upsert_athlete(con, {"name": "New Athlete", "usn": "1RV25CS199", "dept": "CSE",
                                       "sem": "3rd", "section": "B", "sport": "Chess"})
    logic.on_roster_imported(con, {"athlete_ids": [aid], "created": 1, "updated": 0})
    ev = data.events(con, aid)
    assert (ev["title"] == "CIE-1").any()
    assert len(data.timetable(con, aid)) == len(res.rows)


def test_feature_registers():
    from features.imports import FEATURE
    assert FEATURE["name"] == "imports"
    assert {t["render"] for t in FEATURE["tabs"]} == {"ui:render_timetable_import", "ui:render_exam_calendar_import"}
    assert FEATURE["hooks"]["roster_imported"] == "logic:on_roster_imported"
    assert FEATURE["hooks"]["athlete_deleted"] == "logic:on_athlete_deleted"


def test_athlete_deleted_removes_their_upload_log_only(con):
    a, b = [int(i) for i in data.athletes_in_class(con, "CSE", "3rd")["id"][:2]]
    rows = parsers.parse_timetable_file(parsers.timetable_template_csv(), "tt.csv").rows
    logic.save_athlete_timetable(con, a, rows, "aarav-timetable.png")
    logic.save_athlete_timetable(con, b, rows, "diya-timetable.png")
    logic.save_class_timetable(con, "CSE", "3rd", None, rows, "cse3.xlsx")
    logic.apply_exam_calendar(con, [("CSE", "3rd", None)],
                              [{"date": date(2026, 10, 12), "kind": "CIE", "title": "CIE-1"}], "cal.pdf")
    logic.on_athlete_deleted(con, {"athlete_id": a})
    names = set(logic.import_history(con)["source_name"])
    assert "aarav-timetable.png" not in names
    assert {"diya-timetable.png", "cse3.xlsx", "cal.pdf"} <= names
    assert not logic.class_timetable(con, "CSE", "3rd", None).empty
    assert not logic.class_events(con, "CSE", "3rd").empty


def test_athlete_deleted_upgrades_old_log_table():
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.executescript("""CREATE TABLE imports_log (id INTEGER PRIMARY KEY, kind TEXT, dept TEXT, sem TEXT,
        section TEXT, source_name TEXT, rows INTEGER, athletes INTEGER, imported_by INTEGER, imported_at TEXT);
        CREATE TABLE users (id INTEGER PRIMARY KEY, athlete_id INTEGER);
        INSERT INTO users VALUES (7, 3);
        INSERT INTO imports_log(kind, source_name, athletes, imported_by) VALUES ('timetable', 'mine.png', 1, 7);
        INSERT INTO imports_log(kind, source_name, athletes, imported_by) VALUES ('timetable', 'class.xlsx', 12, 7);""")
    logic.on_athlete_deleted(c, {"athlete_id": 3})
    assert [r[0] for r in c.execute("SELECT source_name FROM imports_log")] == ["class.xlsx"]
