from __future__ import annotations

import sys
import os
import subprocess
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.web import json_response
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.star.filter.command import GreedyStr
from pydantic import Field
from pydantic.dataclasses import dataclass

try:
    from astrbot.core.agent.message import TextPart
except ImportError:
    TextPart = None

try:
    from .obscura_service import (
        ForcedTask,
        ObscuraError,
        ObscuraSearchService,
        SearchConfig,
        SearchResponse,
        build_forced_task,
        config_from_mapping,
        extract_forced_query,
        extract_http_urls,
        is_valid_forced_evidence_template,
        remove_http_urls,
        resolve_summary_prompt_template,
    )
except ImportError:
    from obscura_service import (
        ForcedTask,
        ObscuraError,
        ObscuraSearchService,
        SearchConfig,
        SearchResponse,
        build_forced_task,
        config_from_mapping,
        extract_forced_query,
        extract_http_urls,
        is_valid_forced_evidence_template,
        remove_http_urls,
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
        "evidence. Use this for recent, factual, source-dependent, or uncertain questions. "
        "If the user provides a URL, prefer obscura_open_url; this tool also opens URLs as a fallback."
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
            urls = extract_http_urls(
                query,
                limit=getattr(self.service.config, "max_urls_per_request", 3),
            )
            if urls:
                response = await self.service.open_urls(
                    urls,
                    question=remove_http_urls(query) or query,
                    warning="检测到 URL，已打开网页而不是执行搜索。",
                )
            else:
                response = await self.service.search(
                    query, num_results=parsed_num_results
                )
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
            logger.warning(
                "Obscura tool could not access AstrBot context for image captioning."
            )
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
                    media.visual_description = (
                        getattr(llm_resp, "completion_text", "") or ""
                    ).strip()
                except Exception as exc:  # noqa: BLE001 - optional tool evidence enrichment
                    media.error = str(exc)
                    logger.warning(
                        f"Failed to caption image in Obscura tool call: {exc}"
                    )


@dataclass
class ObscuraOpenUrlTool(FunctionTool[AstrAgentContext]):
    name: str = "obscura_open_url"
    description: str = (
        "Open one or more http/https URLs with the local Obscura headless browser and return "
        "page text, metadata, media, and design evidence. Use this when the user shares a URL "
        "and asks what is on the page, asks for a summary, or asks a question about that URL."
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "One or more http/https URLs to open.",
                },
                "question": {
                    "type": "string",
                    "description": "Optional user question or instruction about the URL content.",
                },
            },
            "required": ["url"],
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

        url_text = str(kwargs.get("url", "") or "").strip()
        question = str(kwargs.get("question", "") or "").strip()
        urls = extract_http_urls(
            url_text,
            limit=getattr(self.service.config, "max_urls_per_request", 3),
        )
        if not urls and url_text:
            urls = [url_text]

        try:
            response = await self.service.open_urls(
                urls, question=question or remove_http_urls(url_text)
            )
        except ObscuraError as exc:
            return f"error: {exc}"
        except Exception as exc:  # noqa: BLE001 - tool calls must not crash the agent loop
            logger.error(f"Obscura open URL tool failed: {exc}", exc_info=True)
            return f"error: Obscura open URL failed: {exc}"

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
            logger.warning(
                "Obscura tool could not access AstrBot context for image captioning."
            )
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
                    media.visual_description = (
                        getattr(llm_resp, "completion_text", "") or ""
                    ).strip()
                except Exception as exc:  # noqa: BLE001 - optional tool evidence enrichment
                    media.error = str(exc)
                    logger.warning(
                        f"Failed to caption image in Obscura open URL tool call: {exc}"
                    )


class ObscuraAgentBrowserPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.search_config: SearchConfig = config_from_mapping(self.config)
        self.plugin_dir = Path(__file__).resolve().parent
        self.search_service = ObscuraSearchService(
            self.search_config, base_dir=self.plugin_dir
        )
        self._forced_tasks: dict[str, ForcedTask] = {}

        self._generate_launcher_scripts()
        self._register_launcher_endpoints()

        if self.search_config.enable_llm_tool:
            try:
                self.context.add_llm_tools(
                    ObscuraWebSearchTool(service=self.search_service)
                )
                self.context.add_llm_tools(
                    ObscuraOpenUrlTool(service=self.search_service)
                )
                logger.info(
                    "Obscura LLM tools registered: obscura_web_search, obscura_open_url"
                )
            except Exception as exc:  # noqa: BLE001 - plugin should still load for explicit commands
                logger.error(
                    f"Failed to register Obscura LLM tool: {exc}", exc_info=True
                )

    @filter.command("search", alias={"搜索"}, priority=10)
    async def search_command(self, event: AstrMessageEvent, query: GreedyStr):
        """强制使用 Obscura 搜索并总结。用法：/搜索 {关键词} 或 /search {query}"""
        if not self.search_config.enable_force_commands:
            yield event.plain_result("Obscura 显式搜索命令当前已在配置中禁用。")
            event.stop_event()
            return
        async for result in self._handle_forced_trigger(event, str(query)):
            yield result

    def _generate_launcher_scripts(self):
        """Generate independent .bat and .sh launcher scripts for WebUI and CLI"""
        python_exe = sys.executable
        plugin_root = self.plugin_dir
        core_path = plugin_root / "obscura_manager" / "server.py"
        
        # WebUI scripts
        webui_bat = plugin_root / "launch_webui.bat"
        webui_bat.write_text(f'@echo off\n"{python_exe}" "{core_path}"\npause\n', encoding='utf-8')
        
        webui_sh = plugin_root / "launch_webui.sh"
        webui_sh.write_text(f'#!/bin/bash\n"{python_exe}" "{core_path}"\n', encoding='utf-8')
        try: os.chmod(webui_sh, 0o755)
        except Exception: pass

        # CLI scripts (Assuming server.py can take --cli or similar, or just distinct names)
        # We'll just pass --cli for now, even if it ignores it, it's a good placeholder.
        cli_bat = plugin_root / "launch_cli.bat"
        cli_bat.write_text(f'@echo off\n"{python_exe}" "{core_path}" --cli\npause\n', encoding='utf-8')
        
        cli_sh = plugin_root / "launch_cli.sh"
        cli_sh.write_text(f'#!/bin/bash\n"{python_exe}" "{core_path}" --cli\n', encoding='utf-8')
        try: os.chmod(cli_sh, 0o755)
        except Exception: pass

    def _register_launcher_endpoints(self):
        """Register endpoints for the Launcher Dashboard Page"""
        plugin_name = "astrbot_plugin_agent_browser"
        
        # Status endpoint
        self.context.register_web_api(
            f"/{plugin_name}/launcher/status",
            self.handle_launcher_status,
            ["GET"],
            "Check Obscura Manager status"
        )
        
        # Start WebUI endpoint
        self.context.register_web_api(
            f"/{plugin_name}/launcher/start_webui",
            self.handle_start_webui,
            ["POST"],
            "Start standalone WebUI"
        )
        
        # Start CLI endpoint
        self.context.register_web_api(
            f"/{plugin_name}/launcher/start_cli",
            self.handle_start_cli,
            ["POST"],
            "Start standalone CLI"
        )

    async def handle_launcher_status(self):
        # We could check if port 8000 is listening or if process exists
        return json_response({"status": "ok", "message": "Launcher Ready"})

    async def handle_start_webui(self):
        try:
            core_path = self.plugin_dir / "obscura_manager" / "server.py"
            # CREATE_NEW_CONSOLE is Windows specific (0x00000010)
            creationflags = 0x00000010 if sys.platform == "win32" else 0
            subprocess.Popen([sys.executable, str(core_path)], creationflags=creationflags)
            return json_response({"status": "ok", "message": "WebUI 已尝试在独立窗口中启动"})
        except Exception as e:
            logger.error(f"Failed to start WebUI: {e}", exc_info=True)
            return json_response({"status": "error", "message": str(e)})

    async def handle_start_cli(self):
        try:
            core_path = self.plugin_dir / "obscura_manager" / "server.py"
            creationflags = 0x00000010 if sys.platform == "win32" else 0
            subprocess.Popen([sys.executable, str(core_path), "--cli"], creationflags=creationflags)
            return json_response({"status": "ok", "message": "CLI 已尝试在独立窗口中启动"})
        except Exception as e:
            logger.error(f"Failed to start CLI: {e}", exc_info=True)
            return json_response({"status": "error", "message": str(e)})

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def search_prefix_listener(self, event: AstrMessageEvent):
        """监听强制搜索触发词"""
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
    async def inject_forced_search_evidence(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        task = self._consume_forced_task(event)
        if not task:
            return
        if not self.search_config.enabled:
            self._append_extra_user_text(req, "Obscura 浏览插件当前已禁用。")
            return

        try:
            logger.info(f"Obscura forced browser task: {task.kind} {task.query}")
            search_response = await self._execute_forced_task(task)
            await self._caption_images(search_response)
            evidence = search_response.to_markdown(include_content=True)
            forced_prompt = self._format_forced_evidence_prompt(task.query, evidence)
        except ObscuraError as exc:
            logger.warning(f"Obscura forced browser evidence failed: {exc}")
            forced_prompt = f"Obscura 浏览失败：{exc}"
        except Exception as exc:  # noqa: BLE001 - surface failure to the main model
            logger.error(
                f"Unexpected Obscura forced browser evidence failure: {exc}",
                exc_info=True,
            )
            forced_prompt = f"Obscura 浏览失败：{exc}"

        self._append_extra_user_text(req, forced_prompt)

    async def _handle_forced_trigger(self, event: AstrMessageEvent, query: str):
        query = (query or "").strip()
        if not query:
            yield event.plain_result("请提供搜索关键词，例如：/搜索 AstrBot 插件开发")
            event.stop_event()
            return

        if not self.search_config.enabled:
            yield event.plain_result("Obscura 浏览插件当前已禁用。")
            event.stop_event()
            return

        task = build_forced_task(
            query, max_urls=self.search_config.max_urls_per_request
        )
        if self.search_config.force_trigger_mode == "main_bot":
            self._record_forced_task(event, task)
            return

        async for result in self._run_direct_reply_task(event, task):
            yield result

    async def _run_direct_reply_task(self, event: AstrMessageEvent, task: ForcedTask):
        try:
            logger.info(f"Obscura direct reply browser task: {task.kind} {task.query}")
            search_response = await self._execute_forced_task(task)
            await self._caption_images(search_response)
            answer = await self._summarize(event, task.query, search_response)
        except ObscuraError as exc:
            logger.warning(f"Obscura forced browser task failed: {exc}")
            answer = f"浏览失败：{exc}"
        except Exception as exc:  # noqa: BLE001 - keep plugin failures visible but contained
            logger.error(
                f"Unexpected Obscura forced browser task failure: {exc}", exc_info=True
            )
            answer = f"浏览失败：{exc}"

        yield event.plain_result(answer)
        event.stop_event()

    async def _execute_forced_task(self, task: ForcedTask) -> SearchResponse:
        if task.kind == "open_urls":
            return await self.search_service.open_urls(task.urls, question=task.query)
        return await self.search_service.search(task.query)

    async def _summarize(
        self,
        event: AstrMessageEvent,
        query: str,
        search_response: SearchResponse,
    ) -> str:
        evidence = search_response.to_markdown(include_content=True)
        if not search_response.results:
            return f"没有解析到可用浏览器材料。\n\n{evidence}"

        provider_id = self.search_config.summary_provider_id
        if not provider_id:
            provider_id = await self.context.get_current_chat_provider_id(
                event.unified_msg_origin
            )

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
            logger.error(
                f"Failed to summarize Obscura browser evidence: {exc}", exc_info=True
            )
            return "浏览完成，但摘要模型调用失败。以下是原始浏览器材料：\n\n" + evidence

        completion = getattr(llm_resp, "completion_text", "") or ""
        if completion.strip():
            return completion.strip()
        return "浏览完成，但摘要模型没有返回文本。以下是原始浏览器材料：\n\n" + evidence

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
                    media.visual_description = (
                        getattr(llm_resp, "completion_text", "") or ""
                    ).strip()
                except Exception as exc:  # noqa: BLE001 - image caption is optional evidence enrichment
                    media.error = str(exc)
                    logger.warning(
                        f"Failed to caption image with Obscura evidence: {exc}"
                    )

    def _record_forced_task(self, event: AstrMessageEvent, task: ForcedTask) -> None:
        if len(self._forced_tasks) > 256:
            self._forced_tasks.clear()
        self._forced_tasks[self._event_key(event)] = task

    def _consume_forced_task(self, event: AstrMessageEvent) -> ForcedTask | None:
        return self._forced_tasks.pop(self._event_key(event), None)

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
            raise ObscuraError("主 Bot 证据注入模板必须包含 {query} 和 {evidence}。")
        try:
            return template.format(
                query=query,
                evidence=evidence,
                summary_focus=self.search_config.summary_focus,
            )
        except (KeyError, ValueError) as exc:
            raise ObscuraError(f"主 Bot 证据注入模板格式错误：{exc}") from exc

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
