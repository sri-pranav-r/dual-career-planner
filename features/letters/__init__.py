FEATURE = {
    "name": "letters",
    "tabs": [
        {"label": "Letter status", "roles": {"athlete", "coach", "faculty", "admin"}, "render": "ui:render_status"},
        {"label": "Notifications", "roles": {"athlete"}, "render": "ui:render_inbox"},
    ],
    "public_routes": {},
    "hooks": {
        "tournament_created": "logic:on_tournament_created",
        "tournament_updated": "logic:on_tournament_updated",
        "entries_changed": "logic:on_entries_changed",
        "athlete_deleted": "logic:on_athlete_deleted",
    },
}
