"""The menu: every feature tab stays reachable, and every page explains itself."""
import features
import nav

ROLES = ["athlete", "coach", "faculty", "admin"]


def _pages(role, core=lambda name: True):
    return nav.build(role, dict(features.tabs_for(role)), core)


def test_every_feature_tab_has_a_page():
    for role in ROLES:
        shown = {name for p in _pages(role) for _, kind, name in p.parts if kind == "feature"}
        assert shown == {label for label, _ in features.tabs_for(role)}, role


def test_known_features_are_grouped_not_left_in_more():
    for role in ROLES:
        assert [p.title for p in _pages(role) if p.section == nav.MORE] == [], role


def test_page_keys_are_unique_and_explained():
    for role in ROLES:
        pages = _pages(role)
        assert len({p.key for p in pages}) == len(pages), role
        assert all(p.blurb and p.section and p.icon.startswith(":material/") for p in pages), role


def test_unknown_feature_tab_lands_under_more():
    pages = nav.build("coach", {"Brand new thing": lambda con, user: None}, lambda name: False)
    assert [(p.section, p.title) for p in pages] == [(nav.MORE, "Brand new thing")]


def test_core_parts_follow_permissions():
    pages = nav.build("coach", {}, lambda name: name in ("staff_home", "squad"))
    assert [p.key for p in pages] == ["home", "squad"]


def test_current_page_falls_back_to_the_first():
    pages = _pages("athlete")
    assert nav.current(pages, {"p": "clashes"}).key == "clashes"
    assert nav.current(pages, {"p": "users"}).key == "home"
    assert nav.current(pages, {}).key == "home"
