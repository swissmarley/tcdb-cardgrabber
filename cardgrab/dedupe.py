"""Content-identity deduplication.

Browser saves routinely write the same image several times under different
names. Hashing the bytes is what makes "no duplicates" true regardless of what
the files are called, and it is also what makes re-runs idempotent.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024


def sha256(path: Path) -> str:
    """Stream a file through SHA-256 so large scans stay memory-flat."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class SeenSet:
    """Tracks content hashes already accounted for in this collection."""

    def __init__(self) -> None:
        self._hashes: dict[str, Path] = {}

    def add(self, digest: str, path: Path) -> bool:
        """Register a hash. Returns False if it was already present."""
        if digest in self._hashes:
            return False
        self._hashes[digest] = path
        return True

    def origin(self, digest: str) -> Path | None:
        """The first path that carried this hash."""
        return self._hashes.get(digest)

    def __contains__(self, digest: object) -> bool:
        return digest in self._hashes

    def __len__(self) -> int:
        return len(self._hashes)

    def index_existing(self, directory: Path) -> int:
        """Hash images already in the output folder so re-runs skip them.

        This is what stops a second run from producing 'image(1).jpg'.
        """
        if not directory.is_dir():
            return 0
        count = 0
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                if self.add(sha256(path), path):
                    count += 1
        return count
