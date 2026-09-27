"""Timetable import (sheet or photo) and exam calendar import (college PDF)."""

FEATURE = {
    "name": "imports",
    "tabs": [
        {"label": "Import timetable", "roles": {"athlete", "faculty", "admin"},
         "render": "ui:render_timetable_import"},
        {"label": "Import exam calendar", "roles": {"faculty", "admin"},
         "render": "ui:render_exam_calendar_import"},
    ],
    "public_routes": {},
    "hooks": {
        "roster_imported": "logic:on_roster_imported",
        "athlete_deleted": "logic:on_athlete_deleted",
    },
}
