"""Test suite. Runs on the standard library alone.

Pillow is used only to generate genuine image fixtures; the tests that need it
skip cleanly when it is absent.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cardgrab import naming, pipeline
from cardgrab.config import CollectionConfig, ConfigError, load, safe_dirname
from cardgrab.dedupe import SeenSet, sha256
from cardgrab.imagemeta import UnreadableImage, probe
from cardgrab.matcher import match_all
from cardgrab.models import CardRef, ImageAsset
from cardgrab.sources.manifest import ManifestError, ManifestSource
from cardgrab.sources.saved_page import SavedPageSource, side_from_name

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

CARD = (240, 336)  # a 0.714 aspect ratio, the real card shape


def make_image(path: Path, size=CARD, colour=(200, 30, 30), fmt=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, format=fmt)
    return path


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cardgrab-test-"))
        self.inbox = self.tmp / "inbox"
        self.out = self.tmp / "out"
        self.inbox.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self, **overrides) -> CollectionConfig:
        params = dict(
            name="2026 Panini Prizm FIFA World Cup Monopoly",
            input_dirs=[self.inbox],
            output_root=self.out,
        )
        params.update(overrides)
        return CollectionConfig(**params)


@unittest.skipUnless(HAS_PILLOW, "Pillow needed to generate fixtures")
class ImageMetaTests(TempCase):
    def test_probes_every_supported_format(self):
        for name, fmt in [
            ("a.jpg", "JPEG"), ("b.png", "PNG"), ("c.gif", "GIF"),
            ("d.webp", "WEBP"), ("e.bmp", "BMP"),
        ]:
            path = make_image(self.inbox / name, size=(240, 336), fmt=fmt)
            self.assertEqual(probe(path), (240, 336), f"{name} probed wrong")

    def test_rejects_non_image(self):
        junk = self.inbox / "notes.jpg"
        junk.write_bytes(b"this is definitely not an image file at all")
        with self.assertRaises(UnreadableImage):
            probe(junk)

    def test_rejects_truncated_file(self):
        stub = self.inbox / "stub.png"
        stub.write_bytes(b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(UnreadableImage):
            probe(stub)


class NamingTests(unittest.TestCase):
    def test_pads_to_set_width(self):
        width = naming.number_width(["1", "23", "150"])
        self.assertEqual(width, 3)
        self.assertEqual(
            naming.filename("7", "Lionel Messi", "front", ".jpg", width),
            "007 - Lionel Messi - front.jpg",
        )

    def test_preserves_non_numeric_numbers(self):
        width = naming.number_width(["1", "150"])
        self.assertEqual(naming.pad("RC-12", width), "RC-12")

    def test_strips_unsafe_characters(self):
        result = naming.filename("5", "A/B:C*D?", "back", ".png", 2)
        self.assertEqual(result, "05 - ABCD - back.png")
        for char in '/\\:*?"<>|':
            self.assertNotIn(char, result)

    def test_handles_missing_player_name(self):
        self.assertEqual(
            naming.filename("9", "", "front", ".jpg", 2), "09 - front.jpg"
        )

    def test_rejects_invalid_side(self):
        with self.assertRaises(ValueError):
            naming.filename("1", "X", "sideways", ".jpg", 2)

    def test_disambiguates_collisions(self):
        taken = {"a.jpg"}
        self.assertEqual(naming.disambiguate("a.jpg", taken), "a (2).jpg")
        taken.add("a (2).jpg")
        self.assertEqual(naming.disambiguate("a.jpg", taken), "a (3).jpg")

    def test_collection_name_is_safe_as_dirname(self):
        self.assertEqual(safe_dirname("2026 Panini: Prizm/FIFA"), "2026 Panini- Prizm-FIFA")


@unittest.skipUnless(HAS_PILLOW, "Pillow needed to generate fixtures")
class DedupeTests(TempCase):
    def test_identical_bytes_under_different_names_share_a_hash(self):
        a = make_image(self.inbox / "card.jpg")
        b = self.inbox / "card-copy.jpg"
        shutil.copy2(a, b)
        self.assertEqual(sha256(a), sha256(b))

    def test_seen_set_reports_first_only(self):
        seen = SeenSet()
        self.assertTrue(seen.add("hash1", Path("a")))
        self.assertFalse(seen.add("hash1", Path("b")))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen.origin("hash1"), Path("a"))


class ChecklistParsingTests(TempCase):
    HTML = """
    <html><body><table>
      <tr><th>#</th><th>Player</th></tr>
      <tr><td>1</td><td>Lionel Messi</td></tr>
      <tr><td>2</td><td>Kylian Mbappe</td></tr>
      <tr><td>10</td><td>Erling Haaland</td></tr>
      <tr><td>RC-4</td><td>Lamine Yamal</td></tr>
    </table>
    <img src="/Images/Cards/Soccer/1-Front.jpg">
    <img src="/Images/spacer.gif">
    </body></html>
    """

    def test_parses_number_and_name_from_table(self):
        page = self.tmp / "set.html"
        page.write_text(self.HTML, encoding="utf-8")
        source = SavedPageSource([page])
        cards = source.cards()

        self.assertEqual(len(cards), 4)
        self.assertEqual(cards[0].number, "1")
        self.assertEqual(cards[0].name, "Lionel Messi")
        self.assertIn("table-row", source.strategy)

    def test_sorts_numerically_not_lexically(self):
        page = self.tmp / "set.html"
        page.write_text(self.HTML, encoding="utf-8")
        numbers = [c.number for c in SavedPageSource([page]).cards()]
        self.assertEqual(numbers[:3], ["1", "2", "10"])
        self.assertEqual(numbers[-1], "RC-4")

    def test_falls_back_to_anchors_without_a_table(self):
        page = self.tmp / "gallery.html"
        page.write_text(
            '<a href="/ViewCard.cfm/sid/1/cid/5">5 Jude Bellingham</a>'
            '<a href="/about.html">About us</a>',
            encoding="utf-8",
        )
        source = SavedPageSource([page])
        cards = source.cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].name, "Jude Bellingham")
        self.assertIn("anchor", source.strategy)

    def test_survives_malformed_markup(self):
        page = self.tmp / "broken.html"
        page.write_text("<table><tr><td>1<td>Messi</table></tr", encoding="utf-8")
        self.assertIsInstance(SavedPageSource([page]).cards(), list)

    def test_reads_side_from_filename(self):
        self.assertEqual(side_from_name("123-Front.jpg"), "front")
        self.assertEqual(side_from_name("123_back.png"), "back")
        self.assertIsNone(side_from_name("plain.jpg"))


class ManifestTests(TempCase):
    def test_reads_cards_and_hints(self):
        path = self.tmp / "m.json"
        path.write_text(json.dumps({"cards": [
            {"number": "1", "name": "Messi", "front_url": "https://x/1-Front.jpg"},
        ]}), encoding="utf-8")
        cards = ManifestSource(path).cards()
        self.assertEqual(cards[0].name, "Messi")
        self.assertEqual(cards[0].image_hints, ("https://x/1-Front.jpg",))

    def test_accepts_bare_list(self):
        path = self.tmp / "m.json"
        path.write_text(json.dumps([{"number": "3", "name": "Kane"}]), encoding="utf-8")
        self.assertEqual(ManifestSource(path).cards()[0].number, "3")

    def test_reports_missing_file_clearly(self):
        with self.assertRaises(ManifestError):
            ManifestSource(self.tmp / "nope.json").cards()

    def test_rejects_wrong_shape(self):
        path = self.tmp / "m.json"
        path.write_text(json.dumps({"cards": ["just a string"]}), encoding="utf-8")
        with self.assertRaises(ManifestError):
            ManifestSource(path).cards()


class MatcherTests(unittest.TestCase):
    def asset(self, name: str) -> ImageAsset:
        return ImageAsset(Path(name), "hash-" + name, 240, 336, ".jpg")

    def test_matches_number_token_despite_padding(self):
        cards = [CardRef("12", "Messi")]
        result = match_all([self.asset("012-Front.jpg")], cards)[0]
        self.assertEqual(result.card, cards[0])
        self.assertEqual(result.side, "front")
        self.assertEqual(result.strategy, "number-token")

    def test_matches_url_hint_from_manifest(self):
        cards = [CardRef("12", "Messi", image_hints=("https://x/abc123.jpg",))]
        result = match_all([self.asset("abc123.jpg")], cards)[0]
        self.assertEqual(result.strategy, "url-hint")

    def test_leaves_unknown_images_unmatched(self):
        result = match_all([self.asset("banner-logo.jpg")], [CardRef("12", "Messi")])[0]
        self.assertFalse(result.matched)
        self.assertEqual(result.strategy, "unmatched")

    def test_ignores_ambiguous_surnames(self):
        # Two cards share 'Silva', so 'Silva' must never name either one.
        cards = [CardRef("1", "Bernardo Silva"), CardRef("2", "Thiago Silva")]
        result = match_all([self.asset("Silva.jpg")], cards)[0]
        self.assertFalse(result.matched)

    def test_no_checklist_leaves_everything_unmatched(self):
        results = match_all([self.asset("x.jpg")], [])
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].matched)


@unittest.skipUnless(HAS_PILLOW, "Pillow needed to generate fixtures")
class PipelineTests(TempCase):
    def build_input(self):
        """A realistic browser save: cards, a duplicate, and assorted junk."""
        make_image(self.inbox / "1-Front.jpg", CARD, (200, 30, 30))
        make_image(self.inbox / "1-Back.jpg", CARD, (30, 200, 30))
        make_image(self.inbox / "2-Front.jpg", CARD, (30, 30, 200))
        shutil.copy2(self.inbox / "1-Front.jpg", self.inbox / "copy-of-1-Front.jpg")
        make_image(self.inbox / "logo.png", (900, 90), fmt="PNG")     # banner
        make_image(self.inbox / "avatar.png", (64, 64), fmt="PNG")    # too small
        make_image(self.inbox / "icon.gif", (16, 16), fmt="GIF")      # pixel

        checklist = self.tmp / "set.html"
        checklist.write_text(
            "<table>"
            "<tr><td>1</td><td>Lionel Messi</td></tr>"
            "<tr><td>2</td><td>Kylian Mbappe</td></tr>"
            "<tr><td>3</td><td>Erling Haaland</td></tr>"
            "</table>",
            encoding="utf-8",
        )
        return checklist

    def test_end_to_end_produces_named_deduped_collection(self):
        checklist = self.build_input()
        config = self.config(checklist_html=[checklist])
        result = pipeline.run(config)

        folder = self.out / "2026 Panini Prizm FIFA World Cup Monopoly"
        self.assertTrue(folder.is_dir(), "collection folder named after the collection")

        names = sorted(p.name for p in folder.glob("*.jpg"))
        self.assertEqual(names, [
            "1 - Lionel Messi - back.jpg",
            "1 - Lionel Messi - front.jpg",
            "2 - Kylian Mbappe - front.jpg",
        ])

        self.assertEqual(result.stats.duplicates, 1, "byte-identical copy skipped")
        self.assertEqual(result.stats.rejected_junk, 3, "banner, avatar and pixel")

    def test_reports_missing_cards(self):
        checklist = self.build_input()
        result = pipeline.run(self.config(checklist_html=[checklist]))
        self.assertEqual(result.coverage.total_cards, 3)
        self.assertEqual(result.coverage.fronts, 2)
        self.assertIn("3", result.coverage.missing_fronts)

    def test_rerun_is_idempotent(self):
        checklist = self.build_input()
        config = self.config(checklist_html=[checklist])

        pipeline.run(config)
        folder = self.out / "2026 Panini Prizm FIFA World Cup Monopoly"
        first = sorted(p.name for p in folder.rglob("*") if p.is_file())

        second_result = pipeline.run(config)
        second = sorted(p.name for p in folder.rglob("*") if p.is_file())

        self.assertEqual(first, second, "re-run must not duplicate files")
        self.assertEqual(second_result.stats.written, 0)
        self.assertFalse(any("(2)" in n for n in second))

    def test_split_layout_separates_sides(self):
        checklist = self.build_input()
        result = pipeline.run(self.config(checklist_html=[checklist], layout="split"))
        self.assertTrue((result.output_dir / "front").is_dir())
        self.assertTrue((result.output_dir / "back").is_dir())

    def test_unmatched_images_are_kept_not_misnamed(self):
        self.build_input()
        make_image(self.inbox / "mystery-card.jpg", CARD, (99, 99, 99))
        checklist = self.tmp / "set.html"
        result = pipeline.run(self.config(checklist_html=[checklist]))

        stray = result.output_dir / "_unmatched" / "mystery-card.jpg"
        self.assertTrue(stray.is_file(), "unknown card kept aside rather than dropped")

    def test_dry_run_writes_nothing(self):
        checklist = self.build_input()
        result = pipeline.run(self.config(checklist_html=[checklist]), dry_run=True)
        self.assertGreater(result.stats.written, 0, "still reports what it would do")
        self.assertFalse(self.out.exists(), "but touches no disk")

    def test_works_without_a_checklist(self):
        self.build_input()
        result = pipeline.run(self.config())
        self.assertEqual(result.coverage.total_cards, 0)
        self.assertGreater(result.stats.unmatched, 0)
        self.assertTrue((result.output_dir / "_unmatched").is_dir())

    def test_writes_report_sidecar(self):
        checklist = self.build_input()
        result = pipeline.run(self.config(checklist_html=[checklist]))
        sidecar = result.output_dir / "_report.json"
        self.assertTrue(sidecar.is_file())
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(payload["coverage"]["total_cards"], 3)

    def test_summary_flags_thumbnail_resolution(self):
        checklist = self.build_input()
        result = pipeline.run(self.config(checklist_html=[checklist]))
        self.assertIn("thumbnails", result.summary("x"))


class ConfigTests(TempCase):
    def write(self, body: str) -> Path:
        path = self.tmp / "c.toml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_loads_a_valid_collection(self):
        path = self.write(
            f'name = "Test Set"\n'
            f'input_dirs = ["{self.inbox}"]\n'
            f'output_root = "{self.out}"\n'
        )
        config = load(path)
        self.assertEqual(config.name, "Test Set")
        self.assertEqual(config.output_dir.name, "Test Set")

    def test_missing_required_key_is_explicit(self):
        path = self.write('name = "X"\n')
        with self.assertRaises(ConfigError) as ctx:
            load(path)
        self.assertIn("input_dirs", str(ctx.exception))

    def test_missing_input_dir_is_caught(self):
        path = self.write(
            f'name = "X"\ninput_dirs = ["{self.tmp}/nope"]\noutput_root = "{self.out}"\n'
        )
        with self.assertRaises(ConfigError):
            load(path)

    def test_inverted_aspect_bounds_rejected(self):
        with self.assertRaises(ConfigError):
            self.config(aspect_min=0.9, aspect_max=0.5).validate()

    def test_bad_layout_rejected(self):
        with self.assertRaises(ConfigError):
            self.config(layout="sideways").validate()


class ArchitectureTests(unittest.TestCase):
    NETWORK_ALLOWED = {"parsebot.py", "grab.py"}

    def test_only_the_api_client_touches_the_network(self):
        """Core stays offline; network access is confined to one named module.

        The pipeline must keep working on local files alone, so a network
        import leaking into harvest/match/dedupe/naming is a design break.
        """
        banned = {
            "requests", "urllib.request", "urllib3", "httpx", "aiohttp",
            "socket", "http.client", "ftplib", "cloudscraper", "selenium",
        }
        offenders = []
        for path in Path("cardgrab").rglob("*.py"):
            # Skip macOS AppleDouble sidecars, which are binary and match *.py.
            if path.name.startswith("._") or path.name in self.NETWORK_ALLOWED:
                continue
            text = path.read_text(encoding="utf-8")
            for module in banned:
                if f"import {module}" in text:
                    offenders.append(f"{path}: {module}")
        self.assertEqual(offenders, [], f"network imports found: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class PrefixedNumberRegressionTests(unittest.TestCase):
    """A prefixed card number must never be filed under its bare digits.

    RC-1-Front.jpg once matched card 1, naming Endrick's card after Messi.
    """

    def asset(self, name: str) -> ImageAsset:
        return ImageAsset(Path(name), "hash-" + name, 240, 336, ".jpg")

    def test_prefixed_number_beats_bare_digits(self):
        cards = [CardRef("1", "Lionel Messi"), CardRef("RC-1", "Endrick Felipe")]
        result = match_all([self.asset("RC-1-Front.jpg")], cards)[0]
        self.assertEqual(result.card.number, "RC-1")
        self.assertEqual(result.card.name, "Endrick Felipe")

    def test_plain_number_still_matches(self):
        cards = [CardRef("1", "Lionel Messi"), CardRef("RC-1", "Endrick Felipe")]
        result = match_all([self.asset("1-Front.jpg")], cards)[0]
        self.assertEqual(result.card.number, "1")

    def test_zero_padded_filename_still_matches(self):
        cards = [CardRef("12", "Bernardo Silva")]
        result = match_all([self.asset("012-Front.jpg")], cards)[0]
        self.assertEqual(result.card.number, "12")

    def test_variant_suffix_matches(self):
        cards = [CardRef("25a", "Pedri"), CardRef("25", "Gavi")]
        result = match_all([self.asset("25a-Front.jpg")], cards)[0]
        self.assertEqual(result.card.number, "25a")


class InitTests(TempCase):
    def test_slugifies_collection_name(self):
        from cardgrab.initialize import slugify

        self.assertEqual(
            slugify("2026 Panini Prizm FIFA World Cup Monopoly"),
            "2026-panini-prizm-fifa-world-cup-monopoly",
        )

    def test_finds_saved_html_checklist(self):
        from cardgrab.initialize import find_checklist

        (self.inbox / "set.html").write_text("<table></table>", encoding="utf-8")
        kind, path = find_checklist(self.inbox)
        self.assertEqual(kind, "html")
        self.assertEqual(path.name, "set.html")

    def test_manifest_wins_over_html(self):
        from cardgrab.initialize import find_checklist

        (self.inbox / "set.html").write_text("<table></table>", encoding="utf-8")
        (self.inbox / "cardgrab-manifest.json").write_text("[]", encoding="utf-8")
        kind, _ = find_checklist(self.inbox)
        self.assertEqual(kind, "manifest")

    def test_generated_toml_loads_back(self):
        """The whole point of init: its output must be a valid config."""
        from cardgrab.initialize import find_checklist, render

        (self.inbox / "set.html").write_text(
            "<table><tr><td>1</td><td>Messi</td></tr></table>", encoding="utf-8"
        )
        text = render(
            name="My Set",
            input_dir=self.inbox,
            output_root=self.out,
            checklist=find_checklist(self.inbox),
        )
        path = self.tmp / "generated.toml"
        path.write_text(text, encoding="utf-8")

        config = load(path)
        self.assertEqual(config.name, "My Set")
        self.assertEqual(len(config.checklist_html), 1)

    def test_does_not_clobber_without_force(self):
        from cardgrab.initialize import write

        dest = self.tmp / "c.toml"
        dest.write_text("original", encoding="utf-8")
        self.assertFalse(write(dest, "new", force=False))
        self.assertEqual(dest.read_text(), "original")
        self.assertTrue(write(dest, "new", force=True))


class ParseBotTests(unittest.TestCase):
    """The API client, verified against a local server mimicking the docs.

    No API key or network access is required to run these.
    """

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.headers.get("X-API-Key") != "test-key":
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b"{}")
                    return

                if "list_sets" in self.path:
                    # Exactly the sample shape from the published docs.
                    body = {
                        "data": {"sets": [{
                            "sid": "506623",
                            "url": "https://www.tcdb.com/ViewSet.cfm/sid/506623/2025-Bowman",
                            "name": "2025 Bowman",
                            "year": "2025",
                            "sport": "Baseball",
                        }]},
                        "status": "success",
                    }
                elif "get_checklist" in self.path:
                    body = {
                        "data": {"cards": [
                            {"number": "1", "name": "Lionel Messi", "team": "Argentina", "notations": ""},
                            {"number": "10", "name": "Bukayo Saka", "team": "England", "notations": "RC"},
                            {"number": "2", "name": "Kylian Mbappe", "team": "France", "notations": ""},
                        ]},
                        "status": "success",
                    }
                elif "broken" in self.path:
                    body = {"status": "error", "message": "set not found"}
                else:
                    body = {"data": {}, "status": "success"}

                payload = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        from cardgrab.sources import parsebot

        self.parsebot = parsebot
        self._real_base = parsebot.BASE_URL
        parsebot.BASE_URL = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.parsebot.BASE_URL = self._real_base

    def test_lists_sets(self):
        found = self.parsebot.list_sets(year="2025", sport="Baseball", key="test-key")
        self.assertEqual(found[0]["sid"], "506623")

    def test_checklist_parses_and_sorts(self):
        cards = self.parsebot.get_checklist("598914", key="test-key")
        self.assertEqual([c.number for c in cards], ["1", "2", "10"])
        self.assertEqual(cards[0].name, "Lionel Messi")

    def test_checklist_carries_no_image_urls(self):
        """Documents the finding: this API supplies no images."""
        cards = self.parsebot.get_checklist("598914", key="test-key")
        self.assertTrue(all(c.image_hints == () for c in cards))

    def test_bad_key_is_explained(self):
        with self.assertRaises(self.parsebot.ParseBotError) as ctx:
            self.parsebot.list_sets(year="2025", key="wrong-key")
        self.assertIn("API key", str(ctx.exception))

    def test_api_error_surfaces_message(self):
        with self.assertRaises(self.parsebot.ParseBotError) as ctx:
            self.parsebot.call("broken", {}, "test-key")
        self.assertIn("set not found", str(ctx.exception))

    def test_missing_key_tells_user_how_to_set_it(self):
        import os

        saved = os.environ.pop("PARSE_API_KEY", None)
        try:
            with self.assertRaises(self.parsebot.ParseBotError) as ctx:
                self.parsebot.api_key(None)
            self.assertIn("PARSE_API_KEY", str(ctx.exception))
        finally:
            if saved:
                os.environ["PARSE_API_KEY"] = saved

    def test_fetch_checklist_writes_usable_manifest(self):
        """The manifest it writes must feed the offline pipeline unchanged."""
        from cardgrab.sources.manifest import ManifestSource

        tmp = Path(tempfile.mkdtemp(prefix="cardgrab-pb-"))
        try:
            out = tmp / "cardgrab-manifest.json"
            count = self.parsebot.fetch_checklist("598914", out, key="test-key")
            self.assertEqual(count, 3)

            cards = ManifestSource(out).cards()
            self.assertEqual([c.number for c in cards], ["1", "2", "10"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SideDetectionRegressionTests(unittest.TestCase):
    """Backs were being filed as fronts because of the separator.

    `grab` writes "1 - Lionel Messi - back.jpg"; the old pattern only accepted
    a dash or underscore directly before the marker, so every back defaulted to
    front and produced "front (2)" collisions.
    """

    def test_space_separated_side(self):
        self.assertEqual(side_from_name("1 - Lionel Messi - back.jpg"), "back")
        self.assertEqual(side_from_name("1 - Lionel Messi - front.jpg"), "front")

    def test_dash_separated_still_works(self):
        self.assertEqual(side_from_name("123-Front.jpg"), "front")
        self.assertEqual(side_from_name("123_back.png"), "back")

    def test_tcdb_style_abbreviations(self):
        self.assertEqual(side_from_name("34990918-Bk.jpg"), "back")
        self.assertEqual(side_from_name("34990918-Fr.jpg"), "front")

    def test_no_marker_returns_none(self):
        self.assertIsNone(side_from_name("plain.jpg"))
        self.assertIsNone(side_from_name("1 - Lionel Messi.jpg"))

    def test_player_name_is_not_read_as_a_side(self):
        # "Frontera" must not register as "front".
        self.assertEqual(side_from_name("7 - Ana Frontera - back.jpg"), "back")

    def test_pipeline_files_both_sides(self):
        """End to end: grab-style filenames must yield fronts AND backs."""
        if not HAS_PILLOW:
            self.skipTest("Pillow needed")

        tmp = Path(tempfile.mkdtemp(prefix="cardgrab-sides-"))
        try:
            inbox = tmp / "in"
            inbox.mkdir()
            for number in (1, 2):
                for side, colour in (("front", (200, 30, 30)), ("back", (30, 30, 200))):
                    Image.new("RGB", CARD, (colour[0] + number, colour[1], colour[2])).save(
                        inbox / f"{number} - Player {number} - {side}.jpg"
                    )
            (inbox / "m.json").write_text(
                json.dumps({"cards": [
                    {"number": "1", "name": "Player 1"},
                    {"number": "2", "name": "Player 2"},
                ]}), encoding="utf-8"
            )

            config = CollectionConfig(
                name="Sides Test",
                input_dirs=[inbox],
                output_root=tmp / "out",
                manifest_json=inbox / "m.json",
            )
            result = pipeline.run(config)

            self.assertEqual(result.coverage.fronts, 2)
            self.assertEqual(result.coverage.backs, 2, "backs must not be filed as fronts")
            names = sorted(p.name for p in result.output_dir.glob("*.jpg"))
            self.assertFalse(any("(2)" in n for n in names), names)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
