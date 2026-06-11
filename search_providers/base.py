from typing import Protocol, Sequence
from ..models import SearchResponse

class SearchProvider(Protocol):
    async def search(self, query: str, *, num_results: int | None = None) -> SearchResponse:
        ...

    async def open_urls(
        self,
        urls: Sequence[str],
        *,
        question: str = "",
        warning: str = "",
    ) -> SearchResponse:
        ...
