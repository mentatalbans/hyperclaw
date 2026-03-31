"""
Search Provider Abstraction Layer.
Unified interface for web search, academic search, and knowledge bases.
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Single search result."""
    title: str
    url: str
    snippet: str
    raw: dict = field(default_factory=dict)


@dataclass
class SearchResponse:
    """Search response containing multiple results."""
    results: list[SearchResult]
    total: int | None = None
    raw: dict = field(default_factory=dict)


class SearchProvider(ABC):
    """Abstract base for search providers."""

    name: str = "base"

    @abstractmethod
    async def search(
        self,
        query: str,
        num_results: int = 10,
        **kwargs,
    ) -> SearchResponse:
        """Execute a search query."""
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE SEARCH (via Custom Search API)
# ═══════════════════════════════════════════════════════════════════════════════

class GoogleSearchProvider(SearchProvider):
    """Google Custom Search API."""

    name = "google"

    def __init__(self, api_key: str | None = None, cx: str | None = None):
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        self.cx = cx or os.environ.get("GOOGLE_CSE_ID")
        self.base_url = "https://www.googleapis.com/customsearch/v1"

    async def search(
        self,
        query: str,
        num_results: int = 10,
        **kwargs,
    ) -> SearchResponse:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.base_url,
                params={
                    "key": self.api_key,
                    "cx": self.cx,
                    "q": query,
                    "num": min(num_results, 10),
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            results = [
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    raw=item,
                )
                for item in data.get("items", [])
            ]

            return SearchResponse(
                results=results,
                total=int(data.get("searchInformation", {}).get("totalResults", 0)),
                raw=data,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# SERPAPI
# ═══════════════════════════════════════════════════════════════════════════════

class SerpAPIProvider(SearchProvider):
    """SerpAPI - Google Search wrapper with structured results."""

    name = "serpapi"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("SERPAPI_API_KEY")
        self.base_url = "https://serpapi.com/search"

    async def search(
        self,
        query: str,
        num_results: int = 10,
        **kwargs,
    ) -> SearchResponse:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.base_url,
                params={
                    "api_key": self.api_key,
                    "q": query,
                    "num": num_results,
                    "engine": "google",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            results = [
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    raw=item,
                )
                for item in data.get("organic_results", [])
            ]

            return SearchResponse(
                results=results,
                total=data.get("search_information", {}).get("total_results"),
                raw=data,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# BING SEARCH
# ═══════════════════════════════════════════════════════════════════════════════

class BingSearchProvider(SearchProvider):
    """Microsoft Bing Search API."""

    name = "bing"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("BING_API_KEY")
        self.base_url = "https://api.bing.microsoft.com/v7.0/search"

    async def search(
        self,
        query: str,
        num_results: int = 10,
        **kwargs,
    ) -> SearchResponse:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.base_url,
                headers={"Ocp-Apim-Subscription-Key": self.api_key},
                params={"q": query, "count": num_results},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            results = [
                SearchResult(
                    title=item.get("name", ""),
                    url=item.get("url", ""),
                    snippet=item.get("snippet", ""),
                    raw=item,
                )
                for item in data.get("webPages", {}).get("value", [])
            ]

            return SearchResponse(
                results=results,
                total=data.get("webPages", {}).get("totalEstimatedMatches"),
                raw=data,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# DUCKDUCKGO
# ═══════════════════════════════════════════════════════════════════════════════

class DuckDuckGoProvider(SearchProvider):
    """DuckDuckGo Instant Answer API (free, no API key required)."""

    name = "duckduckgo"

    def __init__(self):
        self.base_url = "https://api.duckduckgo.com"

    async def search(
        self,
        query: str,
        num_results: int = 10,
        **kwargs,
    ) -> SearchResponse:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.base_url,
                params={"q": query, "format": "json", "no_html": 1},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            results = []

            # Abstract result
            if data.get("Abstract"):
                results.append(
                    SearchResult(
                        title=data.get("Heading", ""),
                        url=data.get("AbstractURL", ""),
                        snippet=data.get("Abstract", ""),
                        raw=data,
                    )
                )

            # Related topics
            for topic in data.get("RelatedTopics", [])[:num_results - len(results)]:
                if isinstance(topic, dict) and topic.get("FirstURL"):
                    results.append(
                        SearchResult(
                            title=topic.get("Text", "")[:100],
                            url=topic.get("FirstURL", ""),
                            snippet=topic.get("Text", ""),
                            raw=topic,
                        )
                    )

            return SearchResponse(results=results, raw=data)


# ═══════════════════════════════════════════════════════════════════════════════
# PERPLEXITY SEARCH
# ═══════════════════════════════════════════════════════════════════════════════

class PerplexitySearchProvider(SearchProvider):
    """Perplexity AI - AI-powered search with citations."""

    name = "perplexity"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("PERPLEXITY_API_KEY")
        self.base_url = "https://api.perplexity.ai"

    async def search(
        self,
        query: str,
        num_results: int = 10,
        model: str = "llama-3.1-sonar-small-128k-online",
        **kwargs,
    ) -> SearchResponse:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": query}],
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

            content = data["choices"][0]["message"]["content"]
            citations = data.get("citations", [])

            results = [
                SearchResult(
                    title=f"Source {i + 1}",
                    url=url,
                    snippet=content[:200] if i == 0 else "",
                    raw={"url": url},
                )
                for i, url in enumerate(citations[:num_results])
            ]

            # Include the AI summary as the first result
            if content:
                results.insert(
                    0,
                    SearchResult(
                        title="AI Summary",
                        url="",
                        snippet=content,
                        raw=data,
                    ),
                )

            return SearchResponse(results=results, raw=data)


# ═══════════════════════════════════════════════════════════════════════════════
# ARXIV
# ═══════════════════════════════════════════════════════════════════════════════

class ArxivProvider(SearchProvider):
    """arXiv academic paper search."""

    name = "arxiv"

    def __init__(self):
        self.base_url = "http://export.arxiv.org/api/query"

    async def search(
        self,
        query: str,
        num_results: int = 10,
        **kwargs,
    ) -> SearchResponse:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.base_url,
                params={
                    "search_query": f"all:{query}",
                    "start": 0,
                    "max_results": num_results,
                    "sortBy": "relevance",
                },
                timeout=30.0,
            )
            response.raise_for_status()

            # Parse Atom XML
            import xml.etree.ElementTree as ET
            root = ET.fromstring(response.text)

            ns = {"atom": "http://www.w3.org/2005/Atom"}
            results = []

            for entry in root.findall("atom:entry", ns):
                title = entry.find("atom:title", ns)
                summary = entry.find("atom:summary", ns)
                link = entry.find("atom:id", ns)

                results.append(
                    SearchResult(
                        title=title.text.strip() if title is not None else "",
                        url=link.text.strip() if link is not None else "",
                        snippet=summary.text.strip()[:500] if summary is not None else "",
                        raw={"entry": entry},
                    )
                )

            total_elem = root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
            total = int(total_elem.text) if total_elem is not None else None

            return SearchResponse(results=results, total=total)


# ═══════════════════════════════════════════════════════════════════════════════
# WIKIPEDIA
# ═══════════════════════════════════════════════════════════════════════════════

class WikipediaProvider(SearchProvider):
    """Wikipedia search API."""

    name = "wikipedia"

    def __init__(self, language: str = "en"):
        self.language = language
        self.base_url = f"https://{language}.wikipedia.org/w/api.php"

    async def search(
        self,
        query: str,
        num_results: int = 10,
        **kwargs,
    ) -> SearchResponse:
        async with httpx.AsyncClient() as client:
            # Search for page titles
            search_response = await client.get(
                self.base_url,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": num_results,
                    "format": "json",
                },
                timeout=30.0,
            )
            search_response.raise_for_status()
            search_data = search_response.json()

            results = []
            for item in search_data.get("query", {}).get("search", []):
                title = item.get("title", "")
                snippet = item.get("snippet", "").replace("<span class=\"searchmatch\">", "").replace("</span>", "")
                url = f"https://{self.language}.wikipedia.org/wiki/{title.replace(' ', '_')}"

                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        raw=item,
                    )
                )

            return SearchResponse(
                results=results,
                total=search_data.get("query", {}).get("searchinfo", {}).get("totalhits"),
                raw=search_data,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TAVILY (AI-optimized search)
# ═══════════════════════════════════════════════════════════════════════════════

class TavilyProvider(SearchProvider):
    """Tavily AI-optimized search API."""

    name = "tavily"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        self.base_url = "https://api.tavily.com"

    async def search(
        self,
        query: str,
        num_results: int = 10,
        **kwargs,
    ) -> SearchResponse:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": num_results,
                    "include_answer": True,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            results = []

            # Include AI answer if present
            if data.get("answer"):
                results.append(
                    SearchResult(
                        title="AI Answer",
                        url="",
                        snippet=data["answer"],
                        raw={"answer": data["answer"]},
                    )
                )

            # Add search results
            for item in data.get("results", []):
                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("content", "")[:500],
                        raw=item,
                    )
                )

            return SearchResponse(results=results, raw=data)


