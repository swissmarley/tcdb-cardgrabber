"""Output file naming.

Produces names like "012 - Lionel Messi - front.jpg": sortable, readable, and
obvious when a card is missing.
"""

from __future__ import annotations

import re

_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
_LEADING_DIGITS = re.compile(r"^\s*#?\s*(\d+)")

VALID_SIDES = ("front", "back")


def sanitize(text: str) -> str:
    """Strip characters that are unsafe or awkward in a filename."""
    cleaned = _UNSAFE.sub("", text)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip(" .")


def number_width(numbers: list[str]) -> int:
    """Padding width derived from the longest numeric card number in the set.

    Padding to the set's own width is what makes 9 sort before 10 in Finder.
    """
    widths = [
        len(match.group(1))
        for number in numbers
        if (match := _LEADING_DIGITS.match(number))
    ]
    return max(widths) if widths else 0


def pad(number: str, width: int) -> str:
    """Zero-pad a numeric card number, leaving oddball numbers untouched."""
    match = _LEADING_DIGITS.match(number)
    if not match or width <= 0:
        return sanitize(number) or "unknown"
    digits = match.group(1)
    remainder = number[match.end():].strip()
    padded = digits.zfill(width)
    return sanitize(f"{padded}{remainder}") if remainder else padded


def filename(number: str, player: str, side: str, ext: str, width: int) -> str:
    """Build the output filename for one card image."""
    if side not in VALID_SIDES:
        raise ValueError(f"side must be one of {VALID_SIDES}, got {side!r}")

    parts = [pad(number, width)]
    player_clean = sanitize(player)
    if player_clean:
        parts.append(player_clean)
    parts.append(side)

    ext = ext if ext.startswith(".") else f".{ext}"
    return " - ".join(parts) + ext.lower()


def disambiguate(name: str, taken: set[str]) -> str:
    """Append a counter when two distinct images want the same name.

    Distinct bytes that collide on a name are rare but real (variants, parallels
    listed under one number). Overwriting one with the other would lose an image.
    """
    if name not in taken:
        return name

    stem, _, ext = name.rpartition(".")
    counter = 2
    while f"{stem} ({counter}).{ext}" in taken:
        counter += 1
    return f"{stem} ({counter}).{ext}"
