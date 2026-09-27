"""
Letter verification: signed QR links, faculty approve / reject, and the
department's view of upcoming sport absences. No streamlit import here.

How a letter is verified
  1. When a letter is downloaded, `add_qr_to_letter` stamps it with a QR code
     and a short link:  <base>/?verify=<token>
  2. The token names the athlete, the tournament and the away window printed on
     the letter, and carries an HMAC signature made with a per-database secret.
     Changing any of those (or inventing a letter) breaks the signature.
  3. `verify_token` checks the signature, then re-reads the live records: is the
     athlete still entered, has the PED signed, have the dates moved since the
     letter was printed. The page shows the PED-confirmed data, not the paper.
  4. A signed-in proctor / class teacher / HoD approves or rejects with
     `record_decision`, which also moves the letter status in features/letters
     and emits `verification_decided` so the athlete and coach see it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

import auth
import core
import hooks

from features.letters import logic as letters

DEFAULT_BASE_URL = "http://localhost:8501"
TOKEN_VERSION = "v1"

# The letter is addressed "Through: Class teacher / Proctor", so either signs.
REVIEWER_TITLES = {"proctor": "Proctor", "teacher": "Class teacher", "hod": "Head of Department", "admin": "Admin"}
# The stage a letter must be at before each title can approve it.
STAGE_BEFORE = {"proctor": "ped_signed", "teacher": "ped_signed", "hod": "proctor_signed"}


# ---------------------------------------------------------------------------
# Letter status: stages and the rejected flag come from features/letters
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def letter_status(con, athlete_id: int, tournament_id: int) -> str:
    """Display label from features/letters: 'Not drafted', 'Rejected', 'Needs redo' or the stage."""
    return letters.status_label(letters.find_letter(con, athlete_id, tournament_id))


def _stage_at_least(letter: dict | None, stage: str) -> bool:
    return letter is not None and letters.STAGES.index(letter["stage"]) >= letters.STAGES.index(stage)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def _secret(con) -> bytes:
    env = os.environ.get("PLANNER_SECRET")
    if env:
        return env.encode()
    row = con.execute("SELECT secret FROM verification_keys WHERE id=1").fetchone()
    if row is None:
        con.execute("INSERT OR IGNORE INTO verification_keys(id, secret, created_at) VALUES (1, ?, ?)",
                    (secrets.token_hex(32), datetime.now().isoformat(timespec="seconds")))
        con.commit()
        row = con.execute("SELECT secret FROM verification_keys WHERE id=1").fetchone()
    return row[0].encode()


def _sign(con, body: str) -> str:
    mac = hmac.new(_secret(con), f"{TOKEN_VERSION}|{body}".encode(), hashlib.sha256).digest()[:12]
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def away_window(t: dict) -> tuple[date, date]:
    """First and last day away, including travel days."""
    first = _d(t["start_date"]) - timedelta(days=int(t.get("travel_before") or 0))
    last = _d(t["end_date"]) + timedelta(days=int(t.get("travel_after") or 0))
    return first, last


def make_token(con, athlete_id: int, tournament_id: int) -> str:
    """Token for the letter as it would be printed now: athlete, tournament, away window."""
    t = _tournament(con, tournament_id)
    if t is None:
        raise ValueError(f"No tournament {tournament_id}")
    first, last = away_window(t)
    body = f"{int(athlete_id)}-{int(tournament_id)}-{first:%Y%m%d}-{last:%Y%m%d}"
    return f"{body}.{_sign(con, body)}"


def _configured_base_url(con) -> str | None:
    """settings.base_url (core v3) when present, else None."""
    try:
        import settings
        return settings.base_url(con) or None
    except (ImportError, AttributeError):
        return None


def verify_url(token: str, base_url: str | None = None) -> str:
    base = (base_url or os.environ.get("PLANNER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/?verify={token}"


def parse_token(con, token: str) -> dict | None:
    """Return {athlete_id, tournament_id, first, last} if the signature is valid, else None."""
    m = re.fullmatch(r"(\d+)-(\d+)-(\d{8})-(\d{8})\.([A-Za-z0-9_-]+)", (token or "").strip())
    if not m:
        return None
    body = token.strip().rsplit(".", 1)[0]
    if not hmac.compare_digest(_sign(con, body), m.group(5)):
        return None
    try:
        first = datetime.strptime(m.group(3), "%Y%m%d").date()
        last = datetime.strptime(m.group(4), "%Y%m%d").date()
    except ValueError:
        return None
    return {"athlete_id": int(m.group(1)), "tournament_id": int(m.group(2)), "first": first, "last": last}


# ---------------------------------------------------------------------------
# Reading core tables
# ---------------------------------------------------------------------------

def _row(con, sql, params) -> dict | None:
    cur = con.execute(sql, params)
    r = cur.fetchone()
    return dict(zip([c[0] for c in cur.description], r)) if r else None


def _tournament(con, tid):
    return _row(con, "SELECT * FROM tournaments WHERE id=?", (int(tid),))


def _athlete(con, aid):
    return _row(con, "SELECT * FROM athletes WHERE id=?", (int(aid),))


def _entered(con, aid, tid) -> bool:
    return con.execute("SELECT 1 FROM entries WHERE athlete_id=? AND tournament_id=?",
                       (int(aid), int(tid))).fetchone() is not None


def _user_label(con, user_id) -> str | None:
    if user_id is None:
        return None
    try:
        r = _row(con, "SELECT name, role, title, sport FROM users WHERE id=?", (int(user_id),))
    except Exception:  # noqa: BLE001 - users table may not exist on an old db
        return None
    if not r:
        return None
    if r["role"] == "coach" and not r.get("sport"):
        return f"{r['name']} (Physical Education Director)"
    return f"{r['name']} ({r.get('title') or r['role']})"


def _academic_frames(con, aid):
    tt = pd.read_sql_query("SELECT weekday, subject, kind FROM timetable WHERE athlete_id=?", con, params=(int(aid),))
    ev = pd.read_sql_query("SELECT date, kind, title FROM events WHERE athlete_id=?", con, params=(int(aid),))
    ev["date"] = pd.to_datetime(ev["date"]).dt.date
    return tt, ev


def missed_items(con, aid, t: dict) -> list[core.Clash]:
    tt, ev = _academic_frames(con, aid)
    tdict = {**t, "start_date": _d(t["start_date"]), "end_date": _d(t["end_date"]),
             "travel_before": int(t.get("travel_before") or 0), "travel_after": int(t.get("travel_after") or 0)}
    return core.find_clashes(tdict, tt, ev)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass
class Verification:
    valid: bool                       # signature ok and the athlete / tournament exist
    reason: str                       # one line for the page header
    athlete: dict | None = None
    tournament: dict | None = None
    entered: bool = False             # athlete is on the PED's entry list now
    ped_confirmed: bool = False       # letter status has reached "PED signed"
    created_by: str | None = None     # who entered the tournament in the system
    letter_window: tuple | None = None
    current_window: tuple | None = None
    dates_changed: bool = False
    missed: list = field(default_factory=list)
    status: str | None = None
    letter: dict | None = None        # features/letters row, None if never drafted
    decisions: list = field(default_factory=list)

    @property
    def warnings(self) -> list[str]:
        w = []
        if self.valid and not self.entered:
            w.append("This athlete is no longer on the entry list for this tournament.")
        if self.valid and self.dates_changed:
            f, l = self.current_window
            w.append(f"The tournament dates changed after this letter was printed. The confirmed away "
                     f"window is now {f:%d %b %Y} to {l:%d %b %Y}. Ask the athlete for a fresh letter.")
        if self.valid and self.letter is None:
            w.append("The athlete has not drafted this letter in the planner.")
        elif self.valid and self.letter["rejected"]:
            w.append("This letter was rejected. The athlete must fix it and download a fresh one.")
        elif self.valid and self.letter["needs_redo"] and not self.dates_changed:
            w.append("The tournament changed after this letter was drafted. Ask the athlete for a fresh letter.")
        elif self.valid and self.entered and not self.ped_confirmed:
            w.append("The Physical Education Director has not signed this letter in the system yet.")
        return w

    @property
    def approvable(self) -> bool:
        return (self.valid and self.entered and not self.dates_changed and self.letter is not None
                and not self.letter["needs_redo"] and not self.letter["rejected"])


def verify_token(con, token: str) -> Verification:
    p = parse_token(con, token)
    if p is None:
        return Verification(False, "This link is not genuine. The letter may have been edited or made up.")
    a, t = _athlete(con, p["athlete_id"]), _tournament(con, p["tournament_id"])
    if a is None or t is None:
        return Verification(False, "The athlete or tournament on this letter no longer exists.")
    now = away_window(t)
    letter = letters.find_letter(con, a["id"], t["id"])
    v = Verification(
        valid=True, reason="Genuine letter issued by the Dual-Career Planner.",
        athlete=a, tournament=t, entered=_entered(con, a["id"], t["id"]),
        ped_confirmed=_stage_at_least(letter, "ped_signed"), created_by=_user_label(con, t.get("created_by")),
        letter_window=(p["first"], p["last"]), current_window=now,
        dates_changed=(p["first"], p["last"]) != now,
        missed=missed_items(con, a["id"], t), status=letter_status(con, a["id"], t["id"]), letter=letter,
        decisions=decisions(con, a["id"], t["id"]),
    )
    return v


# ---------------------------------------------------------------------------
# Approve / reject
# ---------------------------------------------------------------------------

def decisions(con, athlete_id, tournament_id) -> list[dict]:
    cur = con.execute("""SELECT reviewer_name, reviewer_title, decision, note, decided_at
                         FROM verification_decisions WHERE athlete_id=? AND tournament_id=?
                         ORDER BY decided_at, id""", (int(athlete_id), int(tournament_id)))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def record_decision(con, user, athlete_id: int, tournament_id: int, decision: str,
                    note: str = "", now: datetime | None = None) -> str:
    """
    Approve or reject a letter as a proctor / class teacher / HoD (or admin).
    Approve is letters.advance_as (one step on, PED -> proctor -> HoD); reject is
    letters.reject_as (reason required, athlete must redraft). Both are logged here
    and emitted as `verification_decided`. Returns the new status label.
    Raises PermissionError or ValueError with a message fit to show the user.
    """
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    auth.require(user, "approve_letters")
    if not auth.can_view_athlete(con, user, athlete_id):
        raise PermissionError("This student is outside your department or class.")
    title = "admin" if user.role == "admin" else (user.title or "")
    if title not in REVIEWER_TITLES:
        raise PermissionError("Only a proctor, class teacher or HoD can sign exemption letters.")
    if not _entered(con, athlete_id, tournament_id):
        raise ValueError("The student is not on the entry list for this tournament.")
    letter = letters.find_letter(con, athlete_id, tournament_id)
    if letter is None:
        raise ValueError("The student has not drafted this letter in the planner yet.")
    if letter["needs_redo"]:
        raise ValueError("The tournament changed after this letter was drafted. Ask the student for a fresh letter.")
    if letter["rejected"]:
        raise ValueError("This letter was rejected. The student must redraft it first.")

    note = (note or "").strip()
    if decision == "rejected" and not note:
        raise ValueError("Give a reason so the student knows what to fix.")
    need = STAGE_BEFORE.get(title)
    if need and letter["stage"] != need:
        if _stage_at_least(letter, letters.next_stage(need)):
            raise ValueError(f"Already past this step: the letter is at {letters.STAGE_LABELS[letter['stage']]}.")
        waiting = {"ped_signed": "the PED's signature", "proctor_signed": "the proctor's signature"}[need]
        raise ValueError(f"Waiting for {waiting} first. The letter is at "
                         f"{letters.STAGE_LABELS[letter['stage']]}.")
    if decision == "approved":
        letters.advance_as(con, user, letter["id"], note=note or "Approved from the verification page")
    else:
        letters.reject_as(con, user, letter["id"], note)   # notifies the athlete

    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    con.execute("""INSERT INTO verification_decisions
                   (athlete_id, tournament_id, reviewer_id, reviewer_name, reviewer_title, decision, note, decided_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (int(athlete_id), int(tournament_id), user.id, user.name, title, decision, note, stamp))
    con.commit()

    if decision == "approved":
        t = _tournament(con, tournament_id)
        letters.notify(con, athlete_id, f"{user.name} ({REVIEWER_TITLES[title]}) approved your exemption letter "
                       f"for {t['name']}.", kind="letter_approved", tournament_id=tournament_id)
    status = letter_status(con, athlete_id, tournament_id)
    hooks.emit(con, "verification_decided", {
        "athlete_id": int(athlete_id), "tournament_id": int(tournament_id), "decision": decision,
        "status": status, "reviewer": user.name, "reviewer_title": title, "note": note,
    })
    return status


