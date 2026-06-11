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
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

from .models import (
    DEFAULT_FORCE_PREFIXES,
    DEFAULT_SEARCH_URL_TEMPLATE,
    DEFAULT_SUMMARY_PROMPT_FILE,
    ForcedTask,
    MediaItem,
    ObscuraError,
    PageEvidence,
    SearchConfig,
    SearchResponse,
    SearchResult,
)
from .search_providers import (
    SearchProvider, 
    DuckDuckGoProvider, 
    AnySearchProvider,
    ExaProvider,
    ParallelProvider,
    PerplexityProvider,
    TavilyProvider
)


def config_from_mapping(config: Mapping[str, Any] | None) -> SearchConfig:
    raw: Mapping[str, Any] = config or {}
    general = _section(raw, "config_general")
    force_trigger = _section(raw, "config_force_trigger")
    main_bot = _section(raw, "config_main_bot")
    direct_reply = _section(raw, "config_direct_reply")
    search = _section(raw, "config_search")
    media = _section(raw, "config_media")
    advanced = _section(raw, "config_advanced")

    force_prefixes = general.get("force_prefixes", DEFAULT_FORCE_PREFIXES)
    if not isinstance(force_prefixes, list):
        force_prefixes = DEFAULT_FORCE_PREFIXES

    return SearchConfig(
        enabled=_as_bool(general.get("enabled", True)),
        enable_force_commands=_as_bool(general.get("enable_force_commands", True)),
        enable_force_prefixes=_as_bool(general.get("enable_force_prefixes", True)),
        enable_llm_tool=_as_bool(general.get("enable_llm_tool", True)),
        force_trigger_mode=_choice(force_trigger.get("force_trigger_mode", "main_bot"), {"main_bot", "direct_reply"}, "main_bot"),
        obscura_path=str(advanced.get("obscura_path", "") or "").strip(),
        summary_provider_id=str(direct_reply.get("summary_provider_id", "") or "").strip(),
        summary_prompt_source=_choice(direct_reply.get("summary_prompt_source", "file"), {"file", "config"}, "file"),
        summary_prompt_template=str(direct_reply.get("summary_prompt_template", "")),
        summary_prompt_file=str(direct_reply.get("summary_prompt_file", DEFAULT_SUMMARY_PROMPT_FILE) or DEFAULT_SUMMARY_PROMPT_FILE).strip(),
        forced_evidence_prompt_template=str(main_bot.get("forced_evidence_prompt_template", "")),
        auto_search_policy=_choice(force_trigger.get("auto_search_policy", "tool"), {"tool", "always"}, "tool"),
        max_urls_per_request=max(1, _as_int(force_trigger.get("max_urls_per_request", 3), 3)),
        summary_focus=_choice(search.get("summary_focus", "auto"), {"auto", "content", "visual_design", "site_overview"}, "auto"),
        enable_media_extraction=_as_bool(media.get("enable_media_extraction", True)),
        media_extract_mode=_choice(media.get("media_extract_mode", "metadata_only"), {"metadata_only", "images"}, "metadata_only"),
        max_images_per_page=max(0, _as_int(media.get("max_images_per_page", 5), 5)),
        image_caption_provider_id=str(media.get("image_caption_provider_id", "") or "").strip(),
        search_engine=str(search.get("search_engine", "duckduckgo_html") or "duckduckgo_html").strip(),
        search_api_key=str(search.get("search_api_key", search.get("anysearch_api_key", "")) or "").strip(),
        search_url_template=str(search.get("search_url_template", DEFAULT_SEARCH_URL_TEMPLATE) or DEFAULT_SEARCH_URL_TEMPLATE).strip(),
        result_count=max(1, _as_int(search.get("result_count", 5), 5)),
        fetch_top_pages=max(0, _as_int(search.get("fetch_top_pages", 3), 3)),
        timeout_seconds=max(1, _as_int(search.get("timeout_seconds", 20), 20)),
        max_page_chars=max(500, _as_int(search.get("max_page_chars", 4000), 4000)),
        force_prefixes=[str(prefix).strip() for prefix in force_prefixes if str(prefix).strip()],
        proxy=str(advanced.get("proxy", "") or "").strip(),
        user_agent=str(advanced.get("user_agent", "") or "").strip(),
        stealth=_as_bool(advanced.get("stealth", False)),
        allow_private_urls=_as_bool(advanced.get("allow_private_urls", False)),
    )