# ═══════════════════════════════════════════════════════════════════════════════
# BRAVE SEARCH
# ═══════════════════════════════════════════════════════════════════════════════

class BraveSearchProvider(SearchProvider):
    """Brave Search API."""

    name = "brave"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY")
        self.base_url = "https://api.search.brave.com/res/v1/web/search"

    async def search(
        self,
        query: str,
        num_results: int = 10,
        **kwargs,
    ) -> SearchResponse:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.base_url,
                headers={
                    "X-Subscription-Token": self.api_key,
                    "Accept": "application/json",
                },
                params={"q": query, "count": num_results},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            results = [
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                    raw=item,
                )
                for item in data.get("web", {}).get("results", [])
            ]

            return SearchResponse(results=results, raw=data)


# ═══════════════════════════════════════════════════════════════════════════════
# YOU.COM
# ═══════════════════════════════════════════════════════════════════════════════

class YouSearchProvider(SearchProvider):
    """You.com Search API."""

    name = "you"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("YOU_API_KEY")
        self.base_url = "https://api.you.com/search"

    async def search(
        self,
        query: str,
        num_results: int = 10,
        **kwargs,
    ) -> SearchResponse:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.base_url,
                headers={"X-API-Key": self.api_key},
                params={"query": query},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            results = [
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("snippet", ""),
                    raw=item,
                )
                for item in data.get("hits", [])[:num_results]
            ]

            return SearchResponse(results=results, raw=data)


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

SEARCH_PROVIDERS: dict[str, type[SearchProvider]] = {
    "google": GoogleSearchProvider,
    "serpapi": SerpAPIProvider,
    "bing": BingSearchProvider,
    "duckduckgo": DuckDuckGoProvider,
    "ddg": DuckDuckGoProvider,
    "perplexity": PerplexitySearchProvider,
    "arxiv": ArxivProvider,
    "wikipedia": WikipediaProvider,
    "wiki": WikipediaProvider,
    "tavily": TavilyProvider,
    "brave": BraveSearchProvider,
    "you": YouSearchProvider,
}


def get_search_provider(name: str | None = None, **kwargs) -> SearchProvider:
    """Get a search provider by name."""
    name = name or os.environ.get("HYPERCLAW_SEARCH_PROVIDER", "duckduckgo")
    name = name.lower()

    if name not in SEARCH_PROVIDERS:
        available = ", ".join(sorted(SEARCH_PROVIDERS.keys()))
        raise ValueError(f"Unknown search provider: {name}. Available: {available}")

    return SEARCH_PROVIDERS[name](**kwargs)
