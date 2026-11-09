"""Stale-input notes for the evening slate. Silent unless the run is armed."""

from __future__ import annotations

_armed = False
_notes: list[str] = []


def arm() -> None:
    global _armed
    _armed = True
    _notes.clear()


def note(message: str) -> None:
    if not _armed:
        return
    text = message.strip()
    if text and text not in _notes:
        _notes.append(text)


def drain() -> list[str]:
    global _armed
    found = list(_notes)
    _notes.clear()
    _armed = False
    return found
