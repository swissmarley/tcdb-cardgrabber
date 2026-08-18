"""Finding real card images inside a browser's output directory.

A saved page is mostly noise: sprites, icons, avatars, banners, tracking
pixels. Cards are separated by shape and size, which is far more robust than
trusting filenames.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cardgrab.config import CollectionConfig
from cardgrab.dedupe import sha256
from cardgrab.imagemeta import SUPPORTED_EXTENSIONS, UnreadableImage, probe
from cardgrab.models import ImageAsset


@dataclass
class Rejection:
    """A file that was examined and deliberately not imported."""

    path: Path
    reason: str


@dataclass
class HarvestResult:
    assets: list[ImageAsset]
    rejections: list[Rejection]

    @property
    def unreadable(self) -> int:
        return sum(1 for r in self.rejections if r.reason.startswith("unreadable"))

    @property
    def junk(self) -> int:
        return sum(1 for r in self.rejections if not r.reason.startswith("unreadable"))


def scan(config: CollectionConfig) -> HarvestResult:
    """Walk the input directories and classify every image file found."""
    assets: list[ImageAsset] = []
    rejections: list[Rejection] = []

    for path in _iter_files(config.input_dirs):
        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue

        try:
            width, height = probe(path)
        except UnreadableImage as exc:
            rejections.append(Rejection(path, f"unreadable: {exc}"))
            continue

        asset = ImageAsset(
            path=path, sha256="", width=width, height=height, ext=ext
        )
        reason = _reject_reason(asset, config)
        if reason:
            rejections.append(Rejection(path, reason))
            continue

        # Hash only what survives the cheap filters.
        assets.append(
            ImageAsset(
                path=path,
                sha256=sha256(path),
                width=width,
                height=height,
                ext=ext,
            )
        )

    assets.sort(key=lambda a: str(a.path))
    return HarvestResult(assets=assets, rejections=rejections)


def _iter_files(directories: list[Path]):
    """Yield every file under the input directories, without repeats.

    Overlapping or nested input_dirs would otherwise visit files twice.
    """
    seen: set[Path] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path


def _reject_reason(asset: ImageAsset, config: CollectionConfig) -> str | None:
    """Return why this image is not a card, or None if it looks like one."""
    if asset.width < config.min_width or asset.height < config.min_height:
        return (
            f"too small: {asset.width}x{asset.height} "
            f"(minimum {config.min_width}x{config.min_height})"
        )
    if not config.aspect_min <= asset.aspect <= config.aspect_max:
        return (
            f"aspect {asset.aspect:.2f} outside "
            f"{config.aspect_min:.2f}-{config.aspect_max:.2f}"
        )
    return None
