"""Reminders outside the app, logging nudges and the weekly team load report."""

FEATURE = {
    "name": "reminders",
    "tabs": [
        {"label": "Reminders", "roles": {"coach", "admin"}, "render": "ui:render_outbox"},
        {"label": "Team load report", "roles": {"coach", "admin"}, "render": "ui:render_team_report"},
        {"label": "Reminder settings", "roles": {"athlete"}, "render": "ui:render_athlete_settings"},
    ],
    "public_routes": {},
    "hooks": {
        "athlete_deleted": "logic:on_athlete_deleted",
    },
}