def on_athlete_deleted(con, payload: dict) -> int:
    """Data deletion: drop every approve / reject recorded for the athlete. Returns rows removed."""
    cur = con.execute("DELETE FROM verification_decisions WHERE athlete_id=?", (int(payload["athlete_id"]),))
    con.commit()
    return cur.rowcount


def decisions_table(con, athlete_ids: list[int]) -> pd.DataFrame:
    """Latest faculty decision per letter, for the athlete and coach tabs."""
    if not athlete_ids:
        return pd.DataFrame(columns=["Athlete", "USN", "Tournament", "Reviewer", "Decision", "Note", "When"])
    marks = ",".join("?" * len(athlete_ids))
    df = pd.read_sql_query(f"""
        SELECT a.name AS Athlete, a.usn AS USN, t.name AS Tournament, d.reviewer_name, d.reviewer_title,
               d.decision AS Decision, d.note AS Note, d.decided_at AS "When"
        FROM verification_decisions d
        JOIN athletes a ON a.id = d.athlete_id JOIN tournaments t ON t.id = d.tournament_id
        WHERE d.athlete_id IN ({marks}) ORDER BY d.decided_at DESC, d.id DESC""",
        con, params=[int(i) for i in athlete_ids])
    df["Reviewer"] = df["reviewer_name"] + " (" + df["reviewer_title"].map(lambda t: REVIEWER_TITLES.get(t, t)) + ")"
    return df[["Athlete", "USN", "Tournament", "Reviewer", "Decision", "Note", "When"]]


