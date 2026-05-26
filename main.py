from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.star.filter.command import GreedyStr

try:
    from .obscura_service import (
        ObscuraError,
        ObscuraSearchService,
        SearchConfig,
        SearchResponse,
        config_from_mapping,
        extract_forced_query,
        resolve_summary_prompt_template,
    )
except ImportError:
    from obscura_service import (
        ObscuraError,
        ObscuraSearchService,
        SearchConfig,
        SearchResponse,
        config_from_mapping,
        extract_forced_query,
        resolve_summary_prompt_template,
    )


@dataclass
class ObscuraWebSearchTool(FunctionTool[AstrAgentContext]):
    name: str = "obscura_web_search"
    description: str = (
        "Search the web with the local Obscura headless browser and return source-grounded "
        "evidence. Use this for recent, factual, source-dependent, or uncertain questions."
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The web search query.",
                },
                "num_results": {
                    "type": "number",
                    "description": "Optional maximum number of search results to return.",
                },
            },
            "required": ["query"],
        }
    )
    service: Any = Field(default=None, exclude=True)

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> ToolExecResult:
        if self.service is None:
            return "error: Obscura search service is not initialized."

        query = str(kwargs.get("query", "") or "").strip()
        num_results = kwargs.get("num_results")
        try:
            parsed_num_results = int(num_results) if num_results is not None else None
        except (TypeError, ValueError):
            parsed_num_results = None

        try:
            response = await self.service.search(query, num_results=parsed_num_results)
        except ObscuraError as exc:
            return f"error: {exc}"
        except Exception as exc:  # noqa: BLE001 - tool calls must not crash the agent loop
            logger.error(f"Obscura web search tool failed: {exc}", exc_info=True)
            return f"error: Obscura web search failed: {exc}"

        await self._caption_images_from_tool_context(context, response)
        return response.to_markdown(include_content=True)

    async def _caption_images_from_tool_context(
        self,
        context: ContextWrapper[AstrAgentContext],
        response: SearchResponse,
    ) -> None:
        config = getattr(self.service, "config", None)
        if (
            config is None
            or not config.enable_image_caption
            or config.media_extract_mode != "images"
            or not config.image_caption_provider_id
        ):
            return
        try:
            astr_context = context.context.context
        except AttributeError:
            logger.warning("Obscura tool could not access AstrBot context for image captioning.")
            return

        for result in response.results:
            for media in result.page.media:
                if media.visual_description or media.error:
                    continue
                try:
                    llm_resp = await astr_context.llm_generate(
                        chat_provider_id=config.image_caption_provider_id,
                        prompt=(
                            "请用中文简洁描述这张网页图片的可见内容。"
                            "如果图片无法访问或无法识别，请说明无法判断。"
                        ),
                        image_urls=[media.url],
                    )
                    media.visual_description = (getattr(llm_resp, "completion_text", "") or "").strip()
                except Exception as exc:  # noqa: BLE001 - optional tool evidence enrichment
                    media.error = str(exc)
                    logger.warning(f"Failed to caption image in Obscura tool call: {exc}")


class ObscuraAgentBrowserPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.search_config: SearchConfig = config_from_mapping(self.config)
        self.plugin_dir = Path(__file__).resolve().parent
        self.search_service = ObscuraSearchService(self.search_config, base_dir=self.plugin_dir)
        self.summary_prompt_template = resolve_summary_prompt_template(
            self.search_config,
            base_dir=self.plugin_dir,
        )

        if self.search_config.enable_llm_tool:
            try:
                self.context.add_llm_tools(ObscuraWebSearchTool(service=self.search_service))
                logger.info("Obscura LLM tool registered: obscura_web_search")
            except Exception as exc:  # noqa: BLE001 - plugin should still load for explicit commands
                logger.error(f"Failed to register Obscura LLM tool: {exc}", exc_info=True)

    @filter.command("search", alias={"搜索"}, priority=10)
    async def search_command(self, event: AstrMessageEvent, query: GreedyStr):
        """强制使用 Obscura 搜索并总结。用法：/搜索 关键词 或 /search query"""
        if not self.search_config.enable_force_commands:
            yield event.plain_result("Obscura 显式搜索命令当前已在配置中禁用。")
            event.stop_event()
            return
        async for result in self._run_forced_search(event, str(query)):
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def search_prefix_listener(self, event: AstrMessageEvent):
        """监听非斜杠搜索前缀，例如：搜索 AstrBot 插件开发"""
        query = None
        if self.search_config.enable_force_prefixes:
            query = extract_forced_query(
                event.message_str,
                self.search_config.force_prefixes,
                include_slash_commands=False,
            )
        if query is None and self.search_config.auto_search_policy == "always":
            message = (event.message_str or "").strip()
            if message and not message.startswith(("/", "!")):
                query = message
        if query is None:
            return
        async for result in self._run_forced_search(event, query):
            yield result

    async def _run_forced_search(self, event: AstrMessageEvent, query: str):
        query = (query or "").strip()
        if not query:
            yield event.plain_result("请提供搜索关键词，例如：/搜索 AstrBot 插件开发")
            event.stop_event()
            return

        if not self.search_config.enabled:
            yield event.plain_result("Obscura 搜索插件当前已禁用。")
            event.stop_event()
            return

        try:
            logger.info(f"Obscura forced search query: {query}")
            search_response = await self.search_service.search(query)
            await self._caption_images(search_response)
            answer = await self._summarize(event, query, search_response)
        except ObscuraError as exc:
            logger.warning(f"Obscura forced search failed: {exc}")
            answer = f"搜索失败：{exc}"
        except Exception as exc:  # noqa: BLE001 - keep plugin failures visible but contained
            logger.error(f"Unexpected Obscura forced search failure: {exc}", exc_info=True)
            answer = f"搜索失败：{exc}"

        yield event.plain_result(answer)
        event.stop_event()

    async def _summarize(
        self,
        event: AstrMessageEvent,
        query: str,
        search_response: SearchResponse,
    ) -> str:
        evidence = search_response.to_markdown(include_content=True)
        if not search_response.results:
            return f"没有解析到可用搜索结果。\n\n{evidence}"

        provider_id = self.search_config.summary_provider_id
        if not provider_id:
            provider_id = await self.context.get_current_chat_provider_id(event.unified_msg_origin)

        try:
            prompt = self.summary_prompt_template.format(
                query=query,
                evidence=evidence,
                summary_focus=self.search_config.summary_focus,
            )
        except (KeyError, ValueError) as exc:
            logger.warning(f"Invalid Obscura summary prompt template, using raw evidence fallback: {exc}")
            return "搜索完成，但摘要提示词模板格式无效。以下是原始搜索材料：\n\n" + evidence
        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
            )
        except Exception as exc:  # noqa: BLE001 - fall back to raw evidence
            logger.error(f"Failed to summarize Obscura search results: {exc}", exc_info=True)
            return "搜索完成，但摘要模型调用失败。以下是原始搜索材料：\n\n" + evidence

        completion = getattr(llm_resp, "completion_text", "") or ""
        if completion.strip():
            return completion.strip()
        return "搜索完成，但摘要模型没有返回文本。以下是原始搜索材料：\n\n" + evidence

    async def _caption_images(self, search_response: SearchResponse) -> None:
        if (
            not self.search_config.enable_image_caption
            or self.search_config.media_extract_mode != "images"
        ):
            return
        provider_id = self.search_config.image_caption_provider_id
        if not provider_id:
            logger.warning("enable_image_caption is true but image_caption_provider_id is empty.")
            return

        for result in search_response.results:
            for media in result.page.media:
                if media.visual_description or media.error:
                    continue
                try:
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=(
                            "请用中文简洁描述这张网页图片的可见内容。"
                            "如果图片无法访问或无法识别，请说明无法判断。"
                        ),
                        image_urls=[media.url],
                    )
                    media.visual_description = (getattr(llm_resp, "completion_text", "") or "").strip()
                except Exception as exc:  # noqa: BLE001 - image caption is optional evidence enrichment
                    media.error = str(exc)
                    logger.warning(f"Failed to caption image with Obscura evidence: {exc}")

    async def terminate(self):
        """AstrBot unload hook."""
        logger.info("Obscura Agent Browser plugin terminated.")
