"""Faculty verification of exemption letters: QR link, approve / reject, department absences."""

FEATURE = {
    "name": "verification",
    "tabs": [
        {"label": "Letter approvals", "roles": {"athlete", "coach"}, "render": "ui:render_decisions"},
        {"label": "Upcoming absences", "roles": {"faculty", "admin"}, "render": "ui:render_department"},
    ],
    "public_routes": {
        "verify": "ui:render_verify",
    },
    "hooks": {
        "athlete_deleted": "logic:on_athlete_deleted",
    },
}
