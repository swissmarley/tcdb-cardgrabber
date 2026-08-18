"""The `init` command: create a collection file without hand-writing TOML.

Asks a few questions, inspects the folder, and writes a ready-to-run config.
"""

from __future__ import annotations

import re
from pathlib import Path

from cardgrab.config import safe_dirname

TEMPLATE = """\
# {name}
# Created by `cardgrab init`. Edit freely -- it is just text.

name = "{name}"
{set_url_line}
# Where your browser put the downloaded images.
input_dirs = [{input_dirs}]

# The collection folder is created inside here, named after 'name' above.
output_root = "{output_root}"
{checklist_line}
# --- Optional tuning ---------------------------------------------------
# Cards are about 0.71 wide-to-tall. Widen if real cards get rejected.
aspect_min = {aspect_min}
aspect_max = {aspect_max}

# Raise these to ignore thumbnails once you have full-size images.
min_width = {min_width}
min_height = {min_height}

# "flat" = one folder. "split" = front/ and back/ subfolders.
layout = "flat"

# Keep images that could not be tied to a card, in _unmatched/.
keep_unmatched = true
"""


def slugify(name: str) -> str:
    """Turn a collection name into a filename-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "collection"


def find_checklist(directory: Path) -> tuple[str, Path] | None:
    """Look for a manifest or a saved HTML page in the download folder.

    Returns (kind, path) where kind is 'manifest' or 'html'.
    """
    if not directory.is_dir():
        return None

    manifests = sorted(directory.glob("*manifest*.json"))
    if manifests:
        return ("manifest", manifests[0])

    # Prefer the largest HTML file: the checklist page, not a stray fragment.
    pages = [p for p in directory.glob("*.htm*") if p.is_file()]
    if pages:
        return ("html", max(pages, key=lambda p: p.stat().st_size))

    return None


def count_images(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    return sum(
        1
        for p in directory.rglob("*")
        if p.is_file() and p.suffix.lower() in exts and not p.name.startswith(".")
    )


def render(
    name: str,
    input_dir: Path,
    output_root: Path,
    checklist: tuple[str, Path] | None,
    min_width: int = 120,
    min_height: int = 120,
) -> str:
    """Produce the TOML text for a collection."""
    if checklist and checklist[0] == "manifest":
        checklist_line = (
            f'\n# Checklist captured by browser/collect_manifest.js.\n'
            f'manifest_json = "{checklist[1]}"\n'
        )
    elif checklist:
        checklist_line = (
            f'\n# Checklist read from your saved page.\n'
            f'checklist_html = ["{checklist[1]}"]\n'
        )
    else:
        checklist_line = (
            "\n# No checklist found yet. Without one, images are still collected\n"
            "# and deduplicated, but land in _unmatched/ because there are no\n"
            "# card numbers or player names to name them after.\n"
            "# checklist_html = [\"/path/to/saved-page.html\"]\n"
            "# manifest_json = \"/path/to/cardgrab-manifest.json\"\n"
        )

    return TEMPLATE.format(
        name=name,
        set_url_line="",
        input_dirs=f'"{input_dir}"',
        output_root=output_root,
        checklist_line=checklist_line,
        aspect_min=0.60,
        aspect_max=0.85,
        min_width=min_width,
        min_height=min_height,
    )


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def interactive(
    name: str | None,
    input_dir: str | None,
    output_root: str | None,
    dest: Path | None,
    min_width: int,
) -> tuple[Path, str]:
    """Gather what is needed, prompting only for what was not supplied."""
    print("\ncardgrab init - let's set up a collection.\n")

    if not name:
        name = _ask("Collection name (exactly as you want the folder named)")
        while not name:
            print("  A name is required.")
            name = _ask("Collection name")

    if not input_dir:
        print("\nWhere did your browser put the downloaded images?")
        print("  Tip: drag the folder into this terminal to paste its path.")
        input_dir = _ask("Download folder", str(Path.home() / "Downloads"))

    folder = Path(input_dir).expanduser().resolve()
    if not folder.is_dir():
        print(f"\n  WARNING: {folder} does not exist yet.")
        print("  Create it and re-run, or the run will stop with an error.")
    else:
        found = count_images(folder)
        print(f"\n  Found {found} image file(s) in {folder}")

    checklist = find_checklist(folder)
    if checklist:
        kind = "manifest" if checklist[0] == "manifest" else "saved page"
        print(f"  Found a checklist ({kind}): {checklist[1].name}")
    else:
        print("  No checklist found in that folder (you can add one later).")

    if not output_root:
        output_root = _ask(
            "\nWhere should collection folders be created",
            str(Path.home() / "Pictures" / "CardCollections"),
        )

    text = render(
        name=name,
        input_dir=folder,
        output_root=Path(output_root).expanduser(),
        checklist=checklist,
        min_width=min_width,
        min_height=min_width,
    )

    if dest is None:
        dest = Path("collections") / f"{slugify(name)}.toml"

    return dest, text


def write(dest: Path, text: str, force: bool = False) -> bool:
    """Write the config, refusing to clobber an existing one unless forced."""
    if dest.exists() and not force:
        print(f"\ncardgrab: {dest} already exists. Use --force to overwrite.")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return True
