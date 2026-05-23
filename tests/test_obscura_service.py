import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from obscura_service import (  # noqa: E402
    build_search_url,
    decode_duckduckgo_url,
    extract_forced_query,
    is_url_allowed,
    parse_duckduckgo_results,
    resolve_obscura_path,
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


if __name__ == "__main__":
    unittest.main()