def _section(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config.get(key, {})
    return value if isinstance(value, Mapping) else {}


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


def _choice(value: Any, allowed: set[str], default: str) -> str:
    candidate = str(value or default).strip()
    return candidate if candidate in allowed else default


def resolve_summary_prompt_template(config: SearchConfig, *, base_dir: str | Path | None = None) -> str:
    root = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent
    if config.summary_prompt_source == "config":
        template = config.summary_prompt_template
    else:
        prompt_path = Path(os.path.expandvars(os.path.expanduser(config.summary_prompt_file)))
        if not prompt_path.is_absolute():
            prompt_path = root / prompt_path
        try:
            template = prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ObscuraError(f"摘要提示词文件无法读取：{prompt_path}") from exc

    if not is_valid_summary_prompt_template(template):
        raise ObscuraError("摘要提示词必须包含 {query} 和 {evidence}。")
    return template


def is_valid_summary_prompt_template(template: str) -> bool:
    return "{query}" in template and "{evidence}" in template


def is_valid_forced_evidence_template(template: str) -> bool:
    return "{query}" in template and "{evidence}" in template


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

    for local_binary in (root / "obscura" / "obscura.exe", root / "obscura" / "obscura"):
        if local_binary.is_file():
            return str(local_binary)

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


URL_PATTERN = re.compile(r"https?://[^\s<>'\"`]+", flags=re.IGNORECASE)
URL_TRAILING_CHARS = ".,;:!?)]}>，。！？；：、）】》】”’"


def extract_http_urls(text: str, *, limit: int | None = None) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in URL_PATTERN.finditer(text or ""):
        candidate = _clean_url_candidate(match.group(0))
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if candidate in seen:
            continue
        urls.append(candidate)
        seen.add(candidate)
        if limit is not None and len(urls) >= limit:
            break
    return urls


def remove_http_urls(text: str) -> str:
    return normalize_space(URL_PATTERN.sub(" ", text or ""))


def build_forced_task(text: str, *, max_urls: int = 3) -> ForcedTask:
    original = normalize_space(text)
    urls = extract_http_urls(original, limit=max(1, max_urls))
    if urls:
        query = remove_http_urls(original) or original
        return ForcedTask(kind="open_urls", query=query, urls=urls)
    return ForcedTask(kind="search", query=original)


def _clean_url_candidate(value: str) -> str:
    return html.unescape((value or "").strip()).rstrip(URL_TRAILING_CHARS)


def truncate_text(value: str, limit: int) -> str:
    value = clean_text(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 20)].rstrip() + "\n...[truncated]"


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
        import ipaddress
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


class PageEvidenceParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.evidence = PageEvidence()
        self._capture_title = False
        self._capture_heading: str | None = None
        self._capture_nav_link = False
        self._capture_regular_link = False
        self._capture_style = False
        self._capture_figcaption = False
        self._text_chunks: list[str] = []
        self._nav_depth = 0
        self._figure_media_indexes: list[int] | None = None
        self._figcaption_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        self._collect_style_tokens(attrs_map.get("style", ""))

        if tag == "title":
            self._capture_title = True
            self._text_chunks = []
            return

        if tag in {"h1", "h2", "h3"}:
            self._capture_heading = tag
            self._text_chunks = []
            return

        if tag == "nav":
            self._nav_depth += 1

        if tag == "figure":
            self._figure_media_indexes = []

        if tag == "figcaption":
            self._capture_figcaption = True
            self._figcaption_chunks = []

        if tag == "style":
            self._capture_style = True
            self._text_chunks = []
            return

        if tag == "meta":
            self._handle_meta(attrs_map)
            return

        if tag in {"img", "source"}:
            self._handle_media_tag(tag, attrs_map)
            return

        if tag == "a":
            if self._nav_depth:
                self._capture_nav_link = True
            else:
                self._capture_regular_link = True
            self._text_chunks = []

    def handle_data(self, data: str) -> None:
        if (
            self._capture_title
            or self._capture_heading
            or self._capture_nav_link
            or self._capture_regular_link
            or self._capture_style
        ):
            self._text_chunks.append(data)
        if self._capture_figcaption:
            self._figcaption_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._capture_title:
            self.evidence.title = clean_text("".join(self._text_chunks))[:300]
            self._capture_title = False
            self._text_chunks = []
            return

        if tag in {"h1", "h2", "h3"} and self._capture_heading == tag:
            self._append_unique(self.evidence.headings, clean_text("".join(self._text_chunks)), limit=16)
            self._capture_heading = None
            self._text_chunks = []
            return

        if tag == "a" and self._capture_nav_link:
            self._append_unique(self.evidence.nav_items, clean_text("".join(self._text_chunks)), limit=24)
            self._capture_nav_link = False
            self._text_chunks = []
            return

        if tag == "a" and self._capture_regular_link:
            self._append_unique(self.evidence.links, clean_text("".join(self._text_chunks)), limit=24)
            self._capture_regular_link = False
            self._text_chunks = []
            return

        if tag == "style" and self._capture_style:
            self._collect_style_tokens("".join(self._text_chunks))
            self._capture_style = False
            self._text_chunks = []
            return

        if tag == "figcaption" and self._capture_figcaption:
            caption = clean_text("".join(self._figcaption_chunks))
            if caption:
                for media_index in self._figure_media_indexes or []:
                    if media_index < len(self.evidence.media) and not self.evidence.media[media_index].caption:
                        self.evidence.media[media_index].caption = caption[:300]
            self._capture_figcaption = False
            self._figcaption_chunks = []
            return

        if tag == "nav" and self._nav_depth:
            self._nav_depth -= 1

        if tag == "figure":
            self._figure_media_indexes = None

    def _handle_meta(self, attrs_map: Mapping[str, str]) -> None:
        key = (attrs_map.get("property") or attrs_map.get("name") or "").strip().lower()
        content = clean_text(attrs_map.get("content", ""))
        if not key or not content:
            return

        if key == "description":
            self.evidence.description = content[:500]
        elif key in {"og:title", "twitter:title"}:
            self.evidence.og_title = content[:300]
        elif key in {"og:description", "twitter:description"}:
            self.evidence.og_description = content[:500]
        elif key in {"og:image", "og:image:secure_url", "twitter:image", "twitter:image:src"}:
            self._add_media(content, source=key)

    def _handle_media_tag(self, tag: str, attrs_map: Mapping[str, str]) -> None:
        alt = clean_text(attrs_map.get("alt", ""))
        title = clean_text(attrs_map.get("title", ""))
        source = "img" if tag == "img" else "source"
        candidates: list[str] = []
        if attrs_map.get("src"):
            candidates.append(attrs_map["src"])
        if attrs_map.get("data-src"):
            candidates.append(attrs_map["data-src"])
        if attrs_map.get("srcset"):
            candidates.extend(parse_srcset(attrs_map["srcset"]))
        if attrs_map.get("data-srcset"):
            candidates.extend(parse_srcset(attrs_map["data-srcset"]))
        for candidate in candidates:
            self._add_media(candidate, source=source, alt=alt, title=title)

    def _add_media(self, url: str, *, source: str, alt: str = "", title: str = "") -> None:
        resolved = resolve_page_url(self.base_url, url)
        if not resolved:
            return
        if any(item.url == resolved for item in self.evidence.media):
            return
        self.evidence.media.append(
            MediaItem(
                url=resolved,
                source=source,
                alt=alt[:300],
                title=title[:300],
            )
        )
        if self._figure_media_indexes is not None:
            self._figure_media_indexes.append(len(self.evidence.media) - 1)

    def _collect_style_tokens(self, css: str) -> None:
        if not css:
            return
        for color in re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]+\)|hsla?\([^)]+\)", css):
            self._append_unique(self.evidence.colors, clean_text(color), limit=24)
        for font in re.findall(r"font-family\s*:\s*([^;}{]+)", css, flags=re.IGNORECASE):
            self._append_unique(self.evidence.fonts, clean_text(font.strip("\"' ")), limit=16)

    @staticmethod
    def _append_unique(target: list[str], value: str, *, limit: int) -> None:
        value = clean_text(value)
        if not value or value in target:
            return
        if len(target) < limit:
            target.append(value[:300])


def parse_page_evidence(
    html_text: str,
    *,
    base_url: str,
    max_images: int,
    allow_private_urls: bool = False,
    include_images: bool = True,
) -> PageEvidence:
    parser = PageEvidenceParser(base_url)
    parser.feed(html_text or "")
    parser.close()
    evidence = parser.evidence
    evidence.media = [
        item
        for item in evidence.media
        if is_url_allowed(item.url, allow_private_urls=allow_private_urls)
    ][:max_images]
    if not include_images:
        evidence.media = []
    return evidence


def parse_srcset(srcset: str) -> list[str]:
    urls: list[str] = []
    for part in (srcset or "").split(","):
        candidate = part.strip().split()
        if candidate:
            urls.append(candidate[0])
    return urls


def resolve_page_url(base_url: str, url: str) -> str:
    url = html.unescape((url or "").strip())
    if not url or url.startswith("data:") or url.startswith("blob:"):
        return ""
    return urljoin(base_url, url)


