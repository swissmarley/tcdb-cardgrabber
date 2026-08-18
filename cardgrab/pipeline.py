"""Orchestration: local files in, clean collection folder out.

This module performs no network access. Every byte it handles was already
fetched by the user's own browser.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from cardgrab import harvest, matcher, naming, report
from cardgrab.config import CollectionConfig
from cardgrab.dedupe import SeenSet
from cardgrab.models import CardRef, MatchResult, RunStats
from cardgrab.sources.base import CardSource, NullSource
from cardgrab.sources.manifest import ManifestSource
from cardgrab.sources.saved_page import SavedPageSource

UNMATCHED_DIRNAME = "_unmatched"
REPORT_FILENAME = "_report.json"


@dataclass
class RunResult:
    stats: RunStats
    coverage: report.Coverage
    matches: list[MatchResult]
    cards: list[CardRef]
    source_desc: str
    output_dir: Path

    def summary(self, name: str) -> str:
        return report.render(
            name, self.output_dir, self.source_desc,
            self.stats, self.coverage, self.matches,
        )


def build_source(config: CollectionConfig) -> CardSource:
    """Pick the checklist adapter implied by the configuration."""
    if config.manifest_json:
        return ManifestSource(config.manifest_json)
    if config.checklist_html:
        return SavedPageSource(config.checklist_html)
    return NullSource()


def run(config: CollectionConfig, dry_run: bool = False) -> RunResult:
    """Execute the full pipeline for one collection."""
    config.validate()

    source = build_source(config)
    cards = source.cards()
    image_urls = getattr(source, "image_urls", [])

    harvested = harvest.scan(config)
    matches = matcher.match_all(harvested.assets, cards, image_urls)

    stats = RunStats(
        scanned=len(harvested.assets) + len(harvested.rejections),
        rejected_junk=harvested.junk,
        rejected_unreadable=harvested.unreadable,
    )

    output_dir = config.output_dir
    seen = SeenSet()
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        stats.skipped_existing = seen.index_existing(output_dir)

    width = naming.number_width([c.number for c in cards])
    taken: set[str] = set()

    for match in matches:
        asset = match.asset

        # Content identity decides duplicates, not filenames.
        if not seen.add(asset.sha256, asset.path):
            stats.duplicates += 1
            continue

        if match.matched:
            assert match.card is not None
            filename = naming.filename(
                match.card.number, match.card.name, match.side, asset.ext, width
            )
            destination_dir = _destination_dir(output_dir, config, match.side)
        else:
            stats.unmatched += 1
            if not config.keep_unmatched:
                continue
            filename = naming.sanitize(asset.path.name) or asset.path.name
            destination_dir = output_dir / UNMATCHED_DIRNAME

        unique = naming.disambiguate(filename, taken)
        taken.add(unique)
        stats.written += 1
        stats.dimensions.append((asset.width, asset.height))

        if not dry_run:
            destination_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asset.path, destination_dir / unique)

    cov = report.coverage(cards, matches)

    if not dry_run:
        report.write_sidecar(
            output_dir / REPORT_FILENAME,
            config.name, source.describe(), stats, cov, matches,
        )

    return RunResult(
        stats=stats,
        coverage=cov,
        matches=matches,
        cards=cards,
        source_desc=source.describe(),
        output_dir=output_dir,
    )


def _destination_dir(output_dir: Path, config: CollectionConfig, side: str) -> Path:
    """Flat layout, or fronts and backs split into subfolders."""
    return output_dir / side if config.layout == "split" else output_dir


def inspect(config: CollectionConfig) -> str:
    """Report what the parser and filters see, without writing anything.

    This is the tuning tool: run it against a real saved page to check the
    checklist parsed correctly and the junk filter is keeping the right images.
    """
    source = build_source(config)
    cards = source.cards()
    harvested = harvest.scan(config)
    matches = matcher.match_all(
        harvested.assets, cards, getattr(source, "image_urls", [])
    )

    lines = [
        "",
        f"Source: {source.describe()}",
        f"Cards parsed from checklist: {len(cards)}",
    ]

    if cards:
        lines.append("  first 10:")
        for card in cards[:10]:
            lines.append(f"    {card.number:>8}  {card.name}")
    else:
        lines += [
            "  NOTE: no cards parsed. Either no checklist is configured, or the",
            "  saved page's markup did not match. The images below will still be",
            "  imported, into _unmatched/.",
        ]

    lines += [
        "",
        f"Images accepted as cards: {len(harvested.assets)}",
        f"Images rejected:          {len(harvested.rejections)}",
    ]

    if harvested.assets:
        lines.append("  accepted sample:")
        for asset in harvested.assets[:10]:
            lines.append(
                f"    {asset.width:>5}x{asset.height:<5} {asset.aspect:.2f}  {asset.path.name}"
            )

    if harvested.rejections:
        lines.append("  rejected sample (check nothing wanted is here):")
        for rejection in harvested.rejections[:10]:
            lines.append(f"    {rejection.path.name}: {rejection.reason}")

    matched = sum(1 for m in matches if m.matched)
    lines += ["", f"Matched to a card: {matched}/{len(matches)}"]
    if matches:
        lines.append("  sample:")
        for match in matches[:10]:
            label = (
                f"{match.card.number} {match.card.name}" if match.card else "UNMATCHED"
            )
            lines.append(
                f"    {match.asset.path.name}  ->  {label} [{match.side}] ({match.strategy})"
            )

    return "\n".join(lines)
