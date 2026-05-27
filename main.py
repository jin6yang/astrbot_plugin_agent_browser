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
    from astrbot.core.agent.message import TextPart
except ImportError:
    TextPart = None

try:
    from .obscura_service import (
        ObscuraError,
        ObscuraSearchService,
        SearchConfig,
        SearchResponse,
        config_from_mapping,
        extract_forced_query,
        is_valid_forced_evidence_template,
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
        is_valid_forced_evidence_template,
        resolve_summary_prompt_template,
    )

try:
    from astrbot.api.provider import ProviderRequest
except ImportError:
    ProviderRequest = Any


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
            or not config.enable_media_extraction
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
        self._forced_queries: dict[str, str] = {}

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
        async for result in self._handle_forced_trigger(event, str(query)):
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
        async for result in self._handle_forced_trigger(event, query):
            yield result

    @filter.on_llm_request()
    async def inject_forced_search_evidence(self, event: AstrMessageEvent, req: ProviderRequest):
        query = self._consume_forced_query(event)
        if not query:
            return
        if not self.search_config.enabled:
            self._append_extra_user_text(req, "Obscura 搜索插件当前已禁用。")
            return

        try:
            logger.info(f"Obscura forced search evidence query: {query}")
            search_response = await self.search_service.search(query)
            await self._caption_images(search_response)
            evidence = search_response.to_markdown(include_content=True)
            forced_prompt = self._format_forced_evidence_prompt(query, evidence)
        except ObscuraError as exc:
            logger.warning(f"Obscura forced search evidence failed: {exc}")
            forced_prompt = f"Obscura 搜索失败：{exc}"
        except Exception as exc:  # noqa: BLE001 - surface failure to the main model
            logger.error(f"Unexpected Obscura forced search evidence failure: {exc}", exc_info=True)
            forced_prompt = f"Obscura 搜索失败：{exc}"

        self._append_extra_user_text(req, forced_prompt)

    async def _handle_forced_trigger(self, event: AstrMessageEvent, query: str):
        query = (query or "").strip()
        if not query:
            yield event.plain_result("请提供搜索关键词，例如：/搜索 AstrBot 插件开发")
            event.stop_event()
            return

        if not self.search_config.enabled:
            yield event.plain_result("Obscura 搜索插件当前已禁用。")
            event.stop_event()
            return

        if self.search_config.force_trigger_mode == "main_bot":
            self._record_forced_query(event, query)
            return

        async for result in self._run_direct_reply_search(event, query):
            yield result

    async def _run_direct_reply_search(self, event: AstrMessageEvent, query: str):
        query = (query or "").strip()

        try:
            logger.info(f"Obscura direct reply search query: {query}")
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
            summary_prompt_template = resolve_summary_prompt_template(
                self.search_config,
                base_dir=self.plugin_dir,
            )
            prompt = summary_prompt_template.format(
                query=query,
                evidence=evidence,
                summary_focus=self.search_config.summary_focus,
            )
        except ObscuraError as exc:
            logger.error(f"Invalid Obscura summary prompt config: {exc}")
            return f"摘要提示词配置错误：{exc}"
        except (KeyError, ValueError) as exc:
            logger.error(f"Invalid Obscura summary prompt template format: {exc}")
            return f"摘要提示词格式错误：{exc}"
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
            not self.search_config.enable_media_extraction
            or self.search_config.media_extract_mode != "images"
            or not self.search_config.image_caption_provider_id
        ):
            return
        provider_id = self.search_config.image_caption_provider_id

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

    def _record_forced_query(self, event: AstrMessageEvent, query: str) -> None:
        if len(self._forced_queries) > 256:
            self._forced_queries.clear()
        self._forced_queries[self._event_key(event)] = query

    def _consume_forced_query(self, event: AstrMessageEvent) -> str | None:
        return self._forced_queries.pop(self._event_key(event), None)

    def _event_key(self, event: AstrMessageEvent) -> str:
        message_obj = getattr(event, "message_obj", None)
        message_id = str(getattr(message_obj, "message_id", "") or "")
        if message_id:
            return f"message:{message_id}"
        timestamp = str(getattr(message_obj, "timestamp", "") or "")
        return "|".join(
            [
                str(getattr(event, "unified_msg_origin", "") or ""),
                str(getattr(event, "message_str", "") or ""),
                timestamp,
            ]
        )

    def _format_forced_evidence_prompt(self, query: str, evidence: str) -> str:
        template = self.search_config.forced_evidence_prompt_template
        if not is_valid_forced_evidence_template(template):
            raise ObscuraError("强制搜索证据提示词必须包含 {query} 和 {evidence}。")
        try:
            return template.format(
                query=query,
                evidence=evidence,
                summary_focus=self.search_config.summary_focus,
            )
        except (KeyError, ValueError) as exc:
            raise ObscuraError(f"强制搜索证据提示词格式错误：{exc}") from exc

    @staticmethod
    def _append_extra_user_text(req: ProviderRequest, text: str) -> None:
        parts = getattr(req, "extra_user_content_parts", None)
        if parts is None:
            parts = []
            setattr(req, "extra_user_content_parts", parts)
        if TextPart is not None:
            parts.append(TextPart(text=text).mark_as_temp())
        else:
            parts.append({"type": "text", "text": text})

    async def terminate(self):
        """AstrBot unload hook."""
        logger.info("Obscura Agent Browser plugin terminated.")
