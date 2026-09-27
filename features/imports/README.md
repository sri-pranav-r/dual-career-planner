# imports

Timetable import and exam calendar import, so nobody types class slots or test dates one by one.

**Timetable** (tab "Import timetable": athletes for themselves; faculty and admin for a whole class)
- Excel or CSV in the usual department grid (a row per day, a column per period) or as Day / Subject / Type columns.
- A code legend under the grid ("CS231 | Data Structures") is used to expand codes. BREAK / LUNCH are skipped.
  Batch-wise labs ("DS LAB (B1) / DLCO LAB (B2)") become two rows; the athlete deletes the one that is not their batch.
- A photo or screenshot works through Tesseract OCR: the image is straightened, grid lines are removed,
  misread codes ("$231", "C5234") are snapped onto the legend. Clean screenshots match the Excel result exactly;
  blurry or tilted phone photos get most rows right. Every import shows an editable preview before saving.

**Exam calendar** (tab "Import exam calendar": faculty and admin)
- The college calendar PDF (text PDF, via pdfplumber), or Excel / CSV. Picks out CIE, SEE, lab CIE, quiz and
  internal test dates, including ranges ("12/10/2026 to 15/10/2026", "14 - 24 December 2026").
  Holidays, commencement, make-up and similar rows are listed as "not used" instead of imported.
- A subject-wise CIE timetable under a heading ("CIE - II TIMETABLE") gives one event per subject and replaces the
  generic "CIE-2" week for those days. SEE ranges skip Sundays.
- Applied to every athlete in the chosen classes (dept + sem, all sections or one), and kept per class so athletes
  added later by the roster import get the same dates (`roster_imported` hook). Re-importing does not duplicate.

Files: `parsers.py` (pure parsing), `logic.py` (saving, hook), `ui.py` (two tabs), `test_imports.py`,
`samples/` (RVCE-style demo files; rebuild with `python3 samples/make_samples.py`).

Tables it owns: `imports_class_timetable`, `imports_class_events`, `imports_log`.
On `athlete_deleted` it removes the log rows of that athlete's own timetable uploads; class-level tables hold no personal data and stay.
Events it emits: none of its own (core's `add_events_bulk` emits `events_added`).

Needs: `pdfplumber`, `openpyxl`, `pillow`, `pytesseract` (pip), and the `tesseract` program for photos
(Windows installer from UB Mannheim, `brew install tesseract`, or `apt install tesseract-ocr`).
Without Tesseract the image option is hidden and sheets and PDFs still work.
