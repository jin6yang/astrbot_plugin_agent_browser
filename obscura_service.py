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


DEFAULT_SEARCH_URL_TEMPLATE = "https://html.duckduckgo.com/html/?q={query}"
DEFAULT_FORCE_PREFIXES = ["搜索", "search"]
DEFAULT_SUMMARY_PROMPT_FILE = "prompts/summary.md"
DEFAULT_FORCED_EVIDENCE_PROMPT_TEMPLATE = """用户明确要求执行浏览器搜索。下面是 Obscura 搜索得到的证据，请结合当前人格、上下文和用户原始问题回答。

要求：
1. 优先依据搜索证据回答。
2. 不要把搜索证据之外的推测说成事实。
3. 如证据不足或搜索失败，请按当前对话风格说明。
4. 如果引用来源，可以使用 [1]、[2] 这样的编号。

总结侧重点：{summary_focus}

强制搜索 query：
{query}

搜索证据：
{evidence}
"""
DEFAULT_SUMMARY_PROMPT_TEMPLATE = """你是一个严谨的联网搜索助手。请基于下面的 Obscura 浏览器搜索材料回答用户问题。

要求：
1. 优先使用搜索材料，不要把没有依据的内容说成事实。
2. 结论后用 [1]、[2] 这样的编号标注来源。
3. 如果材料不足，请明确说明不足，并给出已找到的信息。
4. 如果材料包含图片或设计线索，请区分“页面文字/DOM 元数据能确认的内容”和“无法直接确认的视觉细节”。
5. 用用户提问的语言回答，保持简洁但覆盖关键事实。

总结侧重点：{summary_focus}

用户问题：
{query}

搜索材料：
{evidence}
"""


class ObscuraError(RuntimeError):
    """Raised when Obscura cannot complete a browser operation."""


@dataclass(slots=True)
class SearchConfig:
    enabled: bool = True
    enable_force_commands: bool = True
    enable_force_prefixes: bool = True
    enable_llm_tool: bool = True
    force_trigger_mode: str = "main_bot"
    obscura_path: str = ""
    summary_provider_id: str = ""
    summary_prompt_source: str = "file"
    summary_prompt_template: str = DEFAULT_SUMMARY_PROMPT_TEMPLATE
    summary_prompt_file: str = DEFAULT_SUMMARY_PROMPT_FILE
    forced_evidence_prompt_template: str = DEFAULT_FORCED_EVIDENCE_PROMPT_TEMPLATE
    auto_search_policy: str = "tool"
    summary_focus: str = "auto"
    enable_media_extraction: bool = True
    media_extract_mode: str = "metadata_only"
    max_images_per_page: int = 5
    image_caption_provider_id: str = ""
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
class MediaItem:
    url: str
    source: str = ""
    alt: str = ""
    title: str = ""
    caption: str = ""
    visual_description: str = ""
    error: str = ""


