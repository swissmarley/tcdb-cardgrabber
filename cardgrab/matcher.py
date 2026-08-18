"""Binding harvested images to checklist cards.

Strategies run most-reliable first and each match records which one produced
it, so a bad run can be diagnosed instead of guessed at. Anything unmatched is
kept aside rather than given a wrong name -- a silently misnamed card is worse
than a visible leftover.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from cardgrab.models import CardRef, ImageAsset, MatchResult
from cardgrab.sources.saved_page import side_from_name

_DIGIT_RUN = re.compile(r"\d+")
_WORD = re.compile(r"[A-Za-z]{4,}")
# A full card-number token including any prefix: RC-1, SP12, 25a, 7.
# Matched before bare digit runs, otherwise 'RC-1-Front.jpg' would find only
# '1' and be filed under card 1 -- a confidently wrong name.
_CARD_TOKEN = re.compile(r"[A-Za-z]{0,4}-?\d+[A-Za-z]?")


def match_all(
    assets: list[ImageAsset],
    cards: list[CardRef],
    image_urls: list[str] | None = None,
) -> list[MatchResult]:
    """Bind each asset to a card where possible."""
    if not cards:
        return [MatchResult(asset=a, side=_side(a.path)) for a in assets]

    by_hint = _hint_index(cards)
    by_number = {c.number.lower(): c for c in cards}
    by_padded = _padded_index(cards)
    by_surname = _surname_index(cards)
    url_order = _url_order(image_urls or [], cards)

    results: list[MatchResult] = []
    for asset in assets:
        stem = asset.path.stem
        side = _side(asset.path)

        card, strategy = _resolve(
            stem, asset.path.name, by_hint, by_number, by_padded, by_surname, url_order
        )
        results.append(
            MatchResult(
                asset=asset,
                card=card,
                side=side,
                strategy=strategy if card else "unmatched",
            )
        )

    return results


def _resolve(
    stem: str,
    filename: str,
    by_hint: dict[str, CardRef],
    by_number: dict[str, CardRef],
    by_padded: dict[str, CardRef],
    by_surname: dict[str, CardRef],
    url_order: dict[str, CardRef],
) -> tuple[CardRef | None, str]:
    """Try each strategy in descending order of reliability."""
    key = filename.lower()

    if card := by_hint.get(key):
        return card, "url-hint"
    if card := url_order.get(key):
        return card, "dom-order"

    # Full tokens first, so a prefixed number beats its bare digits.
    for token in sorted(_CARD_TOKEN.findall(stem), key=len, reverse=True):
        if card := by_number.get(token.lower()):
            return card, "number-token"

    # Then bare digit runs, longest first so '012' beats a stray '1'.
    for run in sorted(_DIGIT_RUN.findall(stem), key=len, reverse=True):
        if card := by_padded.get(run.lstrip("0") or "0"):
            return card, "number-token"
        if card := by_number.get(run.lower()):
            return card, "number-token"

    for word in _WORD.findall(stem):
        if card := by_surname.get(word.lower()):
            return card, "surname"

    return None, "unmatched"


def _side(path: Path) -> str:
    """Front unless the filename says otherwise; fronts are the common case."""
    return side_from_name(path.name) or "front"


def _basename(url: str) -> str:
    """Last path segment of a URL, percent-decoded, lowercased."""
    return unquote(urlparse(url).path).rsplit("/", 1)[-1].lower()


def _hint_index(cards: list[CardRef]) -> dict[str, CardRef]:
    """Map image basenames declared by the source to their cards."""
    index: dict[str, CardRef] = {}
    for card in cards:
        for hint in card.image_hints:
            if name := _basename(hint):
                index.setdefault(name, card)
    return index


def _padded_index(cards: list[CardRef]) -> dict[str, CardRef]:
    """Index numeric cards by their unpadded digits, so 012 finds card 12."""
    index: dict[str, CardRef] = {}
    for card in cards:
        digits = "".join(ch for ch in card.number if ch.isdigit())
        if digits:
            index.setdefault(digits.lstrip("0") or "0", card)
    return index


def _surname_index(cards: list[CardRef]) -> dict[str, CardRef]:
    """Index cards by distinctive words in the player name.

    Words shared by several cards are dropped: an ambiguous match is worse than
    no match, because it produces a confidently wrong filename.
    """
    counts: dict[str, int] = {}
    owner: dict[str, CardRef] = {}
    for card in cards:
        for word in {w.lower() for w in _WORD.findall(card.name)}:
            counts[word] = counts.get(word, 0) + 1
            owner.setdefault(word, card)
    return {word: card for word, card in owner.items() if counts[word] == 1}


def _url_order(image_urls: list[str], cards: list[CardRef]) -> dict[str, CardRef]:
    """Positional fallback: nth card image on the page is the nth card.

    Only trusted when the counts line up exactly, since a mismatch would shift
    every name by one.
    """
    basenames = [name for url in image_urls if (name := _basename(url))]
    if not basenames or len(basenames) != len(cards):
        return {}
    return dict(zip(basenames, cards))
