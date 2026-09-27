"""
The one way to produce an exemption letter, so the admin's wording and the
verification QR are applied the same everywhere (single download and bulk).
"""
from __future__ import annotations

import importlib

import core
import data
import settings


def make_letter(con, athlete: dict, tournament: dict, clashes=None) -> bytes:
    aid, tid = int(athlete["id"]), int(tournament["id"])
    if clashes is None:
        clashes = core.find_clashes(tournament, data.timetable(con, aid), data.events(con, aid))
    docx = core.build_letter_docx(athlete, tournament, clashes, template=settings.letter_template(con))
    try:
        verification = importlib.import_module("features.verification.logic")
    except ImportError:
        return docx
    return verification.letter_with_qr(con, docx, aid, tid, base_url=settings.base_url(con))