# ---------------------------------------------------------------------------
# Department view: upcoming absences and make-up tests to plan
# ---------------------------------------------------------------------------

def upcoming_absences(con, athlete_ids: list[int], today: date, horizon_days: int = 45) -> pd.DataFrame:
    """One row per athlete per tournament whose away window overlaps [today, today + horizon]."""
    cols = ["athlete_id", "tournament_id", "Athlete", "USN", "Sem", "Section", "Sport", "Tournament",
            "Away from", "Away to", "Days away", "Class days", "Tests missed", "Letter status"]
    if not athlete_ids:
        return pd.DataFrame(columns=cols)
    end = today + timedelta(days=horizon_days)
    marks = ",".join("?" * len(athlete_ids))
    cur = con.execute(f"""SELECT a.id, t.id FROM entries e
                          JOIN athletes a ON a.id = e.athlete_id JOIN tournaments t ON t.id = e.tournament_id
                          WHERE a.id IN ({marks})""", [int(i) for i in athlete_ids])
    rows = []
    for aid, tid in cur.fetchall():
        a, t = _athlete(con, aid), _tournament(con, tid)
        first, last = away_window(t)
        if last < today or first > end:
            continue
        missed = missed_items(con, aid, t)
        tests = sorted({f"{c.title} ({c.date:%d %b})" for c in missed if c.kind in ("CIE", "SEE")})
        class_days = sum(1 for i in range((last - first).days + 1) if (first + timedelta(days=i)).weekday() < 6)
        rows.append({
            "athlete_id": aid, "tournament_id": tid, "Athlete": a["name"], "USN": a["usn"],
            "Sem": a.get("sem"), "Section": a.get("section") or "", "Sport": a.get("sport"),
            "Tournament": t["name"], "Away from": first, "Away to": last,
            "Days away": (last - first).days + 1, "Class days": class_days,
            "Tests missed": ", ".join(tests), "Letter status": letter_status(con, aid, tid),
        })
    return pd.DataFrame(rows, columns=cols).sort_values(["Away from", "Athlete"]).reset_index(drop=True)


