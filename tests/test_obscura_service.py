import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from obscura_service import (  # noqa: E402
    ObscuraError,
    ObscuraSearchService,
    SearchConfig,
    SearchResult,
    build_search_url,
    config_from_mapping,
    decode_duckduckgo_url,
    extract_forced_query,
    is_valid_forced_evidence_template,
    is_valid_summary_prompt_template,
    is_url_allowed,
    parse_duckduckgo_results,
    parse_page_evidence,
    resolve_obscura_path,
    resolve_summary_prompt_template,
)


class ObscuraServiceTests(unittest.TestCase):
    def test_resolve_configured_obscura_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            executable = Path(tmpdir) / "obscura.exe"
            executable.write_text("", encoding="utf-8")

            self.assertEqual(resolve_obscura_path(str(executable)), str(executable))

    def test_resolve_plugin_relative_obscura_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            binary_dir = Path(tmpdir) / "bin"
            binary_dir.mkdir()
            executable = binary_dir / "obscura.exe"
            executable.write_text("", encoding="utf-8")

            self.assertEqual(
                resolve_obscura_path("bin/obscura.exe", base_dir=tmpdir),
                str(executable),
            )

    def test_extract_forced_query(self):
        prefixes = ["搜索", "search"]

        self.assertEqual(extract_forced_query("搜索 AstrBot 插件开发", prefixes), "AstrBot 插件开发")
        self.assertEqual(extract_forced_query("搜索：AstrBot 插件开发", prefixes), "AstrBot 插件开发")
        self.assertEqual(extract_forced_query("search AstrBot plugin", prefixes), "AstrBot plugin")
        self.assertIsNone(extract_forced_query("普通聊天", prefixes))
        self.assertIsNone(extract_forced_query("/search AstrBot plugin", prefixes))
        self.assertEqual(
            extract_forced_query("/search AstrBot plugin", prefixes, include_slash_commands=True),
            "AstrBot plugin",
        )

    def test_decode_duckduckgo_url(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fx%3D1&rut=abc"
        self.assertEqual(decode_duckduckgo_url(href), "https://example.com/a?x=1")

    def test_url_safety_defaults(self):
        self.assertTrue(is_url_allowed("https://example.com/page"))
        self.assertFalse(is_url_allowed("file:///C:/secret.txt"))
        self.assertFalse(is_url_allowed("http://localhost:8000"))
        self.assertFalse(is_url_allowed("http://127.0.0.1:8000"))
        self.assertFalse(is_url_allowed("http://192.168.1.20"))
        self.assertTrue(is_url_allowed("http://127.0.0.1:8000", allow_private_urls=True))

    def test_parse_duckduckgo_results(self):
        html = """
        <div class="result">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fone">Example One</a>
          <a class="result__snippet">First result snippet.</a>
        </div>
        <div class="result">
          <a class="result__a" href="https://example.org/two">Example Two</a>
          <a class="result__snippet">Second result snippet.</a>
        </div>
        <div class="result">
          <a class="result__a" href="http://127.0.0.1/private">Blocked Local</a>
          <a class="result__snippet">Should be blocked.</a>
        </div>
        """

        results = parse_duckduckgo_results(html, limit=5)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "Example One")
        self.assertEqual(results[0].url, "https://example.com/one")
        self.assertEqual(results[0].snippet, "First result snippet.")
        self.assertEqual(results[1].url, "https://example.org/two")

    def test_build_search_url(self):
        self.assertEqual(
            build_search_url("https://html.duckduckgo.com/html/?q={query}", "AstrBot 插件"),
            "https://html.duckduckgo.com/html/?q=AstrBot+%E6%8F%92%E4%BB%B6",
        )

    def test_config_trigger_switches(self):
        config = config_from_mapping(
            {
                "enable_force_commands": False,
                "enable_force_prefixes": False,
                "auto_search_policy": "always",
                "force_trigger_mode": "direct_reply",
            }
        )

        self.assertFalse(config.enable_force_commands)
        self.assertFalse(config.enable_force_prefixes)
        self.assertEqual(config.auto_search_policy, "always")
        self.assertEqual(config.force_trigger_mode, "direct_reply")

    def test_prompt_source_file_and_config_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_dir = Path(tmpdir) / "prompts"
            prompt_dir.mkdir()
            prompt_file = prompt_dir / "summary.md"
            prompt_file.write_text("FILE {query} {evidence}", encoding="utf-8")
            config = config_from_mapping(
                {
                    "summary_prompt_file": "prompts/summary.md",
                    "summary_prompt_template": "CONFIG {query} {evidence}",
                    "summary_prompt_source": "file",
                }
            )
            config_source = config_from_mapping(
                {
                    "summary_prompt_file": "prompts/summary.md",
                    "summary_prompt_template": "CONFIG {query} {evidence}",
                    "summary_prompt_source": "config",
                }
            )

            self.assertEqual(resolve_summary_prompt_template(config, base_dir=tmpdir), "FILE {query} {evidence}")
            self.assertEqual(resolve_summary_prompt_template(config_source, base_dir=tmpdir), "CONFIG {query} {evidence}")
            self.assertTrue(is_valid_summary_prompt_template("OK {query} {evidence}"))
            self.assertTrue(is_valid_forced_evidence_template("OK {query} {evidence}"))
            self.assertFalse(is_valid_summary_prompt_template("missing placeholders"))

    def test_prompt_source_errors_do_not_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_file = Path(tmpdir) / "summary.md"
            prompt_file.write_text("invalid", encoding="utf-8")
            config = config_from_mapping(
                {
                    "summary_prompt_file": "summary.md",
                    "summary_prompt_template": "also invalid",
                }
            )

            with self.assertRaises(ObscuraError):
                resolve_summary_prompt_template(config, base_dir=tmpdir)

    def test_media_config_replaces_off_with_enable_switch(self):
        config = config_from_mapping(
            {
                "enable_media_extraction": False,
                "media_extract_mode": "off",
                "image_caption_provider_id": "vision",
            }
        )

        self.assertFalse(config.enable_media_extraction)
        self.assertEqual(config.media_extract_mode, "metadata_only")
        self.assertEqual(config.image_caption_provider_id, "vision")

    def test_parse_page_evidence_media_and_design_tokens(self):
        html = """
        <html>
          <head>
            <title>Portfolio</title>
            <meta name="description" content="Personal design blog">
            <meta property="og:title" content="OG Portfolio">
            <meta property="og:description" content="Design notes">
            <meta property="og:image" content="/cover.jpg">
            <style>
              body { color: #123456; font-family: Inter, sans-serif; }
              .hero { background: rgba(1, 2, 3, 0.4); }
            </style>
          </head>
          <body>
            <nav><a href="/work">Work</a></nav>
            <h1>Selected Projects</h1>
            <figure>
              <img src="/hero.png" alt="Hero artwork" title="Hero title">
              <figcaption>Main hero composition</figcaption>
            </figure>
            <picture>
              <source srcset="/small.webp 1x, /large.webp 2x">
            </picture>
            <a href="/about">About</a>
          </body>
        </html>
        """

        evidence = parse_page_evidence(
            html,
            base_url="https://example.com/index.html",
            max_images=5,
        )

        self.assertEqual(evidence.title, "Portfolio")
        self.assertEqual(evidence.description, "Personal design blog")
        self.assertIn("Selected Projects", evidence.headings)
        self.assertIn("Work", evidence.nav_items)
        self.assertIn("About", evidence.links)
        self.assertIn("#123456", evidence.colors)
        self.assertIn("Inter, sans-serif", evidence.fonts)
        self.assertEqual(evidence.media[0].url, "https://example.com/cover.jpg")
        hero = next(item for item in evidence.media if item.url == "https://example.com/hero.png")
        self.assertEqual(hero.alt, "Hero artwork")
        self.assertEqual(hero.caption, "Main hero composition")

    def test_parse_page_evidence_blocks_private_images_and_mode_off(self):
        html = """
        <img src="http://127.0.0.1/private.png" alt="private">
        <img src="https://example.com/public.png" alt="public">
        """

        evidence = parse_page_evidence(
            html,
            base_url="https://example.com",
            max_images=5,
            allow_private_urls=False,
        )
        no_images = parse_page_evidence(
            html,
            base_url="https://example.com",
            max_images=5,
            allow_private_urls=False,
            include_images=False,
        )

        self.assertEqual([item.url for item in evidence.media], ["https://example.com/public.png"])
        self.assertEqual(no_images.media, [])


class ObscuraServiceAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_enable_media_extraction_false_skips_media_evidence(self):
        class FakeService(ObscuraSearchService):
            async def fetch(self, url: str, *, dump: str = "text") -> str:
                if dump == "text":
                    return "Visible body text"
                return "<h1>Heading</h1><img src='https://example.com/image.png' alt='public'>"

        service = FakeService(SearchConfig(enable_media_extraction=False))
        content, page = await service._fetch_result_evidence(
            SearchResult(title="Example", url="https://example.com")
        )

        self.assertEqual(content, "Visible body text")
        self.assertFalse(page.has_content())

    async def test_visual_focus_keeps_structure_without_media_when_disabled(self):
        class FakeService(ObscuraSearchService):
            async def fetch(self, url: str, *, dump: str = "text") -> str:
                if dump == "text":
                    return "Visible body text"
                return "<h1>Heading</h1><img src='https://example.com/image.png' alt='public'>"

        service = FakeService(
            SearchConfig(enable_media_extraction=False, summary_focus="visual_design")
        )
        _, page = await service._fetch_result_evidence(
            SearchResult(title="Example", url="https://example.com")
        )

        self.assertIn("Heading", page.headings)
        self.assertEqual(page.media, [])


if __name__ == "__main__":
    unittest.main()
