import json
import urllib.request
import urllib.error
import asyncio
from typing import Sequence

from ..models import SearchResult, SearchResponse, SearchConfig, ObscuraError

class ExaProvider:
    def __init__(self, config: SearchConfig) -> None:
        self.config = config

    async def search(self, query: str, *, num_results: int | None = None) -> SearchResponse:
        limit = max(1, min(num_results or self.config.result_count, 100))
        api_key = self.config.search_api_key

        if not api_key:
            raise ObscuraError("Exa API key is required but not configured.")

        url = "https://api.exa.ai/search"
        payload = {
            "query": query,
            "numResults": limit,
            "contents": {
                "text": {"maxCharacters": self.config.max_page_chars},
                "highlights": True
            }
        }
        
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key
        }

        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")

        def _do_request():
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8")
                try:
                    err_json = json.loads(body)
                    msg = err_json.get("error", str(e))
                except Exception:
                    msg = str(e)
                raise ObscuraError(f"Exa API error: {msg}") from e
            except Exception as e:
                raise ObscuraError(f"Exa request failed: {e}") from e

        data = await asyncio.to_thread(_do_request)
        
        results = []
        for item in data.get("results", []):
            highlights = item.get("highlights", [])
            snippet = highlights[0] if highlights else item.get("text", "")[:300]
            
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=snippet,
                content=item.get("text", "")
            ))
            
        if not results:
            return SearchResponse(query=query, search_url="Exa API", results=[], warning="搜索页没有解析到结果。")

        return SearchResponse(query=query, search_url="Exa API", results=results)

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
