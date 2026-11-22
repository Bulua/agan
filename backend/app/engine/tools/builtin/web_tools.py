from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.core.config import settings
from app.core.security import secret_box
from app.engine.tools.base import BaseTool, ToolExecutionContext, ToolResult

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _unwrap_ddg_url(raw_href: str) -> str:
    href = html.unescape((raw_href or "").strip())
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg")
        if uddg and uddg[0]:
            return unquote(uddg[0])
    return href


class _DDGHtmlParser(HTMLParser):
    """Lightweight stdlib HTML parser for DuckDuckGo HTML search results."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._in_title_a = False
        self._in_snippet = False
        self._snippet_depth = 0
        self._cur_title: list[str] = []
        self._cur_href: str = ""
        self._cur_snippet: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        classes = set(attr_map.get("class", "").split())

        if tag == "a" and "result__a" in classes:
            self._in_title_a = True
            self._cur_title = []
            self._cur_href = _unwrap_ddg_url(attr_map.get("href", ""))
            return

        if "result__snippet" in classes:
            self._in_snippet = True
            self._snippet_depth = 1
            self._cur_snippet = []
            return

        if self._in_snippet:
            self._snippet_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._in_title_a and tag == "a":
            self._in_title_a = False
            title = re.sub(r"\s+", " ", "".join(self._cur_title)).strip()
            if title and self._cur_href:
                self.results.append(
                    {
                        "title": title,
                        "url": self._cur_href,
                        "snippet": "",
                    }
                )
            return

        if self._in_snippet:
            self._snippet_depth -= 1
            if self._snippet_depth <= 0:
                self._in_snippet = False
                snippet = re.sub(r"\s+", " ", "".join(self._cur_snippet)).strip()
                if self.results and snippet and not self.results[-1]["snippet"]:
                    self.results[-1]["snippet"] = snippet

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._cur_title.append(data)
        elif self._in_snippet:
            self._cur_snippet.append(data)


class _ReadableHTMLParser(HTMLParser):
    """Convert raw HTML into clean, readable Markdown-like text for LLM consumption."""

    _SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "iframe", "template", "head"})
    _BLOCK_TAGS = frozenset(
        {
            "p",
            "div",
            "section",
            "article",
            "main",
            "header",
            "footer",
            "blockquote",
            "pre",
            "tr",
            "table",
            "ul",
            "ol",
            "hr",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str = ""
        self._in_title = False
        self._skip_depth = 0
        self._in_pre = False
        self._chunks: list[str] = []
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "title":
            self._in_title = True
            return
        if t in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return

        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(t[1])
            self._chunks.append("\n\n" + "#" * level + " ")
        elif t == "li":
            self._chunks.append("\n- ")
        elif t == "br":
            self._chunks.append("\n")
        elif t == "pre":
            self._in_pre = True
            self._chunks.append("\n```\n")
        elif t == "code" and not self._in_pre:
            self._chunks.append("`")
        elif t in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "title":
            self._in_title = False
            self.title = re.sub(r"\s+", " ", "".join(self._title_parts)).strip()
            return
        if t in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth > 0:
            return

        if t == "pre":
            self._in_pre = False
            self._chunks.append("\n```\n")
        elif t == "code" and not self._in_pre:
            self._chunks.append("`")
        elif t in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote"):
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._skip_depth > 0:
            return
        if self._in_pre:
            self._chunks.append(data)
        else:
            collapsed = re.sub(r"[ \t\r\f\v]+", " ", data)
            if collapsed.strip():
                self._chunks.append(collapsed)
            elif collapsed and self._chunks and not self._chunks[-1].endswith((" ", "\n")):
                self._chunks.append(" ")

    def get_text(self) -> str:
        raw = "".join(self._chunks)
        lines = [line.rstrip() for line in raw.splitlines()]
        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()


def parse_ddg_html(html_text: str, max_results: int = 5) -> list[dict[str, str]]:
    parser = _DDGHtmlParser()
    parser.feed(html_text or "")
    return parser.results[:max_results]


def html_to_readable_text(html_text: str) -> tuple[str, str]:
    """Return `(page_title, readable_markdown_text)` from raw HTML."""
    parser = _ReadableHTMLParser()
    parser.feed(html_text or "")
    return parser.title, parser.get_text()


class WebSearchTool(BaseTool):
    name = "web_search"
    requires_sandbox = False
    description = (
        "Search the live web for documentation, recent developments, libraries, error messages, "
        "or factual references. Returns a concise numbered list of titles, URLs, and snippets."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "max_results": {
                "type": "integer",
                "description": "Number of results to return (default 5, min 1, max 10).",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
        },
        "required": ["query"],
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.provider: str = str(cfg.get("provider") or "auto").strip().lower()
        self.base_url: str = str(cfg.get("base_url") or "").strip().rstrip("/")
        enc_key = str(cfg.get("api_key_encrypted") or "").strip()
        raw_key = str(cfg.get("api_key") or "").strip()
        self.api_key: str = secret_box.decrypt(enc_key) if enc_key else raw_key
        self.default_max_results: int = max(1, min(int(cfg.get("default_max_results") or 5), 10))
        self.timeout: float = float(max(3, min(int(cfg.get("timeout") or 15), 60)))

    def _resolve_provider(self) -> tuple[str, str, str]:
        """Return `(provider, api_key, base_url)` resolving 'auto' & env fallbacks."""
        prov = self.provider if self.provider in ("auto", "duckduckgo", "tavily", "searxng", "brave") else "auto"
        if prov == "tavily":
            return "tavily", self.api_key or settings.tavily_api_key, self.base_url or "https://api.tavily.com/search"
        if prov == "brave":
            return "brave", self.api_key or settings.brave_api_key, self.base_url or "https://api.search.brave.com/res/v1/web/search"
        if prov == "searxng":
            return "searxng", self.api_key, self.base_url or settings.searxng_base_url.rstrip("/")
        if prov == "duckduckgo":
            return "duckduckgo", "", self.base_url or "https://html.duckduckgo.com/html/"

        # Auto selection priority: Tavily -> Brave -> SearXNG -> DuckDuckGo
        tavily_key = self.api_key if "tavily" in self.base_url else settings.tavily_api_key
        if tavily_key:
            return "tavily", tavily_key, self.base_url or "https://api.tavily.com/search"
        if settings.brave_api_key:
            return "brave", settings.brave_api_key, "https://api.search.brave.com/res/v1/web/search"
        searx_url = self.base_url or settings.searxng_base_url.rstrip("/")
        if searx_url:
            return "searxng", self.api_key, searx_url
        return "duckduckgo", "", "https://html.duckduckgo.com/html/"

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return ToolResult(success=False, error="Parameter 'query' must be non-empty.")

        raw_max = arguments.get("max_results")
        max_results = max(
            1,
            min(int(raw_max) if raw_max is not None else self.default_max_results, 10),
        )
        provider, api_key, endpoint = self._resolve_provider()

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": DEFAULT_USER_AGENT},
            ) as client:
                if provider == "tavily":
                    if not api_key:
                        return ToolResult(
                            success=False,
                            error="Tavily provider selected but API key is missing. Configure it in Tools -> web_search.",
                        )
                    results = await self._search_tavily(client, endpoint, api_key, query, max_results)
                elif provider == "brave":
                    if not api_key:
                        return ToolResult(
                            success=False,
                            error="Brave Search provider selected but API key is missing. Configure it in Tools -> web_search.",
                        )
                    results = await self._search_brave(client, endpoint, api_key, query, max_results)
                elif provider == "searxng":
                    if not endpoint:
                        return ToolResult(
                            success=False,
                            error="SearXNG provider selected but `base_url` is missing. Configure it in Tools -> web_search.",
                        )
                    results = await self._search_searxng(client, endpoint, query, max_results)
                else:
                    results = await self._search_duckduckgo(client, endpoint, query, max_results)
        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                error=f"Web search timed out after {int(self.timeout)}s (provider={provider}).",
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                success=False,
                error=f"Web search network error (provider={provider}): {exc}",
            )

        if not results:
            return ToolResult(
                success=True,
                output=(
                    f"No search results returned for `{query}` (provider: {provider}). "
                    "Try rephrasing the query, or configure Tavily / SearXNG / Brave in Studio -> Tools."
                ),
                metadata={"query": query, "provider": provider, "count": 0, "results": []},
            )

        lines = [f"Search results for `{query}` (provider: {provider}, {len(results)} result(s)):\n"]
        for idx, item in enumerate(results, start=1):
            lines.append(f"{idx}. **{item['title']}**")
            lines.append(f"   URL: {item['url']}")
            if item.get("snippet"):
                lines.append(f"   {item['snippet']}")
        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "query": query,
                "provider": provider,
                "count": len(results),
                "results": results,
            },
        )

    @staticmethod
    async def _search_tavily(
        client: httpx.AsyncClient,
        endpoint: str,
        api_key: str,
        query: str,
        max_results: int,
    ) -> list[dict[str, str]]:
        resp = await client.post(
            endpoint,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        out: list[dict[str, str]] = []
        for item in (data.get("results") or [])[:max_results]:
            out.append(
                {
                    "title": str(item.get("title") or "").strip() or str(item.get("url") or ""),
                    "url": str(item.get("url") or "").strip(),
                    "snippet": str(item.get("content") or "").strip()[:400],
                }
            )
        return out

    @staticmethod
    async def _search_brave(
        client: httpx.AsyncClient,
        endpoint: str,
        api_key: str,
        query: str,
        max_results: int,
    ) -> list[dict[str, str]]:
        resp = await client.get(
            endpoint,
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        web_items = (data.get("web") or {}).get("results") or []
        out: list[dict[str, str]] = []
        for item in web_items[:max_results]:
            out.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "url": str(item.get("url") or "").strip(),
                    "snippet": str(item.get("description") or "").strip()[:400],
                }
            )
        return out

    @staticmethod
    async def _search_searxng(
        client: httpx.AsyncClient,
        base_url: str,
        query: str,
        max_results: int,
    ) -> list[dict[str, str]]:
        url = base_url if base_url.endswith("/search") else f"{base_url}/search"
        resp = await client.get(url, params={"q": query, "format": "json"})
        resp.raise_for_status()
        data = resp.json()
        out: list[dict[str, str]] = []
        for item in (data.get("results") or [])[:max_results]:
            out.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "url": str(item.get("url") or "").strip(),
                    "snippet": str(item.get("content") or "").strip()[:400],
                }
            )
        return out

    @staticmethod
    async def _search_duckduckgo(
        client: httpx.AsyncClient,
        endpoint: str,
        query: str,
        max_results: int,
    ) -> list[dict[str, str]]:
        resp = await client.post(
            endpoint,
            data={"q": query, "b": ""},
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://html.duckduckgo.com/",
            },
        )
        resp.raise_for_status()
        return parse_ddg_html(resp.text, max_results=max_results)


class WebFetchTool(BaseTool):
    name = "web_fetch"
    requires_sandbox = False
    description = (
        "Fetch a public HTTP/HTTPS web page, documentation article, or JSON endpoint and convert "
        "HTML into clean readable text for inspection. Truncated safely to protect context length."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Target HTTP or HTTPS URL to fetch.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default 12000, min 1000, max 40000).",
                "minimum": 1000,
                "maximum": 40000,
                "default": 12000,
            },
        },
        "required": ["url"],
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.default_max_chars: int = max(1000, min(int(cfg.get("default_max_chars") or 12000), 40000))
        self.timeout: float = float(max(3, min(int(cfg.get("timeout") or 20), 60)))

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        url = str(arguments.get("url") or "").strip()
        if not url:
            return ToolResult(success=False, error="Parameter 'url' is required.")

        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            return ToolResult(
                success=False,
                error="Invalid URL: only `http://` and `https://` URLs with a valid host are allowed.",
            )

        raw_cap = arguments.get("max_chars")
        max_chars = max(
            1000,
            min(int(raw_cap) if raw_cap is not None else self.default_max_chars, 40000),
        )

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                max_redirects=5,
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.5",
                },
            ) as client:
                resp = await client.get(url)
        except httpx.TimeoutException:
            return ToolResult(success=False, error=f"Request timed out after {int(self.timeout)}s: {url}")
        except httpx.HTTPError as exc:
            return ToolResult(success=False, error=f"Failed to fetch URL: {exc}")

        content_type = (resp.headers.get("content-type") or "").lower()
        final_url = str(resp.url)
        status_code = resp.status_code

        if any(
            ct in content_type
            for ct in ("image/", "audio/", "video/", "application/octet-stream", "application/pdf", "application/zip")
        ):
            return ToolResult(
                success=False,
                error=(
                    f"URL returned non-text binary content (`{content_type}`, "
                    f"{len(resp.content)} bytes) which cannot be rendered as readable text."
                ),
                metadata={"url": final_url, "status_code": status_code, "content_type": content_type},
            )

        title = ""
        if "html" in content_type or "<html" in resp.text[:512].lower():
            title, body = html_to_readable_text(resp.text)
        elif "json" in content_type:
            try:
                body = json.dumps(resp.json(), ensure_ascii=False, indent=2)
            except Exception:
                body = resp.text
        else:
            body = resp.text.strip()

        truncated = False
        if len(body) > max_chars:
            body = body[:max_chars] + f"\n\n... [truncated at {max_chars} chars]"
            truncated = True

        header_lines = [f"URL: {final_url}", f"Status: HTTP {status_code}"]
        if title:
            header_lines.append(f"Title: {title}")
        output = "\n".join(header_lines) + "\n\n" + (body or "(empty page body)")

        return ToolResult(
            success=(200 <= status_code < 400),
            output=output,
            metadata={
                "url": final_url,
                "status_code": status_code,
                "title": title,
                "content_type": content_type,
                "chars": len(body),
                "truncated": truncated,
            },
        )
