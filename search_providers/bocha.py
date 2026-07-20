import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, Sequence
from urllib.parse import urlparse

from ..models import SearchResult, SearchResponse, SearchConfig, ObscuraError

BOCHA_ENDPOINTS = (
    "https://api.bocha.cn/v1/web-search",
    "https://api.bochaai.com/v1/web-search",
)
BOCHA_MAX_RESULTS = 50

def check_bocha_error(data: dict[str, Any]) -> str | None:
    code = data.get("code")
    if code is None:
        return None
    try:
        code_num = int(code)
    except (TypeError, ValueError):
        code_num = None
    if code_num == 200:
        return None
    return str(data.get("msg") or data.get("message") or f"code={code}")

def parse_bocha_results(data: dict[str, Any], *, limit: int) -> list[SearchResult]:
    web_pages = (data.get("data") or {}).get("webPages") or data.get("webPages") or {}
    items = web_pages.get("value")
    if not isinstance(items, list):
        return []

    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url or url in seen:
            continue
        seen.add(url)
        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=str(item.get("summary") or item.get("snippet") or "").strip(),
            )
        )
        if len(results) >= limit:
            break
    return results

class BochaProvider:
    def __init__(self, config: SearchConfig) -> None:
        self.config = config

    def _post(self, endpoint: str, body: bytes, api_key: str) -> dict[str, Any]:
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            message = detail
            try:
                err = json.loads(detail)
                message = str(err.get("msg") or err.get("message") or detail)
            except Exception:
                pass
            raise ObscuraError(f"Bocha API HTTP {exc.code}: {message}") from exc
        except ObscuraError:
            raise
        except Exception as exc:
            raise ObscuraError(f"Bocha request failed: {exc}") from exc

    async def search(self, query: str, *, num_results: int | None = None) -> SearchResponse:
        limit = max(1, min(num_results or self.config.result_count, BOCHA_MAX_RESULTS))
        api_key = self.config.search_api_key
        if not api_key:
            raise ObscuraError("Bocha API key is required but not configured.")

        body = json.dumps(
            {
                "query": query,
                "freshness": "noLimit",
                "summary": True,
                "count": limit,
            }
        ).encode("utf-8")

        last_error: Exception | None = None
        for endpoint in BOCHA_ENDPOINTS:
            try:
                data = await asyncio.to_thread(self._post, endpoint, body, api_key)
            except Exception as exc:
                last_error = exc
                continue

            api_error = check_bocha_error(data)
            if api_error is not None:
                last_error = ObscuraError(f"Bocha API error: {api_error}")
                continue

            results = parse_bocha_results(data, limit=limit)
            if not results:
                return SearchResponse(
                    query=query,
                    search_url="Bocha API",
                    results=[],
                    warning="搜索页没有解析到结果。",
                )
            return SearchResponse(query=query, search_url="Bocha API", results=results)

        raise ObscuraError(f"Bocha search failed on all endpoints: {last_error}")

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
