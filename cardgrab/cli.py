"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cardgrab import initialize, pipeline
from cardgrab.config import ConfigError, load
from cardgrab.sources.manifest import ManifestError

DESCRIPTION = """\
Organise browser-downloaded trading card images into a clean collection folder.

cardgrab makes no network requests. Download the images with your own browser
first, then let cardgrab sort, name and deduplicate them.

Download a set's images, then organise them:
  .venv/bin/python -m cardgrab grab --set-url "<set page>" --out ~/Downloads/monopoly
  python3 -m cardgrab auto ~/Downloads/monopoly

For a collection you will re-run often, save the settings to a file:
  python3 -m cardgrab init
  python3 -m cardgrab run collections/my-set.toml
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cardgrab",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser(
        "init",
        help="create a collection config (asks a few questions)",
        description="Create a collection config file. Prompts for anything not given.",
    )
    init.add_argument("--name", help="collection name, used as the folder name")
    init.add_argument("--input-dir", help="folder holding the downloaded images")
    init.add_argument("--output-root", help="where collection folders are created")
    init.add_argument("-o", "--out", type=Path, help="path for the new .toml")
    init.add_argument(
        "--min-width",
        type=int,
        default=120,
        help="ignore images narrower than this (raise it to skip thumbnails)",
    )
    init.add_argument("--force", action="store_true", help="overwrite an existing file")

    auto = sub.add_parser(
        "auto",
        help="one-shot: point it at a folder, get the collection (no config file)",
        description=(
            "Process a folder of downloaded card images without a config file. "
            "The collection is named after the folder unless --name is given."
        ),
    )
    auto.add_argument("folder", type=Path, help="folder holding the downloaded images")
    auto.add_argument("--name", help="collection name (default: the folder's name)")
    auto.add_argument(
        "--output-root",
        type=Path,
        default=Path.home() / "Pictures" / "CardCollections",
        help="where the collection folder is created",
    )
    auto.add_argument(
        "--min-width", type=int, default=120, help="ignore images narrower than this"
    )
    auto.add_argument("--inspect", action="store_true", help="show what it sees only")
    auto.add_argument("--dry-run", action="store_true", help="report but write nothing")

    sets = sub.add_parser(
        "sets",
        help="find a set's ID using the parse.bot TCDB API (costs 1 credit)",
        description="Search TCDB sets by year and name to get the sid you need.",
    )
    sets.add_argument("--year", required=True, help="e.g. 2026")
    sets.add_argument("--sport", default="", help="e.g. Soccer")
    sets.add_argument("--query", default="", help="filter by set name substring")
    sets.add_argument("--api-key", help=f"overrides ${'PARSE_API_KEY'}")

    checklist = sub.add_parser(
        "checklist",
        help="download a set's checklist via the API (costs 3 credits)",
        description=(
            "Fetch a set's card list and save it as a manifest for cardgrab. "
            "NOTE: this API returns card metadata only -- it provides no card "
            "images. Images must still be captured from your browser."
        ),
    )
    checklist.add_argument("--sid", required=True, help="set ID, from `cardgrab sets`")
    checklist.add_argument(
        "--out",
        type=Path,
        required=True,
        help="where to write the manifest, e.g. ~/Downloads/monopoly/cardgrab-manifest.json",
    )
    checklist.add_argument("--api-key", help=f"overrides ${'PARSE_API_KEY'}")

    grab = sub.add_parser(
        "grab",
        help="download card images with a real Chromium window (Playwright)",
        description=(
            "Open a Chromium window, walk the set's cards, and save each card's "
            "largest image. No stealth or evasion is used: if the site shows a "
            "verification page, the script pauses and you solve it in the window."
        ),
    )
    grab.add_argument("--set-url", required=True, help="the set's checklist URL")
    grab.add_argument(
        "--out", type=Path, required=True, help="folder to save the images into"
    )
    grab.add_argument("--limit", type=int, default=0, help="stop after N cards (try 3 first)")
    grab.add_argument(
        "--delay",
        type=float,
        default=2.5,
        help="seconds between cards; grows automatically if the site rate-limits",
    )
    grab.add_argument(
        "--retries",
        type=int,
        default=3,
        help="extra passes over cards that failed, with longer pauses",
    )
    grab.add_argument(
        "--headless",
        action="store_true",
        help="hide the window (you cannot solve a verification page this way)",
    )
    grab.add_argument(
        "--max-sides",
        type=int,
        default=2,
        help="images to save per card (2 = front and back)",
    )
    grab.add_argument(
        "--keep-open",
        action="store_true",
        help="leave the browser open when finished",
    )
    grab.add_argument(
        "--profile",
        type=Path,
        default=None,
        help="browser profile dir; keeps verification between runs",
    )

    run = sub.add_parser(
        "run", help="process a collection", description="Process one collection."
    )
    run.add_argument("config", type=Path, help="path to a collection .toml")
    run.add_argument(
        "--inspect",
        action="store_true",
        help="show what the parser and filters see, without writing files",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="run fully and report, but write nothing",
    )
    return parser


def _normalise(argv: list[str]) -> list[str]:
    """Allow `cardgrab my.toml` as shorthand for `cardgrab run my.toml`."""
    if argv and argv[0] not in (
        "init", "run", "auto", "sets", "checklist", "grab", "-h", "--help"
    ):
        return ["run", *argv]
    return argv


def do_init(args: argparse.Namespace) -> int:
    dest, text = initialize.interactive(
        name=args.name,
        input_dir=args.input_dir,
        output_root=args.output_root,
        dest=args.out,
        min_width=args.min_width,
    )

    if not initialize.write(dest, text, force=args.force):
        return 1

    print(f"\nWrote {dest}\n")
    print("Next, check what it sees before it writes anything:\n")
    print(f"  python3 -m cardgrab run {dest} --inspect\n")
    print("If that looks right, run it for real:\n")
    print(f"  python3 -m cardgrab run {dest}\n")
    return 0


def do_grab(args: argparse.Namespace) -> int:
    from cardgrab import grab as grabber

    try:
        stats = grabber.grab(
            set_url=args.set_url,
            out_dir=args.out,
            limit=args.limit,
            delay=args.delay,
            headless=args.headless,
            profile=args.profile or grabber.DEFAULT_PROFILE,
            keep_open=args.keep_open,
            max_sides=args.max_sides,
            retries=args.retries,
        )
    except grabber.GrabError as exc:
        print(f"cardgrab: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nStopped. Re-run the same command to resume - finished cards are skipped.")
        return 130

    print(grabber.summarise(stats, args.out.expanduser()))
    return 0


def do_sets(args: argparse.Namespace) -> int:
    from cardgrab.sources import parsebot

    try:
        found = parsebot.list_sets(
            year=args.year, sport=args.sport, query=args.query, key=args.api_key
        )
    except parsebot.ParseBotError as exc:
        print(f"cardgrab: {exc}", file=sys.stderr)
        return 2

    if not found:
        print("No sets matched. Try a shorter --query, or check --year/--sport.")
        return 0

    print(f"\n{len(found)} set(s) found:\n")
    for entry in found:
        print(f"  sid {entry.get('sid', '?'):<10} {entry.get('name', '')}")
    print("\nNext, download that set's checklist:\n")
    print(f"  python3 -m cardgrab checklist --sid <sid> --out <folder>/cardgrab-manifest.json\n")
    return 0


def do_checklist(args: argparse.Namespace) -> int:
    from cardgrab.sources import parsebot

    try:
        count = parsebot.fetch_checklist(
            sid=args.sid, destination=args.out.expanduser(), key=args.api_key
        )
    except parsebot.ParseBotError as exc:
        print(f"cardgrab: {exc}", file=sys.stderr)
        return 2

    print(f"\nSaved {count} cards to {args.out}")
    print(
        "\nThis is the checklist only - the API returns no card images.\n"
        "Put your card image files in the same folder, then run:\n"
    )
    print(f"  python3 -m cardgrab auto {args.out.parent}\n")
    return 0


def do_auto(args: argparse.Namespace) -> int:
    """The no-config path: a folder in, a finished collection out."""
    from cardgrab.config import CollectionConfig

    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        print(f"cardgrab: no such folder: {folder}", file=sys.stderr)
        return 2

    found = initialize.find_checklist(folder)

    # A manifest written by `grab` knows the set's real name, so any set gets a
    # properly named collection folder without the user renaming anything.
    discovered = ""
    if found and found[0] == "manifest":
        try:
            import json

            discovered = str(
                json.loads(found[1].read_text(encoding="utf-8")).get("set_name", "")
            ).strip()
        except (OSError, ValueError):
            discovered = ""

    config = CollectionConfig(
        name=args.name or discovered or folder.name,
        input_dirs=[folder],
        output_root=args.output_root.expanduser(),
        min_width=args.min_width,
        min_height=args.min_width,
        checklist_html=[found[1]] if found and found[0] == "html" else [],
        manifest_json=found[1] if found and found[0] == "manifest" else None,
    )

    if found:
        kind = "manifest" if found[0] == "manifest" else "saved page"
        print(f"Using checklist ({kind}): {found[1].name}")
    else:
        print(
            "No checklist found in that folder, so images cannot be named by card.\n"
            "Run cardgrab.manifest() in the browser and put the JSON in this folder."
        )

    return _execute(config, inspect=args.inspect, dry_run=args.dry_run)


def _execute(config, inspect: bool, dry_run: bool) -> int:
    try:
        if inspect:
            print(pipeline.inspect(config))
            return 0
        result = pipeline.run(config, dry_run=dry_run)
        print(result.summary(config.name))
        if dry_run:
            print("\n(dry run - nothing was written)")
    except ConfigError as exc:
        print(f"cardgrab: {exc}", file=sys.stderr)
        return 2
    except ManifestError as exc:
        print(f"cardgrab: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"cardgrab: file error: {exc}", file=sys.stderr)
        return 1
    return 0


def do_run(args: argparse.Namespace) -> int:
    try:
        config = load(args.config)
    except ConfigError as exc:
        print(f"cardgrab: {exc}", file=sys.stderr)
        return 2

    return _execute(config, inspect=args.inspect, dry_run=args.dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalise(list(sys.argv[1:] if argv is None else argv)))

    if args.command == "grab":
        return do_grab(args)
    if args.command == "sets":
        return do_sets(args)
    if args.command == "checklist":
        return do_checklist(args)
    if args.command == "auto":
        return do_auto(args)
    if args.command == "init":
        return do_init(args)
    if args.command == "run":
        return do_run(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
