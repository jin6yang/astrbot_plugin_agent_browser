from __future__ import annotations

import asyncio
import html
import ipaddress
import os
import re
import shutil
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, quote_plus, unquote, urlparse


DEFAULT_SEARCH_URL_TEMPLATE = "https://html.duckduckgo.com/html/?q={query}"
DEFAULT_FORCE_PREFIXES = ["搜索", "search"]


class ObscuraError(RuntimeError):
    """Raised when Obscura cannot complete a browser operation."""


@dataclass(slots=True)
class SearchConfig:
    enabled: bool = True
    enable_llm_tool: bool = True
    obscura_path: str = ""
    summary_provider_id: str = ""
    search_engine: str = "duckduckgo_html"
    search_url_template: str = DEFAULT_SEARCH_URL_TEMPLATE
    result_count: int = 5
    fetch_top_pages: int = 3
    timeout_seconds: int = 20
    max_page_chars: int = 4000
    force_prefixes: list[str] = field(default_factory=lambda: DEFAULT_FORCE_PREFIXES.copy())
    proxy: str = ""
    user_agent: str = ""
    stealth: bool = False
    allow_private_urls: bool = False


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    content: str = ""
    error: str = ""


@dataclass(slots=True)
class SearchResponse:
    query: str
    search_url: str
    results: list[SearchResult]
    warning: str = ""

    def to_markdown(self, *, include_content: bool = True) -> str:
        lines = [f"Query: {self.query}", f"Search URL: {self.search_url}"]
        if self.warning:
            lines.append(f"Warning: {self.warning}")
        if not self.results:
            lines.append("No results found.")
            return "\n".join(lines)

        lines.append("")
        for index, result in enumerate(self.results, start=1):
            lines.append(f"[{index}] {result.title}")
            lines.append(f"URL: {result.url}")
            if result.snippet:
                lines.append(f"Snippet: {result.snippet}")
            if include_content and result.content:
                lines.append("Content excerpt:")
                lines.append(result.content)
            if result.error:
                lines.append(f"Fetch error: {result.error}")
            lines.append("")
        return "\n".join(lines).strip()


def config_from_mapping(config: Mapping[str, Any] | None) -> SearchConfig:
    raw: Mapping[str, Any] = config or {}
    force_prefixes = raw.get("force_prefixes", DEFAULT_FORCE_PREFIXES)
    if not isinstance(force_prefixes, list):
        force_prefixes = DEFAULT_FORCE_PREFIXES

    return SearchConfig(
        enabled=_as_bool(raw.get("enabled", True)),
        enable_llm_tool=_as_bool(raw.get("enable_llm_tool", True)),
        obscura_path=str(raw.get("obscura_path", "") or "").strip(),
        summary_provider_id=str(raw.get("summary_provider_id", "") or "").strip(),
        search_engine=str(raw.get("search_engine", "duckduckgo_html") or "duckduckgo_html").strip(),
        search_url_template=str(raw.get("search_url_template", DEFAULT_SEARCH_URL_TEMPLATE) or DEFAULT_SEARCH_URL_TEMPLATE).strip(),
        result_count=max(1, _as_int(raw.get("result_count", 5), 5)),
        fetch_top_pages=max(0, _as_int(raw.get("fetch_top_pages", 3), 3)),
        timeout_seconds=max(1, _as_int(raw.get("timeout_seconds", 20), 20)),
        max_page_chars=max(500, _as_int(raw.get("max_page_chars", 4000), 4000)),
        force_prefixes=[str(prefix).strip() for prefix in force_prefixes if str(prefix).strip()],
        proxy=str(raw.get("proxy", "") or "").strip(),
        user_agent=str(raw.get("user_agent", "") or "").strip(),
        stealth=_as_bool(raw.get("stealth", False)),
        allow_private_urls=_as_bool(raw.get("allow_private_urls", False)),
    )


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def resolve_obscura_path(configured_path: str = "", *, base_dir: str | Path | None = None) -> str | None:
    root = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent
    if configured_path:
        expanded = Path(os.path.expandvars(os.path.expanduser(configured_path)))
        if expanded.is_file():
            return str(expanded)
        if not expanded.is_absolute():
            plugin_relative = root / expanded
            if plugin_relative.is_file():
                return str(plugin_relative)
        found = shutil.which(configured_path)
        if found:
            return found
        return None

    local_windows_binary = root / "obscura-x86_64-windows" / "obscura.exe"
    if local_windows_binary.is_file():
        return str(local_windows_binary)

    return shutil.which("obscura") or shutil.which("obscura.exe")


def build_search_url(template: str, query: str) -> str:
    encoded = quote_plus(query.strip())
    raw = query.strip()
    return template.replace("{query}", encoded).replace("{query_encoded}", encoded).replace("{raw_query}", raw)


