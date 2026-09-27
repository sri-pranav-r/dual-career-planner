FEATURE = {
    "name": "squad",
    "tabs": [
        {"label": "Make-up tests", "roles": {"athlete", "coach", "faculty", "admin"}, "render": "ui:render_makeups"},
        {"label": "Squad selection", "roles": {"coach", "admin"}, "render": "ui:render_selection"},
        {"label": "Bulk letters", "roles": {"coach", "admin"}, "render": "ui:render_bulk_letters"},
        {"label": "Attendance risk", "roles": {"athlete", "coach", "faculty", "admin"}, "render": "ui:render_attendance"},
    ],
    "public_routes": {},
    "hooks": {
        "tournament_updated": "logic:on_tournament_changed",
        "entries_changed": "logic:on_tournament_changed",
        "athlete_deleted": "logic:on_athlete_deleted",
    },
}
