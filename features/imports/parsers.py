"""
Pure parsing for the imports feature. No database, no Streamlit.

Two jobs:
  1. Timetable   - a department timetable sheet (CSV / XLSX) or a photo of one
                   -> weekly slots [{weekday, subject, kind}]
  2. Exam calendar - the college calendar PDF (or a CSV / XLSX of it)
                   -> dated events [{date, kind, title}] for CIE / SEE / lab tests

Everything returns an ImportResult so the UI can show rows for review, plus
warnings and the lines it could not understand. Nothing is saved from here.
"""
from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

TIMETABLE_COLUMNS = ["weekday", "subject", "kind"]
EVENT_COLUMNS = ["date", "kind", "title"]


@dataclass
class ImportResult:
    rows: pd.DataFrame
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # input lines we could not use
    source: str = ""                                   # 'csv' | 'xlsx' | 'image' | 'pdf'

    @property
    def ok(self) -> bool:
        return not self.rows.empty


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DAY_PATTERNS = [
    (0, r"mon(day)?"), (1, r"tue(s|sday)?"), (2, r"wed(nesday)?"),
    (3, r"thu(r|rs|rsday)?"), (4, r"fri(day)?"), (5, r"sat(urday)?"), (6, r"sun(day)?"),
]
_DAY_FULL = [(i, re.compile(rf"^\s*{p}\s*[.:]?\s*$", re.I)) for i, p in _DAY_PATTERNS]
_DAY_PREFIX = [(i, re.compile(rf"^\s*{p}\b\.?", re.I)) for i, p in _DAY_PATTERNS]
_DAY_ANYWHERE = re.compile(r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(day|sday|nesday|rsday|urday)?\b\.?", re.I)


def weekday_of(text) -> int | None:
    """'MON', 'Monday', 'thurs.' -> 0..6; anything else -> None. Also accepts 0-6 ints."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    if isinstance(text, (int,)) or (isinstance(text, float) and text.is_integer()):
        n = int(text)
        return n if 0 <= n <= 6 else None
    s = str(text).strip()
    if s.isdigit():
        n = int(s)
        return n if 0 <= n <= 6 else None
    for i, rx in _DAY_FULL:
        if rx.match(s):
            return i
    return None


def _clean(cell) -> str:
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return ""
    return re.sub(r"[ \t]+", " ", str(cell)).strip()


def read_table(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """CSV or Excel into a header-less DataFrame of strings (row 0 is whatever row 0 was)."""
    name = filename.lower()
    buf = io.BytesIO(file_bytes)
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        df = pd.read_excel(buf, header=None, dtype=object)
    elif name.endswith((".csv", ".txt")):
        text = file_bytes.decode("utf-8-sig", errors="replace")
        df = pd.read_csv(io.StringIO(text), header=None, dtype=str, sep=None, engine="python",
                         skip_blank_lines=False)
    else:
        raise ValueError(f"Unsupported file type: {filename}. Use CSV or XLSX.")
    return df.map(_clean) if hasattr(df, "map") else df.applymap(_clean)


# ---------------------------------------------------------------------------
# 1. Timetable
# ---------------------------------------------------------------------------

_SKIP_CELL = re.compile(
    r"^(|-+|—|–|x|nil|none|free|break|short break|tea break|lunch( break)?|recess|"
    r"b\s*r\s*e\s*a\s*k|l\s*u\s*n\s*c\s*h)$", re.I)
_LAB = re.compile(r"\blab\b|laborator|practical|\(p\)|\bworkshop\b", re.I)
_TIME = re.compile(r"^\d{1,2}[:.]\d{2}(\s*(am|pm))?(\s*(-|–|to)\s*\d{1,2}[:.]\d{2}(\s*(am|pm))?)?$", re.I)


def slot_kind(subject: str) -> str:
    return "lab" if _LAB.search(subject) else "class"


def _split_cell(cell: str) -> list[str]:
    """One timetable cell can hold batch-wise labs: 'DS LAB (B1) / DLCO LAB (B2)' or two lines."""
    parts = re.split(r"\n|\s+/\s+|\s*\|\s*", cell)
    return [p.strip(" ,;") for p in parts if p.strip(" ,;")]


def _finish_timetable(records: list[dict], warnings: list[str], skipped: list[str], source: str,
                      legend: dict[str, str] | None = None) -> ImportResult:
    legend = legend or {}
    out = []
    for r in records:
        subj = r["subject"]
        full = legend.get(subj.upper())
        if full:
            # keep the lab marker if the code had it but the full name does not
            subj = full if (r["kind"] != "lab" or _LAB.search(full)) else f"{full} Lab"
        out.append({"weekday": int(r["weekday"]), "subject": subj, "kind": r["kind"]})
    df = pd.DataFrame(out, columns=TIMETABLE_COLUMNS).drop_duplicates().reset_index(drop=True)
    if not df.empty:
        df = df.sort_values(["weekday"], kind="stable").reset_index(drop=True)
        if (df["weekday"] == 6).any():
            warnings.append("Some slots are on Sunday. Check that the day column was read correctly.")
    else:
        warnings.append("No timetable slots found. The sheet needs a column of days (Mon, Tue, ...) "
                        "or columns named Day and Subject.")
    return ImportResult(df, warnings, skipped, source)


def _parse_long(df: pd.DataFrame, header_row: int, warnings, skipped, source) -> ImportResult:
    """Columns like Day | Subject | Type (| Time). One slot per row."""
    header = [h.lower() for h in df.iloc[header_row]]
    def col(*names):
        for i, h in enumerate(header):
            if any(n == h or h.startswith(n) for n in names):
                return i
        return None
    c_day = col("day", "weekday")
    c_sub = col("subject", "course", "course name", "paper")
    c_kind = col("type", "kind", "class type", "category")
    records = []
    for r in range(header_row + 1, len(df)):
        row = df.iloc[r]
        wd = weekday_of(row.iloc[c_day])
        subj = _clean(row.iloc[c_sub])
        if wd is None or not subj or _SKIP_CELL.match(subj):
            if any(_clean(v) for v in row):
                skipped.append(" | ".join(_clean(v) for v in row if _clean(v)))
            continue
        kind = _clean(row.iloc[c_kind]).lower() if c_kind is not None else ""
        kind = "lab" if kind.startswith(("lab", "prac")) else ("class" if kind else slot_kind(subj))
        records.append({"weekday": wd, "subject": subj, "kind": kind})
    return _finish_timetable(records, warnings, skipped, source)


def _grid_rows_to_records(rows: list[list[str]], warnings, skipped):
    """
    Grid layout used by most department timetables: one row per day, one column per period.
      DAY | 9:00-10:00 | 10:00-11:00 | 11:00-11:30 | ...
      MON | CS231      | MA211       | BREAK       | ...
    Rows that are not day rows (titles, time headers, the subject legend) are returned as `other`.
    """
    records, other = [], []
    for row in rows:
        day_at = next((i for i, c in enumerate(row[:3]) if weekday_of(c) is not None), None)
        if day_at is None:
            other.append(row)
            continue
        wd = weekday_of(row[day_at])
        for cell in row[day_at + 1:]:
            if not cell or _TIME.match(cell):
                continue
            for part in _split_cell(cell):
                if _SKIP_CELL.match(part) or _TIME.match(part):
                    continue
                records.append({"weekday": wd, "subject": part, "kind": slot_kind(part)})
    return records, other


def _legend_from(other_rows: list[list[str]], used: set[str]) -> dict[str, str]:
    """
    Timetables usually list 'CODE | Subject name | Faculty' below the grid.
    Map a code we saw in the grid to the longest text cell next to it.
    """
    legend = {}
    for row in other_rows:
        cells = [c for c in row if c]
        for i, c in enumerate(cells):
            key = c.upper()
            if key in used and i + 1 < len(cells):
                name = cells[i + 1]
                if len(name) > len(c) and not _TIME.match(name) and name.upper() not in used:
                    legend[key] = name
    return legend


def parse_timetable_table(df: pd.DataFrame, source: str = "csv") -> ImportResult:
    """Detect long vs grid layout and parse it."""
    warnings: list[str] = []
    skipped: list[str] = []
    # Long layout: a header row with both a day column and a subject column
    for r in range(min(len(df), 10)):
        hdr = [c.lower() for c in df.iloc[r]]
        if any(h in ("day", "weekday") for h in hdr) and any(
                h.startswith(("subject", "course", "paper")) for h in hdr):
            return _parse_long(df, r, warnings, skipped, source)
    rows = [[_clean(c) for c in row] for row in df.itertuples(index=False)]
    records, other = _grid_rows_to_records(rows, warnings, skipped)
    legend = _legend_from(other, {r["subject"].upper() for r in records})
    if legend:
        warnings.append(f"Expanded {len(legend)} subject codes using the legend in the sheet.")
    return _finish_timetable(records, warnings, skipped, source, legend)


def parse_timetable_file(file_bytes: bytes, filename: str) -> ImportResult:
    """Entry point for uploads: CSV / XLSX, or PNG / JPG through OCR."""
    name = filename.lower()
    if name.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
        return parse_timetable_image(file_bytes)
    source = "xlsx" if name.endswith((".xlsx", ".xlsm", ".xls")) else "csv"
    return parse_timetable_table(read_table(file_bytes, filename), source)


# ---------------------------------------------------------------- image (OCR)

def ocr_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _ocr_image(image_bytes: bytes):
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img).convert("L")
    # Small phone crops OCR badly; upscale so text is ~30px high
    if img.width < 1600:
        f = 1600 / img.width
        img = img.resize((int(img.width * f), int(img.height * f)))
    return _remove_table_lines(_deskew(img))


def _deskew(img, max_angle: float = 6.0):
    """Phone photos are rarely straight. Pick the small rotation whose row profile is sharpest."""
    import numpy as np
    from PIL import Image

    small = img.resize((max(1, img.width // 3), max(1, img.height // 3)))
    ink = Image.fromarray(((np.array(small) < 140) * 255).astype("uint8"))
    def sharpness(angle):
        prof = np.array(ink.rotate(angle, expand=False, fillcolor=0)).sum(axis=1).astype(float)
        return float(np.sum(np.diff(prof) ** 2))
    coarse = max(np.arange(-max_angle, max_angle + 0.01, 0.5), key=sharpness)
    best = max(np.arange(coarse - 0.5, coarse + 0.51, 0.1), key=sharpness)
    if abs(best) < 0.15:
        return img
    return img.rotate(float(best), expand=True, fillcolor=255, resample=Image.BICUBIC)


def _long_runs(dark, min_len):
    """Mark horizontal runs of dark pixels at least min_len long (table rules, not letters)."""
    import numpy as np

    out = np.zeros_like(dark)
    for i in np.where(dark.sum(axis=1) >= min_len)[0]:
        d = np.diff(np.concatenate(([0], dark[i].astype(np.int8), [0])))
        for s, e in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
            if e - s >= min_len:
                out[i, s:e] = True
    return out


def _remove_table_lines(img):
    """Grid lines make Tesseract glue cells together and misread letters; paint them white."""
    import numpy as np
    from PIL import Image

    a = np.array(img)
    dark = a < 140
    mask = _long_runs(dark, max(60, a.shape[1] // 12)) | _long_runs(dark.T, max(40, a.shape[0] // 15)).T
    mask[1:] |= mask[:-1]
    mask[:-1] |= mask[1:]
    mask[:, 1:] |= mask[:, :-1]
    mask[:, :-1] |= mask[:, 1:]
    a = a.copy()
    a[mask] = 255
    return Image.fromarray(a)


_OCR_JUNK = re.compile(r"^[|\[\]_—–\-=:;.,'\"`~]+$")
_CODE = re.compile(r"[A-Z]{1,4}\s?\d{3}[A-Z]{0,2}", re.I)


def ocr_image_rows(image_bytes: bytes) -> list[list[str]]:
    """
    OCR the image into rows of cells. Tesseract gives word boxes; words on one text line
    belong to the same cell unless the horizontal gap between them is wide (a column gap).
    """
    import pytesseract

    img = _ocr_image(image_bytes)
    d = pytesseract.image_to_data(img, config="--psm 4", output_type=pytesseract.Output.DICT)
    lines: dict[tuple, list] = {}
    for i, word in enumerate(d["text"]):
        w = (word or "").strip().strip("|[]")
        if not w or _OCR_JUNK.match(w) or float(d["conf"][i]) < 0:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        lines.setdefault(key, []).append((d["left"][i], d["width"][i], d["height"][i], d["top"][i], w))
    rows = []
    for words in sorted(lines.values(), key=lambda ws: min(w[3] for w in ws)):
        words.sort()
        h = sorted(w[2] for w in words)[len(words) // 2]
        cells, cur, right = [], [], None
        for left, width, _, _, w in words:
            if right is not None and left - right > 0.9 * h:
                cells.append(" ".join(cur))
                cur = []
            cur.append(w)
            right = left + width
        cells.append(" ".join(cur))
        merged: list[str] = []
        for c in cells:
            # '& CO', '(B2)', 'and Networks' continue the previous cell across a wide gap
            if merged and (c[:1] in "&(-" or c[:1].islower() or merged[-1].endswith(("&", "-", "and"))):
                merged[-1] = f"{merged[-1]} {c}"
            else:
                merged.append(c)
        # leftovers of grid lines read as quotes, ticks or a stray 'l'
        cells = [c.strip(" /'\"‘’“”`~|!") for c in merged]
        cells = [c for c in cells if len(c) > 1]
        if cells:
            rows.append(cells)
    return rows


def ocr_image_text(image_bytes: bytes) -> str:
    """Rows with ' | ' between cells: the format parse_timetable_text reads."""
    return "\n".join(" | ".join(r) for r in ocr_image_rows(image_bytes))


_OCR_LETTER = str.maketrans({"$": "S", "5": "S", "€": "C", "¢": "C", "(": "C", "0": "O", "1": "I", "8": "B"})


def _snap_code(token: str, codes: list[str]) -> str | None:
    """'$231', 'C5234', 'C€S234', '¢s232' -> the legend code they were meant to be."""
    import difflib

    t = re.sub(r"[^A-Za-z0-9$€¢(]", "", token).upper()
    m = re.fullmatch(r"(.{1,4}?)(\d{3}[A-Z]{0,2})", t)
    if not m:
        return None
    t = m.group(1).translate(_OCR_LETTER) + m.group(2)
    if t in codes:
        return t
    hit = difflib.get_close_matches(t, codes, n=1, cutoff=0.7)
    return hit[0] if hit else None


def _fix_ocr_codes(records: list[dict], legend: dict[str, str]) -> list[dict]:
    """Snap misread subject codes onto the legend; split cells where OCR glued two codes together."""
    codes = list(legend)
    if not codes:
        return records
    out = []
    for r in records:
        if r["subject"].upper() in legend or r["kind"] == "lab":
            out.append(r)
            continue
        parts = r["subject"].split()
        snapped = [_snap_code(p, codes) for p in parts]
        if all(snapped):
            out += [{**r, "subject": c} for c in snapped]
        elif len(parts) > 1 and all(snapped[i] or _SKIP_CELL.match(p) for i, p in enumerate(parts)):
            out += [{**r, "subject": c} for c in snapped if c]
        else:
            out.append(r)
    return out


def parse_timetable_text(text: str, source: str = "image") -> ImportResult:
    """
    OCR text, one line per row, cells separated by '|' (or runs of 2+ spaces).
    A line with no day right after a day line, holding only lab names or subject codes,
    is the wrapped second line of a cell ('DS LAB (B1) /' then 'DLCO LAB (B2)').
    """
    rows, last_day = [], None
    for line in text.splitlines():
        cells = [c.strip(" /") for c in re.split(r"\s*\|\s*|\s{2,}", line.strip(" |")) if c.strip(" /")]
        if not cells:
            continue
        day = None
        for i, rx in _DAY_PREFIX:
            m = rx.match(cells[0])
            if m:
                day = i
                rest = cells[0][m.end():].strip(" :|-")
                cells = ([rest] if rest else []) + cells[1:]
                break
        if day is not None:
            rows.append([WEEKDAY_NAMES[day], *cells])
            last_day = day
        elif last_day is not None and all(_LAB.search(c) or _CODE.fullmatch(c) for c in cells):
            rows.append([WEEKDAY_NAMES[last_day], *cells])
        else:
            rows.append(cells)
            last_day = None
    warnings = ["Read from an image. Check every row before saving: OCR can misread subject names."]
    records, other = _grid_rows_to_records(rows, warnings, [])
    legend = {}
    for r in other:
        if len(r) >= 2 and _CODE.fullmatch(r[0]) and len(r[1]) > len(r[0]):
            legend[r[0].upper().replace(" ", "")] = r[1]
    records = _fix_ocr_codes(records, legend)
    legend.update(_legend_from(other, {r["subject"].upper() for r in records}))
    skipped = [" | ".join(r) for r in other
               if not any(_TIME.match(c) for c in r) and r[0].upper().replace(" ", "") not in legend
               and r[0].lower() not in ("code", "day", "course code")]
    return _finish_timetable(records, warnings, skipped, source, legend)


def parse_timetable_image(image_bytes: bytes) -> ImportResult:
    if not ocr_available():
        return ImportResult(pd.DataFrame(columns=TIMETABLE_COLUMNS),
                            ["Image reading is not set up on this computer (Tesseract OCR is missing). "
                             "Upload the timetable as an Excel or CSV sheet instead."], [], "image")
    return parse_timetable_text(ocr_image_text(image_bytes), "image")


def timetable_template_csv() -> bytes:
    rows = [
        ["Day", "9:00-10:00", "10:00-11:00", "11:00-11:30", "11:30-12:30", "12:30-1:30", "1:30-2:30", "2:30-4:30"],
        ["MON", "CS231", "MA211", "BREAK", "CS232", "LUNCH", "CS233", ""],
        ["TUE", "CS232", "CS231", "BREAK", "MA211", "LUNCH", "DS LAB (B1) / DLCO LAB (B2)", ""],
        [],
        ["Code", "Subject", "Faculty"],
        ["CS231", "Data Structures", ""],
        ["CS232", "Digital Logic & CO", ""],
        ["CS233", "Operating Systems", ""],
        ["MA211", "Linear Algebra", ""],
    ]
    return "\n".join(",".join(f'"{c}"' if "," in c else c for c in r) for r in rows).encode()


# ---------------------------------------------------------------------------
# 2. Exam calendar
# ---------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_ORD = r"(?:st|nd|rd|th)?"
_SEP = r"\s*(?:-|–|—|to|till|until|&)\s*"

# 14/10/2026, 14-10-26, 14.10.2026
_NUM_DATE = r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})\b"
# 14 Oct 2026, 14th October, 2026, 14-Oct-26
_TXT_DATE = rf"(\d{{1,2}}){_ORD}[\s\-]*{_MON}[\s,\-]*(\d{{4}}|\d{{2}}(?!\d))?"

_RX_NUM_RANGE = re.compile(rf"{_NUM_DATE}{_SEP}{_NUM_DATE}", re.I)
_RX_TXT_RANGE = re.compile(rf"{_TXT_DATE}{_SEP}{_TXT_DATE}", re.I)
_RX_SHORT_RANGE = re.compile(rf"\b(\d{{1,2}}){_ORD}{_SEP}(\d{{1,2}}){_ORD}[\s\-]*{_MON}[\s,\-]*(\d{{4}})?", re.I)
_RX_NUM = re.compile(_NUM_DATE)
_RX_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_RX_TXT = re.compile(rf"\b{_TXT_DATE}", re.I)

_RX_SEE = re.compile(r"\bSEE\b|semester[- ]end|end[- ]sem|final exam|\bESE\b", re.I)
_RX_LAB = re.compile(r"\blab(oratory)?\b.*\b(cie|test|exam|internal|assessment)|\bpractical (exam|test)", re.I)
_RX_CIE = re.compile(r"\bCIE\b|\bIA\b|internal (assessment|test|exam)|\btest\b|\bquiz\b|mid[- ]?term|\bCAT\b", re.I)
_RX_LABEL = re.compile(r"\b(CIE|IA|SEE|Quiz|Test|Internal Assessment)(?:\s+(?:test|exam))?\s*[-–#]?\s*(\d|IV|I{1,3})?\b", re.I)
_RX_DROP = re.compile(r"\b(holiday|vacation|commencement|last working day|registration|result|"
                      r"fee|orientation|makeup|make-up|re-?exam|supplementary)\b", re.I)
_RX_NOISE = re.compile(r"\b(date|day|from|to|time|timing|slot|am|pm|fn|an|forenoon|afternoon)\b\.?", re.I)
_RX_COURSE_CODE = re.compile(r"\b\d{0,2}[A-Z]{2,4}\d{2,3}[A-Z]{0,3}\b")
_RX_CLOCK = re.compile(r"\b\d{1,2}[:.]\d{2}\s*(am|pm)?\s*((-|–|to)\s*\d{1,2}[:.]\d{2}\s*(am|pm)?)?", re.I)


def _year(y: str | None, default_year: int) -> int:
    if not y:
        return default_year
    y = int(y)
    return y + 2000 if y < 100 else y


def _mk(d, m, y) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _month(tok: str) -> int:
    return _MONTHS[tok[:3].lower()]


def find_dates(text: str, default_year: int) -> tuple[list[tuple[date, date]], str]:
    """
    All dates and date ranges in a line, as (start, end) pairs (end == start for single days),
    plus the line with the dates cut out. Day-first, as Indian calendars are written.
    """
    spans: list[tuple[date, date]] = []
    rest = text

    def take(rx, build):
        nonlocal rest
        for m in list(rx.finditer(rest)):
            got = build(m.groups())
            if got and got[0] and got[1] and got[0] <= got[1] and (got[1] - got[0]).days <= 31:
                spans.append(got)
                rest = rest.replace(m.group(0), " ", 1)

    take(_RX_ISO, lambda g: (lambda d: (d, d))(_mk(int(g[2]), int(g[1]), int(g[0]))))
    take(_RX_NUM_RANGE, lambda g: (_mk(int(g[0]), int(g[1]), _year(g[2], default_year)),
                                   _mk(int(g[3]), int(g[4]), _year(g[5], default_year))))
    def txt_range(g):
        y2 = _year(g[5], default_year)
        return (_mk(int(g[0]), _month(g[1]), _year(g[2], y2)), _mk(int(g[3]), _month(g[4]), y2))
    take(_RX_TXT_RANGE, txt_range)
    take(_RX_SHORT_RANGE, lambda g: (_mk(int(g[0]), _month(g[2]), _year(g[3], default_year)),
                                     _mk(int(g[1]), _month(g[2]), _year(g[3], default_year))))
    take(_RX_NUM, lambda g: (lambda d: (d, d))(_mk(int(g[0]), int(g[1]), _year(g[2], default_year))))
    take(_RX_TXT, lambda g: (lambda d: (d, d))(_mk(int(g[0]), _month(g[1]), _year(g[2], default_year))))
    return spans, rest


def guess_year(texts: list[str], fallback: int | None = None) -> int:
    years = Counter()
    for t in texts:
        for y in re.findall(r"\b(20\d{2})\b", t):
            years[int(y)] += 1
    return years.most_common(1)[0][0] if years else (fallback or date.today().year)


def event_kind(text: str) -> str | None:
    if _RX_DROP.search(text):
        return None
    if _RX_LAB.search(text):
        return "lab"
    if _RX_SEE.search(text):
        return "SEE"
    if _RX_CIE.search(text):
        return "CIE"
    return None


def _label(text: str) -> str | None:
    """'CIE-2', 'SEE', 'Quiz 1' from a heading or a line."""
    m = _RX_LABEL.search(text)
    if not m:
        return None
    word, num = m.group(1), m.group(2)
    word = "CIE" if word.lower() in ("ia", "internal assessment") else (word.upper() if len(word) <= 3 else word.title())
    if num:
        roman = {"I": "1", "II": "2", "III": "3", "IV": "4"}
        return f"{word}-{roman.get(num.upper(), num)}"
    return word


def _title(rest: str) -> str:
    t = _RX_CLOCK.sub(" ", rest)
    t = _DAY_ANYWHERE.sub(" ", t)
    t = _RX_NOISE.sub(" ", t)
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"^\s*\d{1,3}[.)]?\s+", " ", t)           # leading serial number
    t = re.sub(r"[|,;:]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—.")
    return t


def parse_calendar_lines(lines: list[str], default_year: int | None = None,
                         include_sundays: bool = False, source: str = "pdf") -> ImportResult:
    """
    Walk the calendar top to bottom. A line with a CIE/SEE keyword and no date is a heading
    ('CIE-2 TIMETABLE FOR III SEMESTER'); dated rows under it inherit its kind and label.
    """
    year = default_year or guess_year(lines)
    warnings, skipped, out = [], [], []
    heading_kind, heading_label = None, None
    for raw in lines:
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        spans, rest = find_dates(line, year)
        kind = event_kind(line)
        if not spans:
            if kind:
                heading_kind, heading_label = kind, _label(line)
            elif _RX_DROP.search(line):
                heading_kind = heading_label = None
            continue
        if _RX_DROP.search(line):
            skipped.append(line)
            continue
        kind = kind or heading_kind
        if not kind:
            skipped.append(line)
            continue
        own_label = _label(line)
        label = own_label or (heading_label if kind == heading_kind else None)
        title = _title(rest)
        if own_label:
            # 'CIE - I' -> 'CIE-1' in place, so 'SEE (Theory)' keeps its order
            title = _RX_LABEL.sub(own_label, title, count=1)
        # course codes add nothing once the course name is there
        no_codes = _RX_COURSE_CODE.sub(" ", title).strip()
        if re.search(r"[a-z]{3}", no_codes):
            title = re.sub(r"\s+", " ", no_codes)
        if label and label.lower() not in title.lower():
            title = f"{title} {label}".strip()
        title = title or label or kind
        # A bare "CIE-2" row is the whole test week; subject-wise rows for the same days replace it
        generic = title.replace(" ", "").lower() in {(label or "").replace(" ", "").lower(), kind.lower()}
        for start, end in spans:
            d = start
            while d <= end:
                if include_sundays or d.weekday() != 6 or start == end:
                    out.append({"date": d, "kind": kind, "title": title, "_generic": generic})
                d += timedelta(days=1)
    df = pd.DataFrame(out, columns=EVENT_COLUMNS + ["_generic"])
    if not df.empty:
        specific = set(zip(df.loc[~df["_generic"], "date"], df.loc[~df["_generic"], "kind"]))
        df = df[~(df["_generic"] & pd.Series([(d, k) in specific for d, k in zip(df["date"], df["kind"])],
                                             index=df.index))]
    df = df.drop(columns="_generic").drop_duplicates().sort_values("date", kind="stable").reset_index(drop=True)
    if df.empty:
        warnings.append("No CIE, SEE or lab test dates found. If the PDF is a scanned image, "
                        "type the dates into a sheet (Date, Type, Title) and upload that instead.")
    if not default_year and not any(re.search(r"\b20\d{2}\b", l) for l in lines):
        warnings.append(f"The calendar has no year on it; assumed {year}.")
    return ImportResult(df, warnings, skipped, source)


def pdf_lines(pdf_bytes: bytes) -> list[str]:
    """Text lines from a text PDF, with table rows flattened to 'a | b | c' so cells stay apart."""
    import pdfplumber

    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.find_tables()
            table_boxes = [t.bbox for t in tables]
            # Text outside tables (headings, notes), in reading order
            outside = page
            for box in table_boxes:
                outside = outside.outside_bbox(box)
            text_lines = [(l["top"], l["text"]) for l in outside.extract_text_lines()]
            table_lines = []
            for t in tables:
                for i, row in enumerate(t.extract()):
                    cells = [re.sub(r"\s+", " ", c or "").strip() for c in row]
                    if any(cells):
                        table_lines.append((t.bbox[1] + i * 0.01, " | ".join(cells)))
            lines += [t for _, t in sorted(text_lines + table_lines, key=lambda x: x[0])]
    return lines


def parse_exam_calendar_file(file_bytes: bytes, filename: str, default_year: int | None = None) -> ImportResult:
    name = filename.lower()
    if name.endswith(".pdf"):
        lines = pdf_lines(file_bytes)
        if not "".join(lines).strip():
            return ImportResult(pd.DataFrame(columns=EVENT_COLUMNS),
                                ["This PDF has no text layer (it is a scan). Ask the office for the "
                                 "original PDF, or type the dates into a sheet and upload that."], [], "pdf")
        return parse_calendar_lines(lines, default_year, source="pdf")
    df = read_table(file_bytes, filename)
    # A tidy sheet with Date / Type / Title columns needs no guessing
    hdr = [c.lower() for c in df.iloc[0]] if len(df) else []
    if "date" in hdr and any(h in ("type", "kind") for h in hdr):
        return _parse_event_sheet(df, hdr, default_year)
    lines = [" | ".join(c for c in row if c) for row in df.itertuples(index=False)]
    return parse_calendar_lines(lines, default_year, source="xlsx" if name.endswith(("xlsx", "xls")) else "csv")


def _parse_event_sheet(df, hdr, default_year) -> ImportResult:
    c_date = hdr.index("date")
    c_kind = next(i for i, h in enumerate(hdr) if h in ("type", "kind"))
    c_title = next((i for i, h in enumerate(hdr) if h in ("title", "subject", "what", "event")), None)
    year = default_year or guess_year([" ".join(r) for r in df.itertuples(index=False)])
    out, skipped = [], []
    for row in df.iloc[1:].itertuples(index=False):
        spans, _ = find_dates(str(row[c_date]), year)
        if not spans:
            try:
                d = pd.to_datetime(row[c_date], dayfirst=True).date()
                spans = [(d, d)]
            except Exception:
                pass
        k = row[c_kind].strip().upper()
        kind = {"CIE": "CIE", "SEE": "SEE", "LAB": "lab"}.get(k) or event_kind(row[c_kind])
        if not spans or not kind:
            if any(row):
                skipped.append(" | ".join(c for c in row if c))
            continue
        title = row[c_title] if c_title is not None and row[c_title] else kind
        for s, e in spans:
            d = s
            while d <= e:
                out.append({"date": d, "kind": kind, "title": title})
                d += timedelta(days=1)
    df_out = pd.DataFrame(out, columns=EVENT_COLUMNS).drop_duplicates().sort_values("date").reset_index(drop=True)
    return ImportResult(df_out, [], skipped, "sheet")