class ObscuraSearchService:
    def __init__(self, config: SearchConfig, *, base_dir: str | Path | None = None) -> None:
        self.config = config
        self.base_dir = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent

    def _get_provider(self) -> SearchProvider:
        if self.config.search_engine == "anysearch_api":
            return AnySearchProvider(self.config)
        elif self.config.search_engine == "exa_api":
            return ExaProvider(self.config)
        elif self.config.search_engine == "parallel_api":
            return ParallelProvider(self.config)
        elif self.config.search_engine == "perplexity_api":
            return PerplexityProvider(self.config)
        elif self.config.search_engine == "tavily_api":
            return TavilyProvider(self.config)
        
        async def fetch_html(url: str) -> str:
            return await self.fetch(url, dump="html")
        return DuckDuckGoProvider(self.config, html_fetcher=fetch_html)

    async def _enrich_results(self, results: list[SearchResult]) -> None:
        fetch_count = min(self.config.fetch_top_pages, len(results))
        if fetch_count <= 0:
            return

        tasks = []
        for result in results[:fetch_count]:
            needs_content = not result.content
            needs_evidence = self.config.enable_media_extraction or self.config.summary_focus in {"visual_design", "site_overview"}
            if needs_content or needs_evidence:
                tasks.append(self._fetch_result_evidence(result, needs_content=needs_content, needs_evidence=needs_evidence))

        if not tasks:
            return

        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        # tasks list and results[:fetch_count] might not align if some results don't need enrichment.
        # we iterate again to map back.
        task_idx = 0
        for result in results[:fetch_count]:
            needs_content = not result.content
            needs_evidence = self.config.enable_media_extraction or self.config.summary_focus in {"visual_design", "site_overview"}
            if needs_content or needs_evidence:
                fetched_content = fetched[task_idx]
                task_idx += 1
                if isinstance(fetched_content, Exception):
                    result.error = str(fetched_content)
                else:
                    new_content, new_page = fetched_content
                    if needs_content:
                        result.content = new_content
                    if needs_evidence:
                        result.page = new_page

    async def search(self, query: str, *, num_results: int | None = None) -> SearchResponse:
        query = normalize_space(query)
        if not query:
            raise ObscuraError("搜索关键词不能为空。")
        if not self.config.enabled:
            raise ObscuraError("Obscura 搜索插件已在配置中禁用。")

        provider = self._get_provider()
        response = await provider.search(query, num_results=num_results)

        allowed_results: list[SearchResult] = []
        for result in response.results:
            if is_url_allowed(result.url, allow_private_urls=self.config.allow_private_urls):
                allowed_results.append(result)
            else:
                result.error = f"URL 被安全策略拦截：{result.url}"

        response.results = allowed_results
        await self._enrich_results(response.results)
        return response

    async def open_urls(
        self,
        urls: Sequence[str],
        *,
        question: str = "",
        warning: str = "",
    ) -> SearchResponse:
        if not self.config.enabled:
            raise ObscuraError("Obscura 搜索插件已在配置中禁用。")

        normalized_urls: list[str] = []
        seen: set[str] = set()
        for raw_url in urls:
            url = _clean_url_candidate(str(raw_url or ""))
            if not url or url in seen:
                continue
            normalized_urls.append(url)
            seen.add(url)
            if len(normalized_urls) >= self.config.max_urls_per_request:
                break

        if not normalized_urls:
            raise ObscuraError("没有可打开的 URL。")

        provider = self._get_provider()
        response = await provider.open_urls(normalized_urls, question=question, warning=warning)

        allowed_results: list[SearchResult] = []
        for result in response.results:
            if is_url_allowed(result.url, allow_private_urls=self.config.allow_private_urls):
                allowed_results.append(result)
            else:
                result.error = f"URL 被安全策略拦截：{result.url}"

        response.results = allowed_results
        await self._enrich_results(response.results)
        return response

    async def _fetch_result_evidence(self, result: SearchResult, needs_content: bool, needs_evidence: bool) -> tuple[str, PageEvidence]:
        if not is_url_allowed(result.url, allow_private_urls=self.config.allow_private_urls):
            raise ObscuraError(f"结果 URL 被安全策略拦截：{result.url}")
        
        content = ""
        if needs_content:
            text = await self.fetch(result.url, dump="text")
            content = truncate_text(text, self.config.max_page_chars)
            
        page_evidence = PageEvidence()
        if needs_evidence:
            try:
                html_text = await self.fetch(result.url, dump="html")
                page_evidence = parse_page_evidence(
                    html_text,
                    base_url=result.url,
                    max_images=self.config.max_images_per_page,
                    allow_private_urls=self.config.allow_private_urls,
                    include_images=self.config.enable_media_extraction,
                )
            except ObscuraError as exc:
                page_evidence.description = f"Page metadata extraction failed: {exc}"
        return content, page_evidence

    async def fetch(self, url: str, *, dump: str = "text") -> str:
        obscura_path = resolve_obscura_path(self.config.obscura_path, base_dir=self.base_dir)
        if not obscura_path:
            raise ObscuraError("未找到 Obscura 可执行文件。请在插件目录 obscura/ 中放置 obscura 可执行文件，或在配置中设置 obscura_path。")
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