@dataclass(slots=True)
class PageEvidence:
    title: str = ""
    description: str = ""
    og_title: str = ""
    og_description: str = ""
    headings: list[str] = field(default_factory=list)
    nav_items: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    media: list[MediaItem] = field(default_factory=list)

    def has_content(self) -> bool:
        return bool(
            self.title
            or self.description
            or self.og_title
            or self.og_description
            or self.headings
            or self.nav_items
            or self.links
            or self.colors
            or self.fonts
            or self.media
        )

    def to_markdown_lines(self) -> list[str]:
        lines: list[str] = []
        if self.title:
            lines.append(f"- Page title: {self.title}")
        if self.description:
            lines.append(f"- Meta description: {self.description}")
        if self.og_title:
            lines.append(f"- OpenGraph title: {self.og_title}")
        if self.og_description:
            lines.append(f"- OpenGraph description: {self.og_description}")
        if self.headings:
            lines.append("- Headings: " + "; ".join(self.headings[:8]))
        if self.nav_items:
            lines.append("- Navigation labels: " + "; ".join(self.nav_items[:10]))
        if self.links:
            lines.append("- Main links: " + "; ".join(self.links[:10]))
        if self.colors:
            lines.append("- CSS color tokens: " + ", ".join(self.colors[:12]))
        if self.fonts:
            lines.append("- CSS font tokens: " + ", ".join(self.fonts[:8]))
        if self.media:
            lines.append("- Media evidence:")
            for index, media in enumerate(self.media[:10], start=1):
                detail = [f"image {index}: {media.url}"]
                if media.source:
                    detail.append(f"source={media.source}")
                if media.alt:
                    detail.append(f"alt={media.alt}")
                if media.title:
                    detail.append(f"title={media.title}")
                if media.caption:
                    detail.append(f"caption={media.caption}")
                if media.visual_description:
                    detail.append(f"visual_caption={media.visual_description}")
                if media.error:
                    detail.append(f"caption_error={media.error}")
                lines.append("  - " + " | ".join(detail))
            if any(not item.visual_description for item in self.media):
                lines.append(
                    "  - Note: image pixels without visual_caption were not analyzed; "
                    "only URL, alt/title text, captions, and page metadata are available."
                )
        return lines


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    content: str = ""
    error: str = ""
    page: PageEvidence = field(default_factory=PageEvidence)


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
            if include_content and result.page.has_content():
                page_lines = result.page.to_markdown_lines()
                if page_lines:
                    lines.append("Page evidence:")
                    lines.extend(page_lines)
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
        enable_force_commands=_as_bool(raw.get("enable_force_commands", True)),
        enable_force_prefixes=_as_bool(raw.get("enable_force_prefixes", True)),
        enable_llm_tool=_as_bool(raw.get("enable_llm_tool", True)),
        force_trigger_mode=_choice(raw.get("force_trigger_mode", "main_bot"), {"main_bot", "direct_reply"}, "main_bot"),
        obscura_path=str(raw.get("obscura_path", "") or "").strip(),
        summary_provider_id=str(raw.get("summary_provider_id", "") or "").strip(),
        summary_prompt_source=_choice(raw.get("summary_prompt_source", "file"), {"file", "config"}, "file"),
        summary_prompt_template=str(raw.get("summary_prompt_template", DEFAULT_SUMMARY_PROMPT_TEMPLATE) or DEFAULT_SUMMARY_PROMPT_TEMPLATE),
        summary_prompt_file=str(raw.get("summary_prompt_file", DEFAULT_SUMMARY_PROMPT_FILE) or DEFAULT_SUMMARY_PROMPT_FILE).strip(),
        forced_evidence_prompt_template=str(raw.get("forced_evidence_prompt_template", DEFAULT_FORCED_EVIDENCE_PROMPT_TEMPLATE) or DEFAULT_FORCED_EVIDENCE_PROMPT_TEMPLATE),
        auto_search_policy=_choice(raw.get("auto_search_policy", "tool"), {"tool", "always"}, "tool"),
        summary_focus=_choice(raw.get("summary_focus", "auto"), {"auto", "content", "visual_design", "site_overview"}, "auto"),
        enable_media_extraction=_as_bool(raw.get("enable_media_extraction", True)),
        media_extract_mode=_choice(raw.get("media_extract_mode", "metadata_only"), {"metadata_only", "images"}, "metadata_only"),
        max_images_per_page=max(0, _as_int(raw.get("max_images_per_page", 5), 5)),
        image_caption_provider_id=str(raw.get("image_caption_provider_id", "") or "").strip(),
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

        tasks = [self._fetch_result_evidence(result) for result in results[:fetch_count]]
        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        for result, fetched_content in zip(results[:fetch_count], fetched, strict=False):
            if isinstance(fetched_content, Exception):
                result.error = str(fetched_content)
            else:
                result.content = fetched_content[0]
                result.page = fetched_content[1]

        return SearchResponse(query=query, search_url=search_url, results=results)

    async def _fetch_result_evidence(self, result: SearchResult) -> tuple[str, PageEvidence]:
        if not is_url_allowed(result.url, allow_private_urls=self.config.allow_private_urls):
            raise ObscuraError(f"结果 URL 被安全策略拦截：{result.url}")
        text = await self.fetch(result.url, dump="text")
        content = truncate_text(text, self.config.max_page_chars)
        page_evidence = PageEvidence()
        should_extract_page_evidence = (
            self.config.enable_media_extraction
            or self.config.summary_focus in {"visual_design", "site_overview"}
        )
        if should_extract_page_evidence:
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
