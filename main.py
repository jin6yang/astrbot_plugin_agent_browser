from __future__ import annotations

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
    )
except ImportError:
    from obscura_service import (
        ObscuraError,
        ObscuraSearchService,
        SearchConfig,
        SearchResponse,
        config_from_mapping,
        extract_forced_query,
    )


SUMMARY_PROMPT_TEMPLATE = """你是一个严谨的联网搜索助手。请基于下面的 Obscura 浏览器搜索材料回答用户问题。

要求：
1. 优先使用搜索材料，不要把没有依据的内容说成事实。
2. 结论后用 [1]、[2] 这样的编号标注来源。
3. 如果材料不足，请明确说明不足，并给出已找到的信息。
4. 用用户提问的语言回答，保持简洁但覆盖关键事实。

用户问题：
{query}

搜索材料：
{evidence}
"""


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
        del context
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

        return response.to_markdown(include_content=True)


class ObscuraAgentBrowserPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.search_config: SearchConfig = config_from_mapping(self.config)
        self.search_service = ObscuraSearchService(self.search_config)

        if self.search_config.enable_llm_tool:
            try:
                self.context.add_llm_tools(ObscuraWebSearchTool(service=self.search_service))
                logger.info("Obscura LLM tool registered: obscura_web_search")
            except Exception as exc:  # noqa: BLE001 - plugin should still load for explicit commands
                logger.error(f"Failed to register Obscura LLM tool: {exc}", exc_info=True)

    @filter.command("search", alias={"搜索"}, priority=10)
    async def search_command(self, event: AstrMessageEvent, query: GreedyStr):
        """强制使用 Obscura 搜索并总结。用法：/搜索 关键词 或 /search query"""
        async for result in self._run_forced_search(event, str(query)):
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def search_prefix_listener(self, event: AstrMessageEvent):
        """监听非斜杠搜索前缀，例如：搜索 AstrBot 插件开发"""
        query = extract_forced_query(
            event.message_str,
            self.search_config.force_prefixes,
            include_slash_commands=False,
        )
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

        prompt = SUMMARY_PROMPT_TEMPLATE.format(query=query, evidence=evidence)
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

    async def terminate(self):
        """AstrBot unload hook."""
        logger.info("Obscura Agent Browser plugin terminated.")
