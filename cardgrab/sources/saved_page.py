"""Checklist extraction from a browser-saved TCDB set page.

Written defensively on purpose. The live DOM could not be inspected during
design because the origin is challenge-gated, so this parser tries several
independent strategies and reports which one produced the records rather than
depending on one fragile selector. Use `--inspect` to see what it found.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from cardgrab.models import CardRef

# A card number cell: optionally '#'-prefixed digits, possibly with a suffix
# letter or a hyphenated prefix such as RC-12 or 25a.
_NUMBER_CELL = re.compile(r"^\s*#?\s*([A-Z]{0,4}-?\d+[A-Za-z]?)\s*$")
_CARD_HREF = re.compile(r"(?:ViewCard|/Card/)", re.IGNORECASE)
_IMAGE_SRC = re.compile(r"\.(?:jpe?g|png|gif|webp)(?:\?|$)", re.IGNORECASE)
# Side markers appear after a dash, underscore OR space: files are named both
# "1-Back.jpg" and "1 - Lionel Messi - back.jpg". Single letters are only
# trusted after a dash/underscore, so a stray word cannot be read as a side.
_SIDE_WORD = re.compile(r"[-_\s](front|back|fr|bk|obv|rev)\b", re.IGNORECASE)
_SIDE_ABBR = re.compile(r"[-_](f|b)\b", re.IGNORECASE)
_BACK_TOKENS = {"back", "bk", "b", "rev"}


class _TableHarvester(HTMLParser):
    """Collects table rows, anchors and image sources in one pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.anchors: list[tuple[str, str]] = []
        self.images: list[str] = []

        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}

        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "a":
            href = attr.get("href", "")
            if href:
                self._anchor_href = href
                self._anchor_text = []
        elif tag == "img":
            # Lazy-loaded galleries keep the real URL in a data attribute.
            for key in ("src", "data-src", "data-original", "data-lazy"):
                value = attr.get(key, "")
                if value and _IMAGE_SRC.search(value):
                    self.images.append(value)
                    break

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            if self._row is not None:
                self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell for cell in self._row):
                self.rows.append(self._row)
            self._row = None
        elif tag == "a" and self._anchor_href is not None:
            text = " ".join("".join(self._anchor_text).split())
            self.anchors.append((self._anchor_href, text))
            self._anchor_href = None
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        if self._anchor_href is not None:
            self._anchor_text.append(data)


class SavedPageSource:
    """Parses one or more saved checklist pages into CardRefs."""

    def __init__(self, paths: list[Path]) -> None:
        self.paths = paths
        self.strategy = "none"
        self.image_urls: list[str] = []

    def describe(self) -> str:
        names = ", ".join(p.name for p in self.paths) or "nothing"
        return f"saved page(s): {names} [strategy: {self.strategy}]"

    def cards(self) -> list[CardRef]:
        found: dict[str, CardRef] = {}
        strategies: set[str] = set()

        for path in self.paths:
            markup = _read(path)
            harvester = _TableHarvester()
            try:
                harvester.feed(markup)
            except Exception:
                # A malformed save should degrade, not abort the whole run.
                pass

            self.image_urls.extend(harvester.images)

            for card, strategy in _extract(harvester):
                strategies.add(strategy)
                # First sighting wins; later pages only fill gaps.
                found.setdefault(card.number, card)

        self.strategy = "+".join(sorted(strategies)) if strategies else "none"
        return sorted(found.values(), key=lambda c: c.sort_key)


def _read(path: Path) -> str:
    """Read a saved page, tolerating whatever encoding the browser used."""
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


def _extract(harvester: _TableHarvester) -> list[tuple[CardRef, str]]:
    """Try table rows first, then fall back to card-page anchors."""
    results = _from_rows(harvester.rows)
    if results:
        return results
    return _from_anchors(harvester.anchors)


def _from_rows(rows: list[list[str]]) -> list[tuple[CardRef, str]]:
    """Checklist tables: a number cell followed by a name cell."""
    results: list[tuple[CardRef, str]] = []
    for row in rows:
        if len(row) < 2:
            continue
        match = _NUMBER_CELL.match(row[0])
        if not match:
            continue
        name = next((cell for cell in row[1:] if cell), "")
        if not name:
            continue
        results.append((CardRef(number=match.group(1), name=name), "table-row"))
    return results


def _from_anchors(anchors: list[tuple[str, str]]) -> list[tuple[CardRef, str]]:
    """Gallery layouts: anchors to card pages carrying 'number name' text."""
    results: list[tuple[CardRef, str]] = []
    for href, text in anchors:
        if not _CARD_HREF.search(href) or not text:
            continue
        parts = text.split(None, 1)
        if not parts:
            continue
        match = _NUMBER_CELL.match(parts[0])
        if not match:
            continue
        name = parts[1].strip() if len(parts) > 1 else ""
        results.append(
            (CardRef(number=match.group(1), name=name, card_url=href), "anchor")
        )
    return results


def side_from_name(text: str) -> str | None:
    """Read front/back out of a filename or URL, if it says.

    The last marker wins: the side sits at the end of the name, while a player
    name earlier in the string could coincidentally contain one.
    """
    matches = _SIDE_WORD.findall(text) or _SIDE_ABBR.findall(text)
    if not matches:
        return None
    return "back" if matches[-1].lower() in _BACK_TOKENS else "front"
