"""Next 14 days: one combined study + training plan per athlete, and pilot logging of whether it was followed."""

FEATURE = {
    "name": "plan",
    "tabs": [
        {"label": "Next 14 days", "roles": {"athlete", "coach", "admin"}, "render": "ui:render_plan"},
        {"label": "Pilot results", "roles": {"coach", "admin"}, "render": "ui:render_pilot"},
    ],
    "public_routes": {},
    "hooks": {
        "athlete_deleted": "logic:on_athlete_deleted",
    },
}
