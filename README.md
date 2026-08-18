# cardgrab

Download every card image of a trading card set from [TCDB](https://www.tcdb.com)
and organise it into a clean, deduplicated collection folder — fronts and backs,
named by card number and player, with a report of anything missing.

```
2026 Panini Prizm Monopoly FIFA World Cup/
├── 001 - Lionel Messi - front.jpg
├── 001 - Lionel Messi - back.jpg
├── 002 - Lautaro Martínez - front.jpg
...
└── _report.json
```

Works for any set. Change the URL, not the code.

---

## Install

```bash
git clone <your-repo-url> cardgrab
cd cardgrab
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

Requires Python 3.11 or newer.

### Optional: install `cardgrab` as a real command

```bash
.venv/bin/pip install -e .
```

Then use `cardgrab …` instead of `.venv/bin/python -m cardgrab …` everywhere
below.

---

## Quick start

Two commands: download, then organise.

```bash
.venv/bin/python -m cardgrab grab \
  --set-url "https://www.tcdb.com/Checklist.cfm/sid/598914/2026-Panini-Prizm-Monopoly-FIFA-World-Cup" \
  --out ~/Downloads/monopoly

python3 -m cardgrab auto ~/Downloads/monopoly
```

A Chromium window opens. **Solve the verification once** when it appears — the
profile remembers it for later runs. Then it walks the set and saves both sides
of every card.

Interrupting with `Ctrl-C` is safe. Re-run the same command and it resumes,
skipping cards it already has.

---

## Building the `grab` command

The command has two required parts and everything else is optional:

```bash
.venv/bin/python -m cardgrab grab --set-url "<URL>" --out <FOLDER>
```

**`--set-url`** — the set's checklist page on TCDB. Open the set in your browser
and copy the address bar. Both of these work:

```
https://www.tcdb.com/Checklist.cfm/sid/598914/2026-Panini-Prizm-Monopoly-FIFA-World-Cup
https://www.tcdb.com/ViewSet.cfm/sid/598914
```

Quote the URL — set names contain characters your shell will otherwise mangle.

**`--out`** — where images are saved. One folder per set.

### Options

| Option | Default | What it does |
|---|---|---|
| `--limit N` | all | Stop after N cards. **Use `--limit 3` first** to check a new set. |
| `--delay S` | `2.5` | Seconds between cards. Grows automatically if the site rate-limits. Do not lower it. |
| `--retries N` | `3` | Extra passes over cards that failed, each after a longer pause. |
| `--max-sides N` | `2` | Images per card. `2` is front and back; `1` is fronts only. |
| `--headless` | off | Hide the window. You cannot solve a verification page this way. |
| `--keep-open` | off | Leave the browser open when finished. |
| `--profile DIR` | `~/.cardgrab/chrome-profile` | Where verification is remembered. |

### Worked examples

Test a new set before committing to a full run:

```bash
.venv/bin/python -m cardgrab grab --set-url "<URL>" --out ~/Downloads/newset --limit 3
```

Fronts only, and gentler on the server:

```bash
.venv/bin/python -m cardgrab grab --set-url "<URL>" --out ~/Downloads/newset \
  --max-sides 1 --delay 4
```

Finish a set that was interrupted — same command, no extra flags:

```bash
.venv/bin/python -m cardgrab grab --set-url "<URL>" --out ~/Downloads/newset
```

---

## Building the `auto` command

```bash
python3 -m cardgrab auto <FOLDER>
```

Takes the downloaded folder and produces the finished collection in
`~/Pictures/CardCollections/`. It reads the set's real name from the manifest
`grab` wrote, so the collection folder is named correctly whatever you called
the download folder.

| Option | What it does |
|---|---|
| `--inspect` | Show what it would do. Writes nothing. |
| `--dry-run` | Run fully and report, but write no files. |
| `--name "…"` | Override the collection name. |
| `--output-root DIR` | Somewhere other than `~/Pictures/CardCollections`. |
| `--min-width N` | Ignore images narrower than N pixels. |

Every run ends with an explicit account of what you actually got:

```
Import
  written          198
  duplicates       4 (identical bytes, skipped)
  rejected junk    6

Coverage
  fronts  100/100
  backs   98/100
  missing backs:  15, 87
```

### Reusing settings

For a set you process repeatedly, save the options to a file instead of
retyping them:

```bash
python3 -m cardgrab init                          # asks a few questions
python3 -m cardgrab run collections/my-set.toml
```

---

## How it works

The work is split at the network boundary.

**`grab`** drives a real Chromium window. It opens each card's page, finds the
card-shaped images, follows the link to the full-size scan where one exists, and
saves both sides. It also writes `cardgrab-manifest.json` — the checklist, with
player names recovered from the card URLs.

**Everything else is offline.** The pipeline that filters, deduplicates, names
and audits never makes a network request. A test enforces this: only `grab.py`
and `sources/parsebot.py` may import networking.

### On bot protection

TCDB sits behind a Cloudflare managed challenge. cardgrab contains **no stealth,
evasion or fingerprint patching**, and no `cf_clearance` forgery. Instead:

- The browser window is **visible by default**, so when verification appears
  **you** solve it and the script continues.
- A **persistent profile** keeps that clearance between runs.
- If verification reappears three times, it **stops** rather than hammering
  the site.

### On rate limiting

TCDB returns HTTP 429 when asked for too much too quickly. cardgrab treats that
as an instruction, not an obstacle:

- It honours the server's `Retry-After` header when one is sent.
- Otherwise it backs off 5s → 10s → 20s, capped at 90s.
- Every 429 **permanently widens** the gap between requests for the rest of the
  run. The pace never speeds back up.
- Cards that still failed get retry passes afterwards, each preceded by a
  cool-off, until the set is complete.

### Deduplication

Images are identified by SHA-256 of their bytes, not their filenames. The same
image saved under three names yields one file, and re-running merges into the
existing folder rather than producing `image(1).jpg`.

### Junk rejection

Cards occupy a narrow aspect-ratio band (~0.71) above a minimum size. Banners,
avatars, ad units and tracking pixels fail on shape, size or both. Tunable per
collection; check the effect with `--inspect` before changing anything.

---

## Optional: checklists via the parse.bot API

An [unofficial TCDB API](https://parse.bot/marketplace/bc218db9-7d58-420a-858f-0c86440986f5/tcdb-com-api)
can supply checklists, including `team` and `notations` (RC, SP, AU) that the
page parser does not see. It **returns no card images**, so it complements
`grab` rather than replacing it.

```bash
export PARSE_API_KEY='your-key'
python3 -m cardgrab sets --year 2026 --sport Soccer --query "Prizm Monopoly"
python3 -m cardgrab checklist --sid 598914 --out ~/Downloads/monopoly/cardgrab-manifest.json
```

Calls cost credits: `sets` 1, `checklist` 3.

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `No card links found` | Wrong page, or it had not finished loading | Try the `ViewSet.cfm` URL. The page is saved to `_debug-page.html` for inspection |
| Lots of `429` | Requesting too fast | It now backs off and retries automatically; raise `--delay` if it persists |
| Verification every card | Cloudflare is not accepting the session | Solve it in the window; if it keeps returning, stop and try later |
| Images are ~250px | The card pages offered no full-size version | Check `_image-sources.json` for what was actually available |
| Everything in `_unmatched/` | No checklist in the folder | Ensure `cardgrab-manifest.json` is present |
| `Playwright is not installed` | Missing browser driver | `.venv/bin/pip install playwright && .venv/bin/playwright install chromium` |

---

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -t .
```

63 tests. The pipeline tests need no network and no browser; Playwright
behaviour is verified against local fixture servers, including one that
deliberately returns 429 to prove the backoff and retry logic recovers a full
set.

---

## Project layout

```
cardgrab/
  grab.py        Playwright downloader (network)
  cardnames.py   recover player names from card URLs
  config.py      collection settings (TOML)
  models.py      CardRef, ImageAsset, MatchResult
  imagemeta.py   image dimensions, standard library only
  sources/       checklist adapters (saved page, manifest, parse.bot API)
  harvest.py     find card images, reject junk
  matcher.py     bind images to cards
  dedupe.py      SHA-256 content identity
  naming.py      output filenames
  pipeline.py    orchestration
  report.py      coverage reporting
  cli.py         command line entry point
collections/     saved per-set settings
tests/           test suite
```

## Please use it considerately

This tool makes requests to someone else's servers. The defaults are
deliberately slow and the backoff is deliberately generous. Leave them alone,
and do not run several sets in parallel.

## Licence

MIT
