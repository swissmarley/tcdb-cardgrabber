# cardgrab — TCDB collection image harvester

Date: 2026-08-18
Status: approved

## Problem

Download every card image of a given TCDB card collection into a folder named
after the collection, with no duplicates. Adding a new collection must require
changing only a few parameters, not code.

First target: `2026 Panini Prizm FIFA World Cup Monopoly`.

## Constraint discovered during research

tcdb.com sits behind a Cloudflare **managed challenge** across the whole origin.
Verified 2026-08-18, all returning HTTP 403 with `cType: 'managed'`:

| Target | Result |
|---|---|
| `/` | 403 challenge |
| `/robots.txt` | 403 challenge |
| `/Search.cfm` | 403 challenge |
| `/Images/Cards/*.jpg` | 403 challenge |
| `api.tcdb.com`, `/api` | 403 challenge |

Image assets are gated too, so there is no open-CDN shortcut. A controlled
browser was also blocked (`bloccato`).

**Out of scope by decision:** cloudscraper, FlareSolverr, undetected-chromedriver,
curl_cffi TLS-fingerprint spoofing, and `cf_clearance` cookie replay. These are
bot-detection bypass techniques. They are excluded on principle and because they
break whenever Cloudflare rotates its challenge.

## Approach

Split the job at the network boundary.

- **Stage A — browser, human-driven.** The user views the set in their own
  browser and saves it (`Save Page As -> Webpage, Complete`, or a bulk-image
  extension). Their browser performs every network request, through a session
  that passed the challenge organically.
- **Stage B — Python, automated.** Consumes the resulting local directory and
  produces the clean collection folder.

**The Python package performs zero network requests.** This is a hard
architectural invariant, enforced by test. Nothing to block, nothing to break.

## Architecture

```
cardgrab/
  config.py      CollectionConfig, TOML loading      <- the "few parameters"
  models.py      CardRef, ImageAsset, MatchResult
  imagemeta.py   stdlib image dimension probing (no Pillow dependency)
  sources/
    base.py        CardSource protocol               <- pluggable seam
    saved_page.py  parses saved checklist HTML
    manifest.py    reads JSON manifest
  harvest.py     walk input dirs, classify card images vs junk
  dedupe.py      SHA-256 content identity
  naming.py      "012 - Lionel Messi - front.jpg"
  pipeline.py    orchestration
  report.py      coverage report + JSON sidecar
  cli.py         argparse entry point
collections/*.toml
browser/collect_manifest.js
```

### Configuration

Adding a collection is one TOML file, no code:

```toml
name        = "2026 Panini Prizm FIFA World Cup Monopoly"
set_url     = "https://www.tcdb.com/ViewSet.cfm/sid/.../..."
input_dirs  = ["~/Downloads/tcdb-monopoly"]
output_root = "~/Pictures/CardCollections"
```

Optional tuning keys with defaults: `min_width` 120, `min_height` 120,
`aspect_min` 0.60, `aspect_max` 0.85, `layout` "flat", `keep_unmatched` true.

### Deduplication

SHA-256 over file bytes. A hash seen before is never written again, which
catches the same image saved under several names (routine in browser saves) and
makes re-runs idempotent — a second run merges into the existing folder rather
than producing `image(1).jpg`.

### Junk rejection

Trading cards occupy a tight aspect-ratio band (~0.71) above a minimum pixel
size. Sprites, logos, avatars and tracking pixels fail the ratio test, the size
test, or both. Both bounds are per-collection tunable.

### Naming

`{number} - {player} - {side}.{ext}`, e.g. `012 - Lionel Messi - front.jpg`.
Card numbers are zero-padded to the width of the longest number in the set so
they sort correctly. Non-numeric numbers (`RC-12`) are preserved verbatim.
Filesystem-unsafe characters are stripped.

### Matching images to cards

Layered, most-reliable first:

1. URL/basename correlation against `<img src>` and card-page hrefs from the
   saved HTML.
2. Card-number token embedded in the filename.
3. Side detection from `-Front` / `-Back` / `_F` / `_B` suffixes.
4. DOM-order positional fallback.

Anything still unmatched is copied to `_unmatched/` rather than being silently
dropped or given a wrong name. Silent misnaming is worse than a visible
leftover.

### Coverage report

The checklist yields the full card list, so a run ends with an explicit
accounting — `142/150 fronts, 138/150 backs, missing: 7, 23, 88` — plus a
`_report.json` sidecar. The user learns what they actually got instead of
assuming completeness.

## Known limitation

Set checklist pages typically render *thumbnails*; full-resolution scans live on
individual card pages. Achievable resolution therefore depends on which pages
the user saves in Stage A. The tool reports the dimension distribution of what
it imported so this is visible rather than silently accepted.

The checklist parser was written defensively, with layered fallbacks, because
the live DOM could not be inspected during design (the origin is challenge-
gated). `--inspect` prints what the parser extracted so it can be tuned against
a real saved page on first run.

## Testing

- Dedupe collapses identical bytes under different names.
- Junk filter rejects out-of-band aspect ratios and undersized images.
- Naming pads, sanitizes, and preserves non-numeric numbers.
- Image dimension probing for JPEG/PNG/GIF/WebP against synthetic fixtures.
- Pipeline is idempotent: running twice yields the same file set.
- Invariant: the package imports no network module.
