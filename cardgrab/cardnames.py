"""Recovering a player name from a TCDB card URL.

Checklist link text often carries only the card number, but the card URL slug
spells out the player:

    .../ViewCard.cfm/sid/598914/cid/34990918/2026-Panini-...-Cup-1-Lionel-Messi
                                                            ^^^^^^^^^^^^^^^^^^

Pure string work -- no network, so it also repairs manifests already written.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

_TRAILING_QUERY = re.compile(r"[?#].*$")


def name_from_card_url(url: str, number: str) -> str:
    """Extract the player name that follows the card number in the URL slug."""
    if not url or not number:
        return ""

    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = unquote(_TRAILING_QUERY.sub("", slug))
    if not slug:
        return ""

    # Anchor on the card number. Search from the right so a number that also
    # appears in the year or set name cannot win.
    pattern = re.compile(rf"-{re.escape(number)}-(.+)$", re.IGNORECASE)
    matches = list(pattern.finditer(slug))
    if not matches:
        return ""

    tail = matches[-1].group(1)
    name = tail.replace("-", " ").strip()
    # Drop a trailing side marker if the slug carries one.
    name = re.sub(r"\s+(front|back)$", "", name, flags=re.IGNORECASE)
    return " ".join(name.split())
