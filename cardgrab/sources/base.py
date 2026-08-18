"""The seam between where card records come from and what we do with them.

Any object exposing cards() can drive the pipeline, so a new source (an
official API, an export, a different site) plugs in without touching the
pipeline, naming, dedupe or reporting code.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cardgrab.models import CardRef


@runtime_checkable
class CardSource(Protocol):
    """Supplies the checklist of cards a collection contains."""

    def cards(self) -> list[CardRef]:
        """Return every card in the collection, in checklist order."""
        ...

    def describe(self) -> str:
        """Short human-readable account of where these records came from."""
        ...


class NullSource:
    """No checklist available.

    Images are still harvested, deduped and organised; they simply land in
    _unmatched/ because there is nothing to name them after. This keeps the
    tool useful before a checklist has been captured.
    """

    def cards(self) -> list[CardRef]:
        return []

    def describe(self) -> str:
        return "no checklist source configured"
