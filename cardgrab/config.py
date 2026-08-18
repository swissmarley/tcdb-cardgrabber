"""Collection configuration.

Everything that varies between collections lives in a TOML file, so adding a
new collection never requires touching code.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Trading cards are ~2.5 x 3.5 inches, an aspect ratio of about 0.714. The
# default band is wide enough for scan margins but tight enough to reject the
# square avatars and wide banners that dominate a saved page.
DEFAULT_ASPECT_MIN = 0.60
DEFAULT_ASPECT_MAX = 0.85
DEFAULT_MIN_WIDTH = 120
DEFAULT_MIN_HEIGHT = 120

VALID_LAYOUTS = ("flat", "split")


class ConfigError(Exception):
    """Raised when a collection file is missing keys or has bad values."""


@dataclass
class CollectionConfig:
    """The few parameters that define one collection."""

    name: str
    input_dirs: list[Path]
    output_root: Path
    set_url: str = ""
    min_width: int = DEFAULT_MIN_WIDTH
    min_height: int = DEFAULT_MIN_HEIGHT
    aspect_min: float = DEFAULT_ASPECT_MIN
    aspect_max: float = DEFAULT_ASPECT_MAX
    layout: str = "flat"
    keep_unmatched: bool = True
    checklist_html: list[Path] = field(default_factory=list)
    manifest_json: Path | None = None

    @property
    def output_dir(self) -> Path:
        """The collection folder: output root plus the collection's own name."""
        return self.output_root / safe_dirname(self.name)

    def validate(self) -> None:
        if not self.name.strip():
            raise ConfigError("'name' must not be empty")
        if not self.input_dirs:
            raise ConfigError("'input_dirs' must list at least one directory")
        if self.layout not in VALID_LAYOUTS:
            raise ConfigError(
                f"'layout' must be one of {VALID_LAYOUTS}, got {self.layout!r}"
            )
        if self.aspect_min <= 0 or self.aspect_max <= 0:
            raise ConfigError("aspect bounds must be positive")
        if self.aspect_min >= self.aspect_max:
            raise ConfigError(
                f"'aspect_min' ({self.aspect_min}) must be below "
                f"'aspect_max' ({self.aspect_max})"
            )
        if self.min_width < 0 or self.min_height < 0:
            raise ConfigError("minimum dimensions must not be negative")

        missing = [str(d) for d in self.input_dirs if not d.is_dir()]
        if missing:
            raise ConfigError(
                "input directory does not exist: " + ", ".join(missing)
            )


def safe_dirname(name: str) -> str:
    """Make a collection name safe to use as a directory name."""
    cleaned = "".join("-" if ch in '/\\:*?"<>|' else ch for ch in name)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip(" .") or "collection"


def _expand(value: str) -> Path:
    return Path(value).expanduser()


def load(path: Path) -> CollectionConfig:
    """Read and validate a collection TOML file."""
    if not path.is_file():
        raise ConfigError(f"no such collection file: {path}")

    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    for required in ("name", "input_dirs", "output_root"):
        if required not in raw:
            raise ConfigError(f"{path} is missing required key '{required}'")

    input_dirs = raw["input_dirs"]
    if isinstance(input_dirs, str):
        input_dirs = [input_dirs]

    checklist = raw.get("checklist_html", [])
    if isinstance(checklist, str):
        checklist = [checklist]

    manifest = raw.get("manifest_json")

    config = CollectionConfig(
        name=raw["name"],
        input_dirs=[_expand(d) for d in input_dirs],
        output_root=_expand(raw["output_root"]),
        set_url=raw.get("set_url", ""),
        min_width=int(raw.get("min_width", DEFAULT_MIN_WIDTH)),
        min_height=int(raw.get("min_height", DEFAULT_MIN_HEIGHT)),
        aspect_min=float(raw.get("aspect_min", DEFAULT_ASPECT_MIN)),
        aspect_max=float(raw.get("aspect_max", DEFAULT_ASPECT_MAX)),
        layout=raw.get("layout", "flat"),
        keep_unmatched=bool(raw.get("keep_unmatched", True)),
        checklist_html=[_expand(c) for c in checklist],
        manifest_json=_expand(manifest) if manifest else None,
    )
    config.validate()
    return config
