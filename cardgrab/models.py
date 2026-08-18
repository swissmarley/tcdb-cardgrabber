"""Core data structures shared across the pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# A plain card number: digits, optionally with a variant letter (25a).
_PLAIN_NUMBER = re.compile(r"^(\d+)([A-Za-z]*)$")


@dataclass(frozen=True)
class CardRef:
    """One card as described by a collection's checklist."""

    number: str
    name: str
    card_url: str = ""
    image_hints: tuple[str, ...] = ()

    @property
    def sort_key(self) -> tuple[int, int, str]:
        """Plain numbers sort numerically; prefixed ones (RC-4) go last.

        Bucketing matters: extracting any digit run would sort RC-4 between
        cards 2 and 10.
        """
        match = _PLAIN_NUMBER.match(self.number.strip())
        if match:
            return (0, int(match.group(1)), match.group(2).lower())
        return (1, 0, self.number.lower())


@dataclass(frozen=True)
class ImageAsset:
    """A candidate image file found in the browser's output directory."""

    path: Path
    sha256: str
    width: int
    height: int
    ext: str

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0

    @property
    def pixels(self) -> int:
        return self.width * self.height


@dataclass
class MatchResult:
    """An asset bound to a card, or deliberately left unbound."""

    asset: ImageAsset
    card: CardRef | None = None
    side: str = "front"
    strategy: str = "unmatched"

    @property
    def matched(self) -> bool:
        return self.card is not None


@dataclass
class RunStats:
    """Accounting for a single pipeline run."""

    scanned: int = 0
    rejected_junk: int = 0
    rejected_unreadable: int = 0
    duplicates: int = 0
    written: int = 0
    unmatched: int = 0
    skipped_existing: int = 0
    dimensions: list[tuple[int, int]] = field(default_factory=list)
