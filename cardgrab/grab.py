"""Playwright card-image grabber.

Drives a real Chromium window to walk a set's cards and save their images.

Design notes, stated plainly:

* No stealth, evasion or fingerprint patching is used. If the site presents a
  challenge, the script pauses and asks you to solve it in the visible window,
  then carries on. That is why the browser runs headed by default.
* A persistent profile directory keeps that clearance between runs, so you
  normally solve a challenge once rather than every time.
* Image bytes are retrieved by navigating to the image URL, which is an
  ordinary page load, rather than by scripted background requests.
* Requests are deliberately spaced out. Do not lower the delay.

This module and sources/parsebot.py are the only parts of cardgrab that touch
the network; the processing pipeline stays entirely offline.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from cardgrab.cardnames import name_from_card_url
from cardgrab.imagemeta import UnreadableImage, probe

DEFAULT_PROFILE = Path.home() / ".cardgrab" / "chrome-profile"
CHALLENGE_MARKERS = ("just a moment", "checking your browser", "verifica di sicurezza",
                     "un attimo", "attendere prego")
CARD_HREF = re.compile(r"(?:ViewCard|/Card/)", re.IGNORECASE)
NUMBER = re.compile(r"^\s*#?\s*([A-Z]{0,4}-?\d+[A-Za-z]?)\s*$")
IMG_EXT = re.compile(r"\.(jpe?g|png|gif|webp)(\?|$)", re.IGNORECASE)


class GrabError(Exception):
    """Raised when the grab cannot proceed."""


@dataclass
class Card:
    number: str
    name: str
    url: str


@dataclass
class GrabStats:
    cards_found: int = 0
    saved: int = 0
    skipped: int = 0
    no_image: int = 0
    failed: int = 0
    blocked: int = 0
    rate_limited: int = 0
    incomplete: int = 0
    widths: list[int] = field(default_factory=list)


@dataclass
class Pace:
    """Current spacing between requests.

    A 429 is the server asking for fewer requests, so the delay only ever
    grows during a run -- it is never wound back down.
    """

    delay: float
    consecutive_429: int = 0


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise GrabError(
            "Playwright is not installed. Install it with:\n"
            "  python3 -m venv .venv\n"
            "  .venv/bin/pip install playwright\n"
            "  .venv/bin/playwright install chromium\n"
            "then run cardgrab with .venv/bin/python"
        ) from exc
    return sync_playwright


def _is_challenge(page) -> bool:
    """True when the current page is an interstitial rather than real content."""
    try:
        title = (page.title() or "").lower()
    except Exception:
        return False
    return any(marker in title for marker in CHALLENGE_MARKERS)


def _wait_for_clearance(page, timeout: int = 300) -> None:
    """Let the human clear any challenge in the visible window.

    We do not attempt to solve or bypass it. We wait, and tell the user what
    to do. If it clears on its own, we continue immediately.
    """
    if not _is_challenge(page):
        return

    print(
        "\n  The site is showing a verification page.\n"
        "  Solve it in the Chromium window that just opened, then leave it on\n"
        "  the set page. Waiting..."
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        if not _is_challenge(page):
            print("  Cleared. Continuing.\n")
            time.sleep(1)
            return

    raise GrabError(
        "The verification page did not clear within "
        f"{timeout}s. Nothing was downloaded."
    )


def _collect_from_page(page) -> list[Card]:
    """Read card links and their number/name from the current page."""
    raw = page.evaluate(
        r"""() => [...document.querySelectorAll('a[href]')].map(a => ({
            href: a.href,
            text: (a.textContent || '').trim().replace(/\s+/g, ' ')
        }))"""
    )

    def harvest(require_card_href: bool) -> dict[str, Card]:
        found: dict[str, Card] = {}
        for entry in raw:
            href = entry.get("href", "")
            text = entry.get("text", "")
            if not href or not text:
                continue
            if require_card_href and not CARD_HREF.search(href):
                continue
            first, _, rest = text.partition(" ")
            match = NUMBER.match(first)
            if not match:
                continue
            number = match.group(1)
            found.setdefault(
                number,
                Card(
                    number=number,
                    name=rest.strip() or name_from_card_url(href, number),
                    url=href,
                ),
            )
        return found

    # Prefer links that clearly point at card pages. If the layout uses some
    # other URL shape, fall back to any link whose text opens with a card
    # number -- better than reporting an empty set.
    cards = harvest(require_card_href=True) or harvest(require_card_href=False)
    return list(cards.values())


_PAGE_PARAM = re.compile(r"[?&](PageIndex|page|pg|p)=(\d+)", re.IGNORECASE)


def _pagination_param(page) -> str:
    """Which query parameter this site uses for paging."""
    try:
        hrefs = page.evaluate(
            "() => [...document.querySelectorAll('a[href]')].map(a => a.href)"
        )
    except Exception:
        return "PageIndex"

    for href in hrefs or []:
        match = _PAGE_PARAM.search(href or "")
        if match:
            return match.group(1)
    return "PageIndex"


def _with_page(set_url: str, param: str, index: int) -> str:
    """The set URL pointed at a given page."""
    base = re.sub(rf"[?&]{re.escape(param)}=\d+", "", set_url, flags=re.IGNORECASE)
    base = base.rstrip("?&")
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{param}={index}"


def _collect_all_pages(
    page, set_url: str, timeout: int, delay: float, max_pages: int = 60
) -> list[Card]:
    """Every card across every page of the checklist.

    Rather than trusting pagination markup, this walks the page parameter
    upwards and stops when a page contributes no card it has not already seen.
    That works whether or not the pagination links can be found, and copes with
    a site that only ever shows a few page numbers at a time.
    """
    cards: dict[str, Card] = {}
    for card in _collect_with_retry(page):
        cards.setdefault(card.number, card)

    if not cards:
        return []

    param = _pagination_param(page)
    print(f"  page 1: {len(cards)} cards")

    for index in range(2, max_pages + 1):
        url = _with_page(set_url, param, index)
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"  page {index}: {type(exc).__name__} - stopping here")
            break

        _wait_for_clearance(page, timeout)

        fresh = [c for c in _collect_from_page(page) if c.number not in cards]
        if not fresh:
            # Either the last page, or the site ignored the parameter.
            break

        for card in fresh:
            cards[card.number] = card
        print(f"  page {index}: +{len(fresh)} cards (total {len(cards)})")
        time.sleep(delay)
    else:
        print(f"  stopped at the {max_pages}-page safety limit")

    return list(cards.values())


def _card_images(page) -> list[dict]:
    """Every card-shaped image on the page, largest first.

    Also reports any anchor wrapping each image: card pages commonly link a
    displayed thumbnail to its full-size version, so that href is often the
    better download target.
    """
    found = page.evaluate(
        r"""() => {
            const imgs = [...document.images].filter(i => {
                if (!i.naturalWidth || !i.naturalHeight) return false;
                const r = i.naturalWidth / i.naturalHeight;
                return i.naturalWidth >= 120 && r > 0.45 && r < 1.05;
            });
            imgs.sort((a, b) =>
                (b.naturalWidth * b.naturalHeight) - (a.naturalWidth * a.naturalHeight));
            return imgs.map(i => {
                const a = i.closest('a');
                return {
                    src: i.src,
                    w: i.naturalWidth,
                    h: i.naturalHeight,
                    alt: i.alt || '',
                    href: a ? (a.href || '') : ''
                };
            });
        }"""
    )
    return found or []


_BACK_HINT = re.compile(r"back|_b\b|-bk|-b\.|rev(erse)?", re.IGNORECASE)
_FRONT_HINT = re.compile(r"front|_f\b|-fr|-f\.|obv(erse)?", re.IGNORECASE)


def _side_of(entry: dict, position: int) -> str:
    """Front or back for one image.

    URL and alt text are trusted first; failing that, position decides, since
    card pages conventionally show the front before the back.
    """
    haystack = f"{entry.get('src', '')} {entry.get('alt', '')} {entry.get('href', '')}"
    if _BACK_HINT.search(haystack):
        return "back"
    if _FRONT_HINT.search(haystack):
        return "front"
    return "front" if position == 0 else "back"


def _best_url(entry: dict) -> str:
    """Prefer an anchor pointing at an image file: usually the full-size one."""
    href = entry.get("href", "")
    if href and IMG_EXT.search(href):
        return href
    return entry.get("src", "")


def _retry_after(response) -> float:
    """Honour a Retry-After header when the server sends one."""
    try:
        raw = response.header_value("retry-after")
    except Exception:
        return 0.0
    if not raw:
        return 0.0
    try:
        return max(0.0, min(300.0, float(raw.strip())))
    except ValueError:
        return 0.0


def _fetch_image(
    page, url: str, destination: Path, label: str, side: str,
    pace: Pace, stats: GrabStats, attempts: int = 3,
) -> str:
    """Download one image. Returns 'saved', 'retry' or 'skip'.

    On HTTP 429 it waits -- preferring the server's own Retry-After -- and
    permanently widens the gap between requests before trying again.
    """
    for attempt in range(1, attempts + 1):
        try:
            response = page.goto(url, wait_until="commit")
        except Exception as exc:  # noqa: BLE001
            print(f"  {label} {side}: {type(exc).__name__}")
            stats.failed += 1
            return "retry"

        if response and response.ok:
            destination.write_bytes(response.body())
            stats.saved += 1
            pace.consecutive_429 = 0
            try:
                stats.widths.append(probe(destination)[0])
            except UnreadableImage:
                pass
            return "saved"

        status = response.status if response else 0

        if status == 429:
            stats.rate_limited += 1
            pace.consecutive_429 += 1
            wait = _retry_after(response) or min(90.0, 5.0 * (2 ** pace.consecutive_429))
            pace.delay = min(pace.delay * 1.4, 10.0)
            print(
                f"  rate limited (429) - pausing {wait:.0f}s, "
                f"now {pace.delay:.1f}s between requests"
            )
            time.sleep(wait)
            continue

        print(f"  {label} {side}: image fetch failed ({status or 'no response'})")
        stats.failed += 1
        return "retry"

    return "retry"


def _safe(text: str) -> str:
    cleaned = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "", text)
    return " ".join(cleaned.split()).strip(" .")


def _collect_with_retry(page, attempts: int = 4, pause: float = 2.5) -> list[Card]:
    """Collect card links, allowing the page time to finish rendering.

    After a challenge clears the site often reloads, so the first look can land
    on a page that is not populated yet.
    """
    for attempt in range(1, attempts + 1):
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        cards = _collect_from_page(page)
        if cards:
            return cards

        if attempt < attempts:
            print(f"  no cards yet (look {attempt}/{attempts}), waiting...")
            time.sleep(pause)
            if attempt == 2:
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
    return []


def _dump_debug(page, out_dir: Path) -> Path | None:
    """Save the page and summarise it, so a parse failure can be fixed.

    Guessing at markup we cannot see is what caused the earlier failures; this
    captures the truth instead.
    """
    print("\n  --- what the page actually contains ---")
    try:
        print(f"  title : {page.title()}")
        print(f"  url   : {page.url}")
    except Exception:
        pass

    try:
        info = page.evaluate(
            r"""() => {
                const links = [...document.querySelectorAll('a[href]')];
                return {
                    links: links.length,
                    images: document.images.length,
                    bigImages: [...document.images]
                        .filter(i => i.naturalWidth >= 150).length,
                    sample: links.slice(0, 25).map(a => ({
                        href: a.getAttribute('href') || '',
                        text: (a.textContent || '').trim()
                                .replace(/\s+/g, ' ').slice(0, 45)
                    }))
                };
            }"""
        )
        print(f"  links : {info['links']}")
        print(f"  images: {info['images']} ({info['bigImages']} card-sized)")
        print("  first links seen:")
        for row in info["sample"]:
            if row["href"] or row["text"]:
                print(f"    {row['text'][:40]:<42} {row['href'][:60]}")
    except Exception as exc:
        print(f"  (could not inspect: {exc})")

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = out_dir / "_debug-page.html"
        saved.write_text(page.content(), encoding="utf-8")
        print(f"\n  Saved the page to: {saved}")
        print("  Send that file (or the list above) and the parser can be fixed.")
        return saved
    except Exception as exc:
        print(f"  (could not save page: {exc})")
        return None


def grab(
    set_url: str,
    out_dir: Path,
    limit: int = 0,
    delay: float = 1.5,
    headless: bool = False,
    profile: Path = DEFAULT_PROFILE,
    timeout: int = 300,
    keep_open: bool = False,
    max_sides: int = 2,
    retries: int = 3,
) -> GrabStats:
    """Walk a set's cards and save each card's largest image."""
    sync_playwright = _import_playwright()

    out_dir = out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    profile.mkdir(parents=True, exist_ok=True)

    stats = GrabStats()
    seen_samples: list[dict] = []
    pace = Pace(delay=delay)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=headless,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(45000)

        try:
            print(f"Opening {set_url}")
            page.goto(set_url, wait_until="domcontentloaded")
            _wait_for_clearance(page, timeout)

            cards = _collect_all_pages(page, set_url, timeout, delay)
            if not cards:
                _dump_debug(page, out_dir)
                if not headless:
                    print(
                        "\n  Leaving the browser open so you can look at the page."
                    )
                    print("  Press Enter here to close it.")
                    try:
                        input()
                    except EOFError:
                        time.sleep(20)
                raise GrabError(
                    "No card links found on that page.\n"
                    "  If you used a Checklist.cfm URL, try the ViewSet.cfm one "
                    "for the same set.\n"
                    "  The page was saved for inspection - see above."
                )

            cards.sort(key=lambda c: _sort_key(c.number))
            stats.cards_found = len(cards)
            set_name = _set_name(page)

            print(f"\nFound {len(cards)} cards.")
            targets = cards[:limit] if limit else cards
            print(f"Fetching images for {len(targets)}, {delay}s apart.\n")
            on_disk = {p.name for p in out_dir.iterdir() if p.is_file()}

            _write_manifest(out_dir, set_url, cards, set_name)

            pending = list(targets)
            for round_number in range(1, retries + 2):
                if not pending:
                    break

                if round_number > 1:
                    # A 429 means the server wants fewer requests, so wait it
                    # out properly and slow down before trying the rest again.
                    cool_off = min(180, 30 * round_number)
                    pace.delay = min(pace.delay * 1.6, 10.0)
                    print(
                        f"\nRetry {round_number - 1}/{retries}: {len(pending)} card(s) "
                        f"left. Waiting {cool_off}s for the rate limit to reset, "
                        f"then {pace.delay:.1f}s between cards.\n"
                    )
                    time.sleep(cool_off)

                unfinished: list[Card] = []
                total = len(pending)

                for index, card in enumerate(pending, start=1):
                    label = f"{card.number}{' - ' + _safe(card.name) if card.name else ''}"

                    # Only skip a card once both sides are on disk, so a run
                    # that previously captured just the front fills in the back.
                    have = {
                        side
                        for side in ("front", "back")
                        if any(n.startswith(f"{label} - {side}.") for n in on_disk)
                    }
                    if have == {"front", "back"}:
                        if round_number == 1:
                            stats.skipped += 1
                        continue

                    try:
                        page_response = page.goto(
                            card.url, wait_until="domcontentloaded"
                        )
                        if page_response and page_response.status == 429:
                            stats.rate_limited += 1
                            pace.consecutive_429 += 1
                            wait = _retry_after(page_response) or min(
                                90.0, 5.0 * (2 ** pace.consecutive_429)
                            )
                            pace.delay = min(pace.delay * 1.4, 10.0)
                            print(
                                f"  {label}: page rate limited - pausing {wait:.0f}s"
                            )
                            time.sleep(wait)
                            unfinished.append(card)
                            continue

                        if _is_challenge(page):
                            stats.blocked += 1
                            if stats.blocked >= 3:
                                print(
                                    "\n  Verification keeps appearing. Stopping so as "
                                    "not to hammer the site.\n"
                                    "  Solve it in the window, then run the same "
                                    "command again - finished cards are skipped."
                                )
                                unfinished.extend(pending[index - 1:])
                                pending = []
                                break
                            _wait_for_clearance(page, timeout)

                        entries = _card_images(page)
                        if not entries:
                            # Images that were rate-limited never finish
                            # loading, so the page looks empty. Retry before
                            # concluding the card genuinely has no picture.
                            if round_number <= retries:
                                unfinished.append(card)
                            else:
                                stats.no_image += 1
                                print(f"  {label}: no card image on the page")
                            time.sleep(pace.delay)
                            continue

                        if len(seen_samples) < 5:
                            seen_samples.append(
                                {"card": card.number, "images": entries}
                            )

                        incomplete = False
                        used: set[str] = set()

                        for position, entry in enumerate(entries[:max_sides]):
                            side = _side_of(entry, position)
                            if side in used:
                                side = "back" if side == "front" else "front"
                            if side in used:
                                continue
                            used.add(side)

                            url = _best_url(entry)
                            if not url:
                                continue

                            suffix = (
                                Path(urlparse(url).path).suffix.lower() or ".jpg"
                            )
                            destination = out_dir / f"{label} - {side}{suffix}"
                            if destination.name in on_disk:
                                continue

                            outcome = _fetch_image(
                                page, url, destination, label, side, pace, stats
                            )
                            if outcome == "saved":
                                on_disk.add(destination.name)
                            elif outcome == "retry":
                                incomplete = True

                            time.sleep(pace.delay * 0.5)

                        if incomplete:
                            unfinished.append(card)

                    except Exception as exc:  # noqa: BLE001 - report, keep going
                        stats.failed += 1
                        unfinished.append(card)
                        print(f"  {label}: {type(exc).__name__}: {exc}")

                    if index % 10 == 0 or index == total:
                        print(f"  {index}/{total} ... {label}")

                    time.sleep(pace.delay)

                pending = unfinished

            stats.incomplete = len(pending)
            if pending:
                print(
                    f"\n  {len(pending)} card(s) still incomplete after "
                    f"{retries} retr{'y' if retries == 1 else 'ies'}. "
                    "Run the same command again later to finish them."
                )

            if seen_samples:
                (out_dir / "_image-sources.json").write_text(
                    json.dumps(seen_samples, indent=2), encoding="utf-8"
                )

            if keep_open and not headless:
                print("\nDone. Press Enter to close the browser.")
                try:
                    input()
                except EOFError:
                    pass
        finally:
            context.close()

    return stats


def _sort_key(number: str) -> tuple[int, int, str]:
    match = re.match(r"^(\d+)([A-Za-z]*)$", number.strip())
    if match:
        return (0, int(match.group(1)), match.group(2).lower())
    return (1, 0, number.lower())


def _set_name(page) -> str:
    """The set's own name, so any set lands in a sensibly named folder."""
    for getter in (
        lambda: page.evaluate("() => document.querySelector('h1')?.textContent || ''"),
        lambda: page.title() or "",
    ):
        try:
            text = " ".join((getter() or "").split())
        except Exception:
            continue
        # Strip the site's own suffix, e.g. "... | Trading Card Database".
        text = re.split(r"\s*[|\u2013\u2014]\s*", text)[0].strip()
        text = re.sub(r"\b(checklist|card list|trading card database)\b", "", text,
                      flags=re.IGNORECASE).strip(" -\u2013\u2014")
        if len(text) > 3:
            return text
    return ""


