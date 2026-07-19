import asyncio
import html
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Awaitable, Callable, Sequence
from urllib.parse import quote_plus, urlparse

from ..models import SearchResult, SearchResponse, SearchConfig, ObscuraError

BING_RSS_URL_TEMPLATE = "https://www.bing.com/search?q={query}&format=rss"
BING_HTML_URL_TEMPLATE = "https://www.bing.com/search?q={query}"
BING_BASE_URL = "https://www.bing.com"
DEFAULT_HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def build_bing_url(template: str, query: str) -> str:
    encoded = quote_plus(query.strip())
    raw = query.strip()
    return template.replace("{query}", encoded).replace("{query_encoded}", encoded).replace("{raw_query}", raw)

def parse_bing_rss(xml_text: str, *, limit: int) -> list[SearchResult]:
    try:
        root = ET.fromstring(xml_text or "")
    except ET.ParseError:
        return []

    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in root.iter("item"):
        title = clean_text(item.findtext("title") or "")
        url = (item.findtext("link") or "").strip()
        if not title or not url or url in seen:
            continue
        seen.add(url)
        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=clean_text(item.findtext("description") or ""),
            )
        )
        if len(results) >= limit:
            break
    return results

def normalize_bing_url(href: str) -> str:
    href = html.unescape((href or "").strip())
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return BING_BASE_URL + href
    return href

class BingHTMLResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._current: dict[str, str] | None = None
        self._result_li_depth = 0
        self._in_h2 = False
        self._capture_title = False
        self._capture_snippet = False
        self._caption_depth = 0
        self._title_chunks: list[str] = []
        self._snippet_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())

        if tag == "li" and "b_algo" in classes:
            self._flush_current()
            self._current = {"title": "", "url": "", "snippet": ""}
            self._result_li_depth = 1
            return

        if self._current is None:
            return

        if tag == "li" and self._result_li_depth:
            self._result_li_depth += 1
            return

        if tag == "h2":
            self._in_h2 = True
            return

        if tag == "a" and self._in_h2 and not self._current["url"]:
            url = normalize_bing_url(attrs_map.get("href", ""))
            if url:
                self._current["url"] = url
                self._capture_title = True
                self._title_chunks = []
            return

        if "b_caption" in classes:
            self._caption_depth += 1
            return

        if tag == "p" and not self._current["snippet"]:
            class_attr = attrs_map.get("class", "")
            if self._caption_depth or class_attr.startswith("b_lineclamp"):
                self._capture_snippet = True
                self._snippet_chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title_chunks.append(data)
        if self._capture_snippet:
            self._snippet_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return

        if tag == "a" and self._capture_title:
            self._current["title"] = clean_text("".join(self._title_chunks))
            self._capture_title = False
            self._title_chunks = []
            return

        if tag == "h2":
            self._in_h2 = False
            return

        if tag == "p" and self._capture_snippet:
            self._current["snippet"] = clean_text("".join(self._snippet_chunks))
            self._capture_snippet = False
            self._snippet_chunks = []
            return

        if self._caption_depth and tag in {"div", "td", "section"}:
            self._caption_depth -= 1
            return

        if tag == "li" and self._result_li_depth:
            self._result_li_depth -= 1
            if self._result_li_depth == 0:
                self._flush_current()

    def close(self) -> None:
        super().close()
        self._flush_current()

    def _flush_current(self) -> None:
        if not self._current:
            return
        title = clean_text(self._current.get("title", ""))
        url = self._current.get("url", "").strip()
        if title and url:
            self.results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=clean_text(self._current.get("snippet", "")),
                )
            )
        self._current = None
        self._result_li_depth = 0
        self._in_h2 = False
        self._capture_title = False
        self._capture_snippet = False
        self._caption_depth = 0
        self._title_chunks = []
        self._snippet_chunks = []

def parse_bing_results(html_text: str, *, limit: int) -> list[SearchResult]:
    parser = BingHTMLResultParser()
    parser.feed(html_text or "")
    parser.close()

    results: list[SearchResult] = []
    seen: set[str] = set()
    for result in parser.results:
        if result.url in seen:
            continue
        seen.add(result.url)
        results.append(result)
        if len(results) >= limit:
            break
    return results

class BingProvider:
    def __init__(self, config: SearchConfig, html_fetcher: Callable[[str], Awaitable[str]]) -> None:
        self.config = config
        self.html_fetcher = html_fetcher

    async def _fetch_rss(self, url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.config.user_agent or DEFAULT_HTTP_USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
        )
        proxy = self.config.proxy

        def _do_request() -> str:
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                )
            else:
                opener = urllib.request.build_opener()
            try:
                with opener.open(request, timeout=self.config.timeout_seconds) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                raise ObscuraError(f"Bing RSS HTTP {exc.code}") from exc
            except Exception as exc:
                raise ObscuraError(f"Bing RSS request failed: {exc}") from exc

        return await asyncio.to_thread(_do_request)

    async def search(self, query: str, *, num_results: int | None = None) -> SearchResponse:
        limit = max(1, min(num_results or self.config.result_count, self.config.result_count))
        query = query.strip()
        rss_url = build_bing_url(BING_RSS_URL_TEMPLATE, query)
        html_url = build_bing_url(BING_HTML_URL_TEMPLATE, query)

        rss_error: Exception | None = None
        try:
            rss_text = await self._fetch_rss(rss_url)
            results = parse_bing_rss(rss_text, limit=limit)
            if results:
                return SearchResponse(query=query, search_url=rss_url, results=results)
        except Exception as exc:
            rss_error = exc

        warning = ""
        if rss_error is not None:
            warning = f"Bing RSS 不可用（{rss_error}），已降级为浏览器渲染抓取。"

        try:
            html_text = await self.html_fetcher(html_url)
            results = parse_bing_results(html_text, limit=limit)
        except Exception as html_exc:
            if rss_error is not None:
                raise ObscuraError(
                    f"Bing search failed: rss={rss_error}; html={html_exc}"
                ) from html_exc
            return SearchResponse(
                query=query,
                search_url=rss_url,
                results=[],
                warning=f"搜索页没有解析到结果。（浏览器抓取失败：{html_exc}）",
            )

        if not results:
            empty_warning = "搜索页没有解析到结果。"
            if warning:
                empty_warning = f"{warning}但搜索页没有解析到结果。"
            return SearchResponse(
                query=query,
                search_url=html_url,
                results=[],
                warning=empty_warning,
            )
        return SearchResponse(query=query, search_url=html_url, results=results, warning=warning)

    async def open_urls(self, urls: Sequence[str], *, question: str = "", warning: str = "") -> SearchResponse:
        results = [
            SearchResult(
                title=urlparse(url).netloc or url,
                url=url,
                snippet="Opened directly from user-provided URL.",
            )
            for url in urls
        ]
        return SearchResponse(
            query=question or " ".join(urls),
            search_url="",
            results=results,
            mode="open_urls",
            warning=warning,
        )
