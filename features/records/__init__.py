"""Athlete records: injuries, calendar, taper, semester summary, CSV export, consent and data deletion."""

FEATURE = {
    "name": "records",
    "tabs": [
        {"label": "Calendar", "roles": {"athlete", "coach", "faculty", "admin"}, "render": "ui:render_calendar"},
        {"label": "Injuries", "roles": {"athlete", "coach", "admin"}, "render": "ui:render_injuries"},
        {"label": "Taper plan", "roles": {"athlete", "coach", "admin"}, "render": "ui:render_taper"},
        {"label": "Semester summary", "roles": {"athlete", "coach", "faculty", "admin"}, "render": "ui:render_summary"},
        {"label": "Export", "roles": {"coach", "faculty", "admin"}, "render": "ui:render_export"},
        {"label": "Privacy & data", "roles": {"athlete", "admin"}, "render": "ui:render_privacy"},
    ],
    "public_routes": {},
    "hooks": {
        "athlete_deleted": "logic:on_athlete_deleted",
    },
}
