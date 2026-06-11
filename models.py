from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

DEFAULT_SEARCH_URL_TEMPLATE = "https://html.duckduckgo.com/html/?q={query}"
DEFAULT_FORCE_PREFIXES = ["搜索", "search"]
DEFAULT_SUMMARY_PROMPT_FILE = "prompts/summary.md"

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
    summary_prompt_template: str = ""
    summary_prompt_file: str = DEFAULT_SUMMARY_PROMPT_FILE
    forced_evidence_prompt_template: str = ""
    auto_search_policy: str = "tool"
    max_urls_per_request: int = 3
    summary_focus: str = "auto"
    enable_media_extraction: bool = True
    media_extract_mode: str = "metadata_only"
    max_images_per_page: int = 5
    image_caption_provider_id: str = ""
    search_engine: str = "duckduckgo_html"
    anysearch_api_key: str = ""
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
class ForcedTask:
    kind: str
    query: str
    urls: list[str] = field(default_factory=list)

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
    mode: str = "search"
    warning: str = ""

    def to_markdown(self, *, include_content: bool = True) -> str:
        if self.mode == "open_urls":
            lines = ["Task: open URLs", f"Question: {self.query}"]
        else:
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
