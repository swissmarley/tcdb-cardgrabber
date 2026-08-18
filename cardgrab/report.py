"""Run reporting.

A run that quietly "succeeds" while missing forty backs is a failure the user
discovers weeks later. Every run states what it actually got.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from cardgrab.models import CardRef, MatchResult, RunStats


@dataclass
class Coverage:
    total_cards: int = 0
    fronts: int = 0
    backs: int = 0
    missing_fronts: list[str] = field(default_factory=list)
    missing_backs: list[str] = field(default_factory=list)


def coverage(cards: list[CardRef], matches: list[MatchResult]) -> Coverage:
    """Compare what was imported against the checklist."""
    have: dict[str, set[str]] = {}
    for match in matches:
        if match.card:
            have.setdefault(match.card.number, set()).add(match.side)

    result = Coverage(total_cards=len(cards))
    for card in cards:
        sides = have.get(card.number, set())
        if "front" in sides:
            result.fronts += 1
        else:
            result.missing_fronts.append(card.number)
        if "back" in sides:
            result.backs += 1
        else:
            result.missing_backs.append(card.number)
    return result


def render(
    name: str,
    output_dir: Path,
    source_desc: str,
    stats: RunStats,
    cov: Coverage,
    matches: list[MatchResult],
) -> str:
    """Build the human-readable summary printed at the end of a run."""
    lines = [
        "",
        f"Collection : {name}",
        f"Folder     : {output_dir}",
        f"Checklist  : {source_desc}",
        "",
        "Import",
        f"  scanned          {stats.scanned}",
        f"  written          {stats.written}",
        f"  duplicates       {stats.duplicates} (identical bytes, skipped)",
        f"  already present  {stats.skipped_existing}",
        f"  rejected junk    {stats.rejected_junk}",
        f"  unreadable       {stats.rejected_unreadable}",
        f"  unmatched        {stats.unmatched}",
    ]

    if cov.total_cards:
        lines += [
            "",
            "Coverage",
            f"  fronts  {cov.fronts}/{cov.total_cards}",
            f"  backs   {cov.backs}/{cov.total_cards}",
        ]
        if cov.missing_fronts:
            lines.append(f"  missing fronts: {_compact(cov.missing_fronts)}")
        if cov.missing_backs:
            lines.append(f"  missing backs:  {_compact(cov.missing_backs)}")
        if not cov.missing_fronts and not cov.missing_backs:
            lines.append("  complete set")
    else:
        lines += [
            "",
            "Coverage",
            "  no checklist available - images organised but not named by card.",
            "  Point 'checklist_html' or 'manifest_json' at a saved page to enable naming.",
        ]

    strategies = Counter(m.strategy for m in matches if m.matched)
    if strategies:
        detail = ", ".join(f"{k} {v}" for k, v in strategies.most_common())
        lines += ["", f"Match strategies: {detail}"]

    if stats.dimensions:
        sizes = Counter(f"{w}x{h}" for w, h in stats.dimensions)
        common = ", ".join(f"{size} x{count}" for size, count in sizes.most_common(4))
        widths = [w for w, _ in stats.dimensions]
        lines += [
            "",
            f"Resolution: {min(widths)}-{max(widths)}px wide. Most common: {common}",
        ]
        if max(widths) < 400:
            lines.append(
                "  NOTE: these look like thumbnails. Full-size scans live on the"
            )
            lines.append(
                "  individual card pages - save those if you need higher resolution."
            )

    return "\n".join(lines)


def _compact(numbers: list[str], limit: int = 20) -> str:
    """Show the first few missing numbers rather than flooding the terminal."""
    shown = ", ".join(numbers[:limit])
    remainder = len(numbers) - limit
    return f"{shown} (+{remainder} more)" if remainder > 0 else shown


def write_sidecar(
    path: Path,
    name: str,
    source_desc: str,
    stats: RunStats,
    cov: Coverage,
    matches: list[MatchResult],
) -> None:
    """Write _report.json so runs can be compared or consumed by other tools."""
    payload = {
        "collection": name,
        "source": source_desc,
        "stats": {
            "scanned": stats.scanned,
            "written": stats.written,
            "duplicates": stats.duplicates,
            "skipped_existing": stats.skipped_existing,
            "rejected_junk": stats.rejected_junk,
            "rejected_unreadable": stats.rejected_unreadable,
            "unmatched": stats.unmatched,
        },
        "coverage": {
            "total_cards": cov.total_cards,
            "fronts": cov.fronts,
            "backs": cov.backs,
            "missing_fronts": cov.missing_fronts,
            "missing_backs": cov.missing_backs,
        },
        "strategies": dict(Counter(m.strategy for m in matches if m.matched)),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
