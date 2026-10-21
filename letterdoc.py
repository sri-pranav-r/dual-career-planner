"""
The one way to produce an exemption letter, so the admin's wording and the
verification QR are applied the same everywhere (single download and bulk).
"""
from __future__ import annotations

import importlib

import core
import data
import settings


def _clashes(con, athlete, tournament, clashes):
    if clashes is None:
        aid = int(athlete["id"])
        clashes = core.find_clashes(tournament, data.timetable(con, aid), data.events(con, aid))
    return clashes


def make_email(con, athlete: dict, tournament: dict, clashes=None) -> dict:
    """The same request as an email ({to, cc, subject, body}); the college accepts either."""
    return core.build_letter_email(athlete, tournament, _clashes(con, athlete, tournament, clashes),
                                   template=settings.letter_template(con))


def make_letter(con, athlete: dict, tournament: dict, clashes=None) -> bytes:
    aid, tid = int(athlete["id"]), int(tournament["id"])
    clashes = _clashes(con, athlete, tournament, clashes)
    docx = core.build_letter_docx(athlete, tournament, clashes, template=settings.letter_template(con))
    try:
        verification = importlib.import_module("features.verification.logic")
    except ImportError:
        return docx
    return verification.letter_with_qr(con, docx, aid, tid, base_url=settings.base_url(con))
