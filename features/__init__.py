"""
Feature loader. Each subfolder with an __init__.py that defines FEATURE is a
feature module (contract in INTERFACES.md). Core calls:

  apply_schemas(con)   run every features/<name>/schema.sql
  load_all()           import every FEATURE dict, resolving "module:function" strings
  register_hooks()     connect FEATURE["hooks"] to hooks.on
  tabs_for(role)       [(label, render_fn)] for that role, in load order
  public_route(params) the first (fn, value) whose key is in the URL query params

A feature that fails to import is skipped and its error kept in `load_errors`,
so one broken module never takes the app down.
"""
from __future__ import annotations

import importlib
import traceback
from pathlib import Path

_DIR = Path(__file__).parent
load_errors: dict[str, str] = {}
_loaded: list[dict] | None = None

# Tab order across features; unknown names go last, alphabetically.
ORDER = ["imports", "letters", "verification", "squad", "records", "reminders"]


def _feature_dirs():
    dirs = [p for p in _DIR.iterdir() if p.is_dir() and (p / "__init__.py").exists() and not p.name.startswith(("_", "."))]
    return sorted(dirs, key=lambda p: (ORDER.index(p.name) if p.name in ORDER else len(ORDER), p.name))


def apply_schemas(con) -> None:
    for d in _feature_dirs():
        sql = d / "schema.sql"
        if sql.exists():
            try:
                con.executescript(sql.read_text())
            except Exception:  # noqa: BLE001
                load_errors[d.name] = f"schema.sql failed:\n{traceback.format_exc()}"


def _resolve(pkg: str, ref):
    if callable(ref):
        return ref
    mod, fn = ref.split(":")
    return getattr(importlib.import_module(f"features.{pkg}.{mod}"), fn)


def load_all(force: bool = False) -> list[dict]:
    global _loaded
    if _loaded is not None and not force:
        return _loaded
    out = []
    for d in _feature_dirs():
        try:
            f = importlib.import_module(f"features.{d.name}").FEATURE
            out.append({
                "name": f.get("name", d.name),
                "tabs": [{"label": t["label"], "roles": set(t["roles"]), "render": _resolve(d.name, t["render"])}
                         for t in f.get("tabs", [])],
                "public_routes": {k: _resolve(d.name, v) for k, v in f.get("public_routes", {}).items()},
                "hooks": {k: _resolve(d.name, v) for k, v in f.get("hooks", {}).items()},
            })
        except Exception:  # noqa: BLE001
            load_errors[d.name] = traceback.format_exc()
    _loaded = out
    return out


def register_hooks() -> None:
    import hooks
    for f in load_all():
        for event, fn in f["hooks"].items():
            hooks.on(event, fn)


def tabs_for(role: str):
    return [(t["label"], t["render"]) for f in load_all() for t in f["tabs"] if role in t["roles"]]


def public_route(params):
    for f in load_all():
        for key, fn in f["public_routes"].items():
            if params.get(key):
                return fn, params.get(key)
    return None