def makeup_plan(con, athlete_ids: list[int], today: date, horizon_days: int = 45) -> pd.DataFrame:
    """Each CIE / SEE that athletes will miss, with who needs a make-up. For scheduling."""
    end = today + timedelta(days=horizon_days)
    need: dict[tuple, list[str]] = {}
    for r in upcoming_absences(con, athlete_ids, today, horizon_days).itertuples():
        t = _tournament(con, r.tournament_id)
        for c in missed_items(con, r.athlete_id, t):
            if c.kind in ("CIE", "SEE") and today <= c.date <= end:
                need.setdefault((c.date, c.kind, c.title), []).append(f"{r.Athlete} ({r.USN})")
    rows = [{"Date": d, "Type": k, "Assessment": ti, "Students": len(set(p)), "Who": ", ".join(sorted(set(p)))}
            for (d, k, ti), p in sorted(need.items())]
    return pd.DataFrame(rows, columns=["Date", "Type", "Assessment", "Students", "Who"])


# ---------------------------------------------------------------------------
# QR on the letter
# ---------------------------------------------------------------------------

def qr_png(url: str) -> bytes | None:
    """PNG bytes of a QR code for url, or None if the qrcode package is missing."""
    try:
        import qrcode
    except ImportError:
        return None
    img = qrcode.make(url, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def add_qr_to_letter(docx_bytes: bytes, url: str) -> bytes:
    """Append a 'Verify this letter' block with a QR code and the link to a .docx letter."""
    from docx import Document
    from docx.shared import Cm, Pt

    doc = Document(io.BytesIO(docx_bytes))
    doc.add_paragraph("")
    p = doc.add_paragraph()
    p.add_run("Verify this letter").bold = True
    png = qr_png(url)
    if png:
        doc.add_picture(io.BytesIO(png), width=Cm(3.2))
    note = doc.add_paragraph(
        "Faculty: scan the code or open the link to see the tournament and dates confirmed by the "
        "Physical Education Director, and to approve or reject this request.\n" + url)
    for run in note.runs:
        run.font.size = Pt(8)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def letter_with_qr(con, docx_bytes: bytes, athlete_id: int, tournament_id: int, base_url: str | None = None) -> bytes:
    """What the letter download should call: signs a token for this letter and stamps it on."""
    return add_qr_to_letter(docx_bytes, verify_url(make_token(con, athlete_id, tournament_id),
                                                   base_url or _configured_base_url(con)))