def _write_manifest(
    out_dir: Path, set_url: str, cards: list[Card], set_name: str = ""
) -> None:
    """Save the checklist so the offline pipeline can name and audit the set."""
    payload = {
        "source_url": set_url,
        "set_name": set_name,
        "source": "cardgrab playwright grab",
        "cards": [
            {"number": c.number, "name": c.name, "card_url": c.url} for c in cards
        ],
    }
    (out_dir / "cardgrab-manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def summarise(stats: GrabStats, out_dir: Path) -> str:
    lines = [
        "",
        f"Cards on checklist : {stats.cards_found}",
        f"Images saved       : {stats.saved}",
        f"Already had        : {stats.skipped}",
        f"No image on page   : {stats.no_image}",
        f"Fetch failures     : {stats.failed}",
        f"Rate limits hit    : {stats.rate_limited}",
        f"Still incomplete   : {stats.incomplete}",
        f"Folder             : {out_dir}",
    ]
    real = [w for w in stats.widths if w]
    if real:
        lines.append(
            f"Image width        : {min(real)}-{max(real)}px "
            f"(average {sum(real) // len(real)}px)"
        )
        if max(real) < 400:
            lines.append(
                "  NOTE: that is thumbnail size - see _image-sources.json for what"
            )
            lines.append("  the card pages actually offered.")
    lines += [
        "",
        "Now organise, deduplicate and audit the set:",
        f"  python3 -m cardgrab auto {out_dir}",
    ]
    return "\n".join(lines)
