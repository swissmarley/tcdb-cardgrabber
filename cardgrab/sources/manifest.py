"""Checklist from a JSON manifest.

The manifest is produced by browser/collect_manifest.js, which reads the DOM of
a page already open in the user's browser. Useful when a page's markup defeats
the HTML parser, and as the interchange format for any future source.
"""

from __future__ import annotations

import json
from pathlib import Path

from cardgrab.cardnames import name_from_card_url
from cardgrab.models import CardRef


class ManifestError(Exception):
    """Raised when a manifest file is missing or structurally wrong."""


class ManifestSource:
    """Reads CardRefs from a JSON manifest file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._count = 0

    def describe(self) -> str:
        return f"manifest: {self.path.name} ({self._count} records)"

    def cards(self) -> list[CardRef]:
        if not self.path.is_file():
            raise ManifestError(f"no such manifest: {self.path}")

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ManifestError(f"cannot read {self.path}: {exc}") from exc

        # Accept either a bare list or {"cards": [...]} from the snippet.
        records = raw.get("cards", []) if isinstance(raw, dict) else raw
        if not isinstance(records, list):
            raise ManifestError(
                f"{self.path}: expected a list of cards, got {type(records).__name__}"
            )

        cards: dict[str, CardRef] = {}
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ManifestError(
                    f"{self.path}: entry {index} is {type(record).__name__}, not an object"
                )
            number = str(record.get("number", "")).strip()
            if not number:
                continue

            card_url = str(record.get("card_url", "")).strip()
            name = str(record.get("name", "")).strip()
            # Checklist links often carry only the number; the URL slug has the
            # player, so recover it rather than leaving the card unnamed.
            if not name and card_url:
                name = name_from_card_url(card_url, number)

            hints = [
                str(record[key])
                for key in ("front_url", "back_url", "image_url")
                if record.get(key)
            ]
            cards.setdefault(
                number,
                CardRef(
                    number=number,
                    name=name,
                    card_url=card_url,
                    image_hints=tuple(hints),
                ),
            )

        self._count = len(cards)
        return sorted(cards.values(), key=lambda c: c.sort_key)
