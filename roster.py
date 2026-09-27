"""
Roster import from the sports department spreadsheet (.csv or .xlsx).

parse(file)          -> DataFrame with canonical columns, whatever the sheet called them
validate(df)         -> (clean_rows: list[dict], problems: list[str])
apply(con, rows)     -> {"created": n, "updated": n, "accounts": n, "athlete_ids": [...]}

New athletes get a login: username = USN, first password = USN, and they are
asked to change it on first sign-in.
"""
from __future__ import annotations

import io
import re

import pandas as pd

import auth
import data
import hooks

# canonical column -> header spellings seen in department sheets (compared lower-case, no punctuation)
SYNONYMS = {
    "usn": ["usn", "usnno", "usnnumber", "university seat number", "regno", "registerno", "registrationnumber", "rollno"],
    "name": ["name", "studentname", "athletename", "fullname", "nameofthestudent"],
    "dept": ["dept", "department", "branch", "programme", "program"],
    "sem": ["sem", "semester", "currentsemester"],
    "section": ["section", "sec", "div", "division"],
    "sport": ["sport", "game", "discipline", "event", "games"],
    "proctor": ["proctor", "mentor", "counsellor", "counselor", "classteacher"],
    "phone": ["phone", "mobile", "mobileno", "phoneno", "contact", "contactno"],
    "email": ["email", "emailid", "mail", "emailaddress"],
}
REQUIRED = ("usn", "name", "dept", "sem", "sport")
USN_RE = re.compile(r"^\d[A-Z]{2}\d{2}[A-Z]{2,3}\d{3}$")   # VTU-style: 1RV25CS012

# Common ways the department writes a branch -> the short code the app uses.
DEPT_ALIASES = {
    "computer science": "CSE", "computer science and engineering": "CSE", "cs": "CSE", "cse": "CSE",
    "information science": "ISE", "information science and engineering": "ISE", "is": "ISE", "ise": "ISE",
    "electronics and communication": "ECE", "electronics and communication engineering": "ECE", "ec": "ECE", "ece": "ECE",
    "electrical and electronics": "EEE", "electrical and electronics engineering": "EEE", "ee": "EEE", "eee": "EEE",
    "mechanical": "ME", "mechanical engineering": "ME", "me": "ME",
    "civil": "CV", "civil engineering": "CV", "cv": "CV",
}


def _key(h) -> str:
    return re.sub(r"[^a-z]", "", str(h).lower())


def _sem(v) -> str:
    """'3', 3, 'III', 'third', '3rd sem' -> '3rd'."""
    s = str(v).strip().lower()
    roman = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8}
    words = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8}
    m = re.match(r"(\d+)", s)
    first = s.split()[0] if s else ""
    n = int(m.group(1)) if m else roman.get(first, words.get(first, 0))
    if not 1 <= n <= 8:
        return ""
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n, "th")


def parse(file, filename: str | None = None) -> pd.DataFrame:
    """file: path, bytes, or a file-like (e.g. Streamlit UploadedFile)."""
    if isinstance(file, (bytes, bytearray)):
        raw, name = bytes(file), filename or ""
    elif hasattr(file, "getvalue"):
        raw, name = file.getvalue(), filename or getattr(file, "name", "")
    else:
        raw, name = None, filename or str(file)
    name = name.lower()
    src = io.BytesIO(raw) if raw is not None else file
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(src, dtype=str)
    else:
        df = pd.read_csv(src, dtype=str)
    lookup = {_key(s): canon for canon, syns in SYNONYMS.items() for s in syns}
    rename = {}
    for col in df.columns:
        canon = lookup.get(_key(col))
        if canon and canon not in rename.values():
            rename[col] = canon
    df = df.rename(columns=rename)[list(rename.values())]
    return df.fillna("")


def validate(df: pd.DataFrame) -> tuple[list[dict], list[str]]:
    problems, rows, seen = [], [], set()
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        return [], [f"Missing column(s): {', '.join(missing)}. Found: {', '.join(df.columns) or 'none'}."]
    for i, r in df.iterrows():
        line = i + 2  # header is row 1 in the sheet
        rec = {c: str(r.get(c, "")).strip() for c in SYNONYMS}
        if not any(rec.values()):
            continue
        rec["usn"] = rec["usn"].upper().replace(" ", "")
        if not USN_RE.match(rec["usn"]):
            problems.append(f"Row {line}: USN '{rec['usn']}' doesn't look like a USN (e.g. 1RV25CS012).")
            continue
        if rec["usn"] in seen:
            problems.append(f"Row {line}: USN {rec['usn']} appears twice; kept the first.")
            continue
        blanks = [c for c in REQUIRED if not rec[c]]
        if blanks:
            problems.append(f"Row {line} ({rec['usn']}): blank {', '.join(blanks)}.")
            continue
        rec["dept"] = DEPT_ALIASES.get(rec["dept"].lower(), rec["dept"].upper())
        sem = _sem(rec["sem"])
        if not sem:
            problems.append(f"Row {line} ({rec['usn']}): semester '{rec['sem']}' not understood.")
            continue
        rec["sem"] = sem
        rec["section"] = rec["section"].upper()
        rec["sport"] = rec["sport"].strip().title()
        seen.add(rec["usn"])
        rows.append(rec)
    return rows, problems


def apply(con, rows: list[dict]) -> dict:
    created = updated = accounts = 0
    ids = []
    for rec in rows:
        aid, is_new = data.upsert_athlete(con, rec)
        ids.append(aid)
        created += is_new
        updated += not is_new
        a = data.athlete(con, aid)
        u = auth.user_by_username(con, rec["usn"])
        if u is None:
            con.commit()
            auth.create_user(con, rec["usn"], a["name"], "athlete", rec["usn"], athlete_id=aid, dept=a["dept"],
                             sem=a["sem"], section=a["section"], sport=a["sport"], must_change_password=True)
            accounts += 1
        else:
            auth.update_user(con, u.id, name=a["name"], dept=a["dept"], sem=a["sem"], section=a["section"], sport=a["sport"])
    con.commit()
    result = {"created": created, "updated": updated, "accounts": accounts, "athlete_ids": ids}
    hooks.emit(con, "roster_imported", result)
    return result


TEMPLATE_CSV = (
    "USN,Name,Department,Semester,Section,Sport,Proctor,Phone,Email\n"
    "1RV25CS012,Aarav Kulkarni,CSE,3,A,Cricket,Dr. Meena R,98XXXXXXXX,aarav@example.edu\n"
)
