"""
Tiny event bus. Core and features call `emit`; features register handlers
through the "hooks" entry of their FEATURE dict (see features/__init__.py).

A failing handler never breaks the action that emitted the event: the error
is recorded in `last_errors` and the remaining handlers still run.
"""
from __future__ import annotations

import traceback
from collections import defaultdict
from typing import Callable

_handlers: dict[str, list[Callable]] = defaultdict(list)
last_errors: list[str] = []


def on(event: str, fn: Callable) -> None:
    if fn not in _handlers[event]:
        _handlers[event].append(fn)


def clear() -> None:
    _handlers.clear()
    last_errors.clear()


def emit(con, event: str, payload: dict) -> None:
    for fn in list(_handlers.get(event, [])):
        try:
            fn(con, payload)
        except Exception:  # noqa: BLE001 - a feature bug must not block core writes
            last_errors.append(f"{event} -> {getattr(fn, '__module__', '?')}.{getattr(fn, '__name__', '?')}\n{traceback.format_exc()}")
