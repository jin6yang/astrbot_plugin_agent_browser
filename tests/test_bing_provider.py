import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_agent_browser.models import ObscuraError, SearchConfig  # noqa: E402
from astrbot_plugin_agent_browser.search_providers.bing import (  # noqa: E402
    BING_HTML_URL_TEMPLATE,
    BING_RSS_URL_TEMPLATE,
    BingProvider,
    build_bing_url,
    parse_bing_results,
    parse_bing_rss,
)

RSS_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Bing Search</title>
    <item>
      <title>Example One</title>
      <link>https://example.com/one</link>
      <description>First   result snippet.</description>
    </item>
    <item>
      <title>Example Two</title>
      <link>https://example.org/two</link>
      <description>Second result &amp; snippet.</description>
    </item>
    <item>
      <title>Duplicate</title>
      <link>https://example.com/one</link>
      <description>Duplicated url.</description>
    </item>
    <item>
      <title></title>
      <link>https://example.com/no-title</link>
      <description>Missing title.</description>
    </item>
  </channel>
</rss>
"""

HTML_SAMPLE = """
<html><body>
<ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://example.com/one">Example One</a></h2>
    <div class="b_caption"><p>First result snippet.</p></div>
  </li>
  <li class="b_algo">
    <h2><a href="//example.org/two">Example Two</a></h2>
    <p class="b_lineclamp2">Second result snippet.</p>
  </li>
  <li class="b_algo">
    <h2><a href="https://example.com/one">Duplicate</a></h2>
    <div class="b_caption"><p>Duplicated url.</p></div>
  </li>
  <li class="b_ad">
    <h2><a href="https://ads.example.com">Ad result</a></h2>
  </li>
</ol>
</body></html>
"""


class BingParseTests(unittest.TestCase):
    def test_build_bing_url(self):
        self.assertEqual(
            build_bing_url(BING_RSS_URL_TEMPLATE, "AstrBot 插件"),
            "https://www.bing.com/search?q=AstrBot+%E6%8F%92%E4%BB%B6&format=rss",
        )

    def test_parse_bing_rss(self):
        results = parse_bing_rss(RSS_SAMPLE, limit=5)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "Example One")
        self.assertEqual(results[0].url, "https://example.com/one")
        self.assertEqual(results[0].snippet, "First result snippet.")
        self.assertEqual(results[1].snippet, "Second result & snippet.")

    def test_parse_bing_rss_limit(self):
        results = parse_bing_rss(RSS_SAMPLE, limit=1)
        self.assertEqual(len(results), 1)

    def test_parse_bing_rss_bad_xml(self):
        self.assertEqual(parse_bing_rss("not xml at all", limit=5), [])
        self.assertEqual(parse_bing_rss("", limit=5), [])

    def test_parse_bing_results(self):
        results = parse_bing_results(HTML_SAMPLE, limit=5)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "Example One")
        self.assertEqual(results[0].url, "https://example.com/one")
        self.assertEqual(results[0].snippet, "First result snippet.")
        self.assertEqual(results[1].url, "https://example.org/two")
        self.assertEqual(results[1].snippet, "Second result snippet.")


class FakeBingProvider(BingProvider):
    def __init__(self, config, html_fetcher, *, rss_text=None, rss_error=None):
        super().__init__(config, html_fetcher)
        self._rss_text = rss_text
        self._rss_error = rss_error
        self.rss_urls: list[str] = []

    async def _fetch_rss(self, url: str) -> str:
        self.rss_urls.append(url)
        if self._rss_error is not None:
            raise self._rss_error
        return self._rss_text or ""


class BingProviderSearchTests(unittest.IsolatedAsyncioTestCase):
    def _config(self) -> SearchConfig:
        return SearchConfig(
            search_engine="bing_rss",
            search_url_template="https://evil.example/{query}",
            result_count=5,
        )

    async def test_rss_success_skips_html_fallback(self):
        html_calls: list[str] = []

        async def html_fetcher(url: str) -> str:
            html_calls.append(url)
            return HTML_SAMPLE

        provider = FakeBingProvider(self._config(), html_fetcher, rss_text=RSS_SAMPLE)
        response = await provider.search("test query")

        self.assertEqual(len(response.results), 2)
        self.assertEqual(response.results[0].url, "https://example.com/one")
        self.assertEqual(html_calls, [])
        self.assertIn("format=rss", response.search_url)

    async def test_rss_error_falls_back_to_html(self):
        async def html_fetcher(url: str) -> str:
            return HTML_SAMPLE

        provider = FakeBingProvider(
            self._config(), html_fetcher, rss_error=ObscuraError("Bing RSS HTTP 403")
        )
        response = await provider.search("test query")

        self.assertEqual(len(response.results), 2)
        self.assertIn("format=rss", provider.rss_urls[0])
        self.assertNotIn("format=rss", response.search_url)
        self.assertIn("降级", response.warning)

    async def test_rss_empty_falls_back_to_html(self):
        async def html_fetcher(url: str) -> str:
            return HTML_SAMPLE

        provider = FakeBingProvider(self._config(), html_fetcher, rss_text="<rss></rss>")
        response = await provider.search("test query")

        self.assertEqual(len(response.results), 2)
        self.assertEqual(response.warning, "")

    async def test_both_paths_fail_raise(self):
        async def html_fetcher(url: str) -> str:
            raise ObscuraError("browser boom")

        provider = FakeBingProvider(
            self._config(), html_fetcher, rss_error=ObscuraError("Bing RSS HTTP 403")
        )

        with self.assertRaises(ObscuraError) as ctx:
            await provider.search("test query")
        self.assertIn("rss=", str(ctx.exception))
        self.assertIn("html=", str(ctx.exception))

    async def test_both_paths_empty_return_warning(self):
        async def html_fetcher(url: str) -> str:
            return "<html><body></body></html>"

        provider = FakeBingProvider(self._config(), html_fetcher, rss_text="<rss></rss>")
        response = await provider.search("test query")

        self.assertEqual(response.results, [])
        self.assertIn("没有解析到结果", response.warning)

    async def test_custom_url_template_is_ignored(self):
        requested: list[str] = []

        async def html_fetcher(url: str) -> str:
            requested.append(url)
            return HTML_SAMPLE

        provider = FakeBingProvider(self._config(), html_fetcher, rss_text=RSS_SAMPLE)
        await provider.search("test query")

        for url in provider.rss_urls + requested:
            self.assertTrue(url.startswith("https://www.bing.com/"), url)


if __name__ == "__main__":
    unittest.main()
