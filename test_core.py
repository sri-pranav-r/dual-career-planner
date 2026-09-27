"""Run:  python3 -m pytest test_core.py   (or  python3 test_core.py)"""
from datetime import date
import pandas as pd
import core


def test_session_load():
    assert core.session_load(60, 7) == 420


def test_acwr_zones():
    assert core.acwr_zone(1.6)[0] == "High risk"
    assert core.acwr_zone(1.4)[0] == "Caution"
    assert core.acwr_zone(1.0)[0] == "Sweet spot"
    assert core.acwr_zone(0.5)[0] == "Under-trained"
    assert core.acwr_zone(None)[0] == "No data yet"


def test_acwr_needs_28_days():
    loads = pd.Series([100.0] * 27, index=pd.date_range("2026-01-01", periods=27))
    assert core.acwr_series(loads)["acwr"].isna().all()
    loads = pd.Series([100.0] * 28, index=pd.date_range("2026-01-01", periods=28))
    assert abs(core.acwr_series(loads)["acwr"].iloc[-1] - 1.0) < 1e-9


def test_acwr_spike():
    base = [100.0] * 21 + [250.0] * 7
    loads = pd.Series(base, index=pd.date_range("2026-01-01", periods=28))
    r = core.acwr_series(loads)["acwr"].iloc[-1]
    assert r > 1.5


def test_find_clashes_counts_travel_days_and_skips_sunday():
    tt = pd.DataFrame({"weekday": [0, 6], "subject": ["DS", "Never"], "kind": ["class", "class"]})
    ev = pd.DataFrame({"date": [date(2026, 10, 6)], "kind": ["CIE"], "title": ["DS CIE-1"]})
    t = {"name": "T", "start_date": date(2026, 10, 5), "end_date": date(2026, 10, 6), "travel_before": 1, "travel_after": 0}
    # window = Sun 4 Oct (travel), Mon 5 Oct, Tue 6 Oct
    cl = core.find_clashes(t, tt, ev)
    kinds = sorted(c.kind for c in cl)
    assert kinds == ["CIE", "class"]          # Monday class + Tuesday CIE; Sunday slot ignored
    assert cl[0].kind == "CIE"                # sorted most severe first


def test_letter_builds():
    a = {"name": "A", "usn": "1RV25CS001", "dept": "CSE", "sem": "3rd", "sport": "Cricket"}
    t = {"name": "T", "venue": "X", "start_date": date(2026, 10, 5), "end_date": date(2026, 10, 6), "travel_before": 0, "travel_after": 0}
    b = core.build_letter_docx(a, t, [])
    assert b[:2] == b"PK"


def _tours(rows):
    return pd.DataFrame(rows, columns=["id", "name", "start_date", "end_date"])


def test_upcoming_keeps_ongoing_and_drops_finished():
    today = date(2026, 10, 10)
    t = _tours([(1, "over", date(2026, 10, 1), date(2026, 10, 2)),
                (2, "on now", date(2026, 10, 9), date(2026, 10, 11)),
                (3, "later", date(2026, 10, 20), date(2026, 10, 21))])
    assert list(core.upcoming(t, today)["name"]) == ["on now", "later"]


def test_starting_within_excludes_past_tournaments():
    today = date(2026, 10, 10)
    t = _tours([(1, "past", date(2026, 9, 1), date(2026, 9, 2)),
                (2, "soon", date(2026, 10, 20), date(2026, 10, 21)),
                (3, "far", date(2026, 12, 20), date(2026, 12, 21))])
    assert list(core.starting_within(t, today, 30)["name"]) == ["soon"]


def test_find_clashes_accepts_numpy_travel_days():
    import numpy as np
    tt = pd.DataFrame({"weekday": [0], "subject": ["DS"], "kind": ["class"]})
    t = {"name": "T", "start_date": date(2026, 10, 6), "end_date": date(2026, 10, 6),
         "travel_before": np.int64(1), "travel_after": np.int64(0)}
    assert [c.kind for c in core.find_clashes(t, tt, pd.DataFrame())] == ["class"]


if __name__ == "__main__":
    import sys
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for f in fns:
        f(); print("ok", f.__name__)
    print(f"{len(fns)} tests passed")
