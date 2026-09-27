"""Streamlit screens for the imports feature. Parse, let the person fix rows, then save."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import auth

from . import logic, parsers

WEEKDAYS = parsers.WEEKDAY_NAMES


def _class_label(dept, sem, section) -> str:
    return f"{dept} {sem} sem" + (f" section {section}" if section else "")


def _scoped_classes(con, user) -> pd.DataFrame:
    """Classes this user may import for. Faculty are limited to their dept (and sem/section if set)."""
    df = logic.classes(con)
    if user.role == "faculty":
        if user.dept:
            df = df[df["dept"] == user.dept]
        if user.sem:
            df = df[df["sem"] == user.sem]
        if user.section:
            df = df[df["section"] == user.section]
    return df.reset_index(drop=True)


def _show_notes(res: parsers.ImportResult):
    for w in res.warnings:
        st.info(w)
    if res.skipped:
        with st.expander(f"{len(res.skipped)} lines were not used"):
            st.caption("These had no day, or no date, or were not a CIE / SEE / lab test. "
                       "Add any that matter as rows in the table above.")
            st.text("\n".join(res.skipped[:200]))


# ---------------------------------------------------------------------------
# Timetable
# ---------------------------------------------------------------------------

def _timetable_editor(res: parsers.ImportResult, key: str) -> pd.DataFrame:
    df = res.rows.copy()
    df.insert(0, "Day", [WEEKDAYS[int(w)] for w in df["weekday"]])
    df = df.drop(columns=["weekday"]).rename(columns={"subject": "Subject", "kind": "Type"})
    edited = st.data_editor(
        df, key=key, num_rows="dynamic", width="stretch", hide_index=True,
        column_config={
            "Day": st.column_config.SelectboxColumn(options=WEEKDAYS[:6], required=True),
            "Subject": st.column_config.TextColumn(required=True),
            "Type": st.column_config.SelectboxColumn(options=["class", "lab"], required=True),
        })
    edited = edited.dropna(subset=["Day", "Subject"])
    return pd.DataFrame({"weekday": [WEEKDAYS.index(d) for d in edited["Day"]],
                         "subject": edited["Subject"], "kind": edited["Type"].fillna("class")})


def render_timetable_import(con, user):
    st.subheader("Import your weekly timetable")
    st.caption("Upload the department timetable as Excel or CSV, or a clear photo or screenshot of it. "
               "You can fix any row before saving. Batch-wise labs show up as two rows: delete the one "
               "that is not your batch.")

    for_class = user.role in ("faculty", "admin") and auth.can(user, "import_calendar")
    if not for_class and not (user.role == "athlete" and auth.can(user, "view_own_data")):
        st.warning("You do not have permission to import timetables.")
        return

    target = None
    if for_class:
        cls = _scoped_classes(con, user)
        if cls.empty:
            st.info("No athletes in your classes yet. Import the roster first.")
            return
        labels = [f"{_class_label(r.dept, r.sem, r.section)} ({r.athletes} athletes)" for r in cls.itertuples()]
        i = st.selectbox("Class", range(len(labels)), format_func=lambda j: labels[j], key="imp_tt_class")
        target = cls.iloc[i].to_dict()

    fmt = "CSV, XLSX" + (", PNG, JPG" if parsers.ocr_available() else "")
    up = st.file_uploader(f"Timetable file ({fmt})", key="imp_tt_file",
                          type=["csv", "xlsx", "xls"] + (["png", "jpg", "jpeg"] if parsers.ocr_available() else []))
    st.download_button("Download a sample sheet", parsers.timetable_template_csv(),
                       "timetable-sample.csv", "text/csv", key="imp_tt_tpl")
    if not up:
        return
    try:
        res = parsers.parse_timetable_file(up.getvalue(), up.name)
    except Exception as e:  # noqa: BLE001 - show the person what went wrong
        st.error(f"Could not read that file: {e}")
        return

    st.markdown(f"**{len(res.rows)} weekly slots found.** Check them, then save.")
    rows = _timetable_editor(res, key=f"imp_tt_edit_{up.name}")
    _show_notes(res)

    if target is not None:
        label = _class_label(target["dept"], target["sem"], target["section"])
        if st.button(f"Save for all {int(target['athletes'])} athletes in {label}", type="primary", key="imp_tt_save"):
            r = logic.save_class_timetable(con, target["dept"], target["sem"], target["section"] or None,
                                           rows, up.name, user.id)
            st.success(f"Saved {r['slots']} slots to {r['athletes']} athletes' timetables.")
    else:
        if st.button("Replace my timetable with this", type="primary", key="imp_tt_save"):
            n = logic.save_athlete_timetable(con, user.athlete_id, rows, up.name, user.id)
            st.success(f"Saved {n} weekly slots. The Clashes tab now uses this timetable.")


# ---------------------------------------------------------------------------
# Exam calendar
# ---------------------------------------------------------------------------

def render_exam_calendar_import(con, user):
    st.subheader("Import the exam calendar")
    st.caption("Upload the college calendar PDF once a semester. CIE, SEE and lab test dates are "
               "added for every athlete in the classes you pick, and to athletes added to those "
               "classes later.")
    if not auth.can(user, "import_calendar"):
        st.warning("Only faculty and admins can import the exam calendar.")
        return

    cls = _scoped_classes(con, user)
    if cls.empty:
        st.info("No athletes in your classes yet. Import the roster first.")
        return
    # Offer each dept+sem (all sections) plus each individual section
    options = []
    for (dept, sem), g in cls.groupby(["dept", "sem"], sort=True):
        options.append((dept, sem, None, int(g["athletes"].sum())))
        if len(g) > 1 or g["section"].iloc[0]:
            options += [(dept, sem, r.section, int(r.athletes)) for r in g.itertuples()]
    def fmt(o):
        dept, sem, sec, n = o
        return f"{dept} {sem} sem, " + (f"section {sec}" if sec else "all sections") + f" ({n})"
    picked = st.multiselect("Apply to", options, format_func=fmt, key="imp_ex_targets")

    c1, c2 = st.columns([3, 1])
    up = c1.file_uploader("Calendar file (PDF, XLSX or CSV)", type=["pdf", "xlsx", "xls", "csv"], key="imp_ex_file")
    year = c2.number_input("Year if missing", 2020, 2100, pd.Timestamp.today().year, key="imp_ex_year")
    if not up:
        return
    try:
        res = parsers.parse_exam_calendar_file(up.getvalue(), up.name, default_year=None)
        if any("no year" in w for w in res.warnings):
            res = parsers.parse_exam_calendar_file(up.getvalue(), up.name, default_year=int(year))
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read that file: {e}")
        return

    counts = res.rows["kind"].value_counts().to_dict() if res.ok else {}
    st.markdown(f"**{len(res.rows)} dates found:** " +
                ", ".join(f"{counts.get(k, 0)} {k}" for k in ("CIE", "SEE", "lab")) +
                ". Untick anything that is not for these classes.")
    df = res.rows.copy()
    df.insert(0, "Use", True)
    edited = st.data_editor(
        df, key=f"imp_ex_edit_{up.name}", num_rows="dynamic", hide_index=True, width="stretch",
        column_config={
            "Use": st.column_config.CheckboxColumn(default=True),
            "date": st.column_config.DateColumn("Date", format="ddd DD MMM YYYY", required=True),
            "kind": st.column_config.SelectboxColumn("Type", options=["CIE", "SEE", "lab"], required=True),
            "title": st.column_config.TextColumn("Title", required=True),
        })
    _show_notes(res)

    chosen = edited[edited["Use"].fillna(False)].dropna(subset=["date", "kind", "title"])
    disabled = chosen.empty or not picked
    if st.button(f"Add {len(chosen)} dates to {len(picked)} class(es)", type="primary",
                 disabled=disabled, key="imp_ex_save"):
        r = logic.apply_exam_calendar(con, [(d, s, sec) for d, s, sec, _ in picked],
                                      chosen[["date", "kind", "title"]], up.name, user.id)
        st.success(f"Added {r['events']} dates for {r['athletes']} athletes. Clash checks now include them.")

    hist = logic.import_history(con, 10)
    if not hist.empty:
        with st.expander("Recent imports"):
            st.dataframe(hist[["imported_at", "kind", "dept", "sem", "section", "source_name", "rows", "athletes"]],
                         hide_index=True, width="stretch")