def extract_forced_query(message: str, prefixes: Sequence[str], *, include_slash_commands: bool = False) -> str | None:
    text = normalize_space(message)
    if not text:
        return None

    for prefix in prefixes:
        prefix = prefix.strip()
        if not prefix:
            continue
        candidates = [prefix]
        if include_slash_commands:
            candidates.extend([f"/{prefix}", f"!{prefix}"])
        for candidate in candidates:
            if text == candidate:
                return ""
            lower_text = text.lower()
            lower_candidate = candidate.lower()
            separators = (" ", ":", "：")
            for separator in separators:
                token = f"{lower_candidate}{separator}"
                if lower_text.startswith(token):
                    return text[len(candidate) + len(separator) :].strip()
    return None


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def truncate_text(value: str, limit: int) -> str:
    value = clean_text(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 20)].rstrip() + "\n...[truncated]"


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


def is_url_allowed(url: str, *, allow_private_urls: bool = False) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    host = hostname.strip().lower().rstrip(".")
    if not allow_private_urls and (host == "localhost" or host.endswith(".localhost")):
        return False

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True

    if allow_private_urls:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


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


def parse_duckduckgo_results(html_text: str, *, limit: int, allow_private_urls: bool = False) -> list[SearchResult]:
    parser = DuckDuckGoHTMLResultParser()
    parser.feed(html_text or "")
    parser.close()

    results: list[SearchResult] = []
    seen: set[str] = set()
    for result in parser.results:
        if result.url in seen:
            continue
        seen.add(result.url)
        if not is_url_allowed(result.url, allow_private_urls=allow_private_urls):
            continue
        results.append(result)
        if len(results) >= limit:
            break
    return results


class ObscuraSearchService:
    def __init__(self, config: SearchConfig, *, base_dir: str | Path | None = None) -> None:
        self.config = config
        self.base_dir = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent

    async def search(self, query: str, *, num_results: int | None = None) -> SearchResponse:
        query = normalize_space(query)
        if not query:
            raise ObscuraError("搜索关键词不能为空。")
        if not self.config.enabled:
            raise ObscuraError("Obscura 搜索插件已在配置中禁用。")

        limit = max(1, min(num_results or self.config.result_count, self.config.result_count))
        search_url = build_search_url(self.config.search_url_template, query)
        if not is_url_allowed(search_url, allow_private_urls=self.config.allow_private_urls):
            raise ObscuraError(f"搜索 URL 被安全策略拦截：{search_url}")

        search_html = await self.fetch(search_url, dump="html")
        results = parse_duckduckgo_results(
            search_html,
            limit=limit,
            allow_private_urls=self.config.allow_private_urls,
        )
        if not results:
            return SearchResponse(query=query, search_url=search_url, results=[], warning="搜索页没有解析到结果。")

        fetch_count = min(self.config.fetch_top_pages, len(results))
        if fetch_count <= 0:
            return SearchResponse(query=query, search_url=search_url, results=results)

        tasks = [self._fetch_result_content(result) for result in results[:fetch_count]]
        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        for result, fetched_content in zip(results[:fetch_count], fetched, strict=False):
            if isinstance(fetched_content, Exception):
                result.error = str(fetched_content)
            else:
                result.content = fetched_content

        return SearchResponse(query=query, search_url=search_url, results=results)

    async def _fetch_result_content(self, result: SearchResult) -> str:
        if not is_url_allowed(result.url, allow_private_urls=self.config.allow_private_urls):
            raise ObscuraError(f"结果 URL 被安全策略拦截：{result.url}")
        text = await self.fetch(result.url, dump="text")
        return truncate_text(text, self.config.max_page_chars)

    async def fetch(self, url: str, *, dump: str = "text") -> str:
        obscura_path = resolve_obscura_path(self.config.obscura_path, base_dir=self.base_dir)
        if not obscura_path:
            raise ObscuraError("未找到 Obscura 可执行文件。请在插件配置中设置 obscura_path，或将 obscura 加入 PATH。")
        if not is_url_allowed(url, allow_private_urls=self.config.allow_private_urls):
            raise ObscuraError(f"URL 被安全策略拦截：{url}")

        args = [
            obscura_path,
            "fetch",
            url,
            "--dump",
            dump,
            "--quiet",
            "--timeout",
            str(self.config.timeout_seconds),
            "--wait-until",
            "load",
        ]
        if self.config.proxy:
            args.extend(["--proxy", self.config.proxy])
        if self.config.user_agent:
            args.extend(["--user-agent", self.config.user_agent])
        if self.config.stealth:
            args.append("--stealth")

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ObscuraError(f"未找到 Obscura 可执行文件：{obscura_path}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.timeout_seconds + 5,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise ObscuraError(f"Obscura 抓取超时：{url}") from exc

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            detail = truncate_text(stderr_text or stdout_text, 800)
            raise ObscuraError(f"Obscura 抓取失败（exit {proc.returncode}）：{detail}")
        return stdout_text
