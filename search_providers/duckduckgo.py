import re
import html
from html.parser import HTMLParser
from typing import Awaitable, Callable, Sequence
from urllib.parse import parse_qs, unquote, urlparse

from ..models import SearchResult, SearchResponse, SearchConfig, ObscuraError

URL_TRAILING_CHARS = ".,;:!?)]}>，。！？；：、）】》」』”’"

def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def decode_duckduckgo_url(href: str) -> str:
    href = html.unescape((href or "").strip())
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://duckduckgo.com" + href

    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg:
            return unquote(uddg[0])
    return href

class DuckDuckGoHTMLResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._current: dict[str, str] | None = None
        self._capture_title = False
        self._capture_snippet = False
        self._title_chunks: list[str] = []
        self._snippet_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())

        if tag == "a" and "result__a" in classes:
            self._flush_current()
            self._current = {
                "title": "",
                "url": decode_duckduckgo_url(attrs_map.get("href", "")),
                "snippet": "",
            }
            self._capture_title = True
            self._title_chunks = []
            return

        if self._current is not None and ("result__snippet" in classes or "result__snippet" in attrs_map.get("class", "")):
            self._capture_snippet = True
            self._snippet_chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title_chunks.append(data)
        if self._capture_snippet:
            self._snippet_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title and self._current is not None:
            self._current["title"] = clean_text("".join(self._title_chunks))
            self._capture_title = False
            self._title_chunks = []
            return

        if self._capture_snippet and tag in {"a", "div", "span"} and self._current is not None:
            self._current["snippet"] = clean_text("".join(self._snippet_chunks))
            self._capture_snippet = False
            self._snippet_chunks = []
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

def parse_duckduckgo_results(html_text: str, *, limit: int) -> list[SearchResult]:
    parser = DuckDuckGoHTMLResultParser()
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

class DuckDuckGoProvider:
    def __init__(self, config: SearchConfig, html_fetcher: Callable[[str], Awaitable[str]]) -> None:
        self.config = config
        self.html_fetcher = html_fetcher

    async def search(self, query: str, *, num_results: int | None = None) -> SearchResponse:
        limit = max(1, min(num_results or self.config.result_count, self.config.result_count))
        from urllib.parse import quote_plus
        encoded = quote_plus(query.strip())
        raw = query.strip()
        search_url = self.config.search_url_template.replace("{query}", encoded).replace("{query_encoded}", encoded).replace("{raw_query}", raw)
        
        try:
            search_html = await self.html_fetcher(search_url)
        except Exception as exc:
            raise ObscuraError(f"DuckDuckGo search failed to fetch: {exc}") from exc

        results = parse_duckduckgo_results(search_html, limit=limit)
        if not results:
            return SearchResponse(query=query, search_url=search_url, results=[], warning="搜索页没有解析到结果。")

        return SearchResponse(query=query, search_url=search_url, results=results)

    async def open_urls(self, urls: Sequence[str], *, question: str = "", warning: str = "") -> SearchResponse:
        from urllib.parse import urlparse
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
