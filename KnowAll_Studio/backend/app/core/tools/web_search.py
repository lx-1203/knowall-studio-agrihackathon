"""Web search tool — searches the internet via DuckDuckGo."""
from __future__ import annotations

import logging
from typing import Any

from app.core.tools.base import Tool, ToolResult, tool_registry

logger = logging.getLogger("knowall.tools.web_search")


class WebSearchTool(Tool):
    """Search the web for information using DuckDuckGo."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "搜索互联网获取最新信息。输入搜索关键词，返回搜索结果摘要和链接。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回结果数",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 5)

        if not query:
            return ToolResult(success=False, error="query is required")

        try:
            import httpx

            # Use DuckDuckGo HTML API (no key required)
            url = "https://html.duckduckgo.com/html/"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.post(url, data={"q": query}, headers=headers)
                resp.raise_for_status()
                html = resp.text
                # DuckDuckGo also returns a verification page with HTTP 202
                # (occasionally 200). That is not a successful zero-hit search.
                if resp.status_code == 202 or 'anomaly-modal' in html or 'id="challenge-form"' in html:
                    return ToolResult(
                        success=False,
                        error='Search provider requires verification; retry later',
                        metadata={'reason': 'verification_required'},
                    )

            # Parse results from HTML (lightweight, no bs4 dependency)
            results = self._parse_ddg_html(html, max_results)

            return ToolResult(
                success=True,
                output=results,
                metadata={"query": query, "result_count": len(results)},
            )

        except ImportError:
            return ToolResult(success=False, error="httpx not installed")
        except Exception as e:
            logger.error("Web search failed: %s", e)
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _parse_ddg_html(html: str, max_results: int) -> list[dict]:
        """Parse DuckDuckGo HTML results without BeautifulSoup."""
        import re
        results = []

        # Find result blocks
        blocks = re.findall(
            r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'class="result__snippet"[^>]*>(.*?)</(?:span|div)',
            html, re.DOTALL
        )

        for href, title, snippet in blocks[:max_results]:
            # Clean HTML tags
            clean_title = re.sub(r'<[^>]+>', '', title).strip()
            clean_snippet = re.sub(r'<[^>]+>', '', snippet).strip()
            # Decode DuckDuckGo redirect URL
            actual_url = href
            if "uddg=" in href:
                match = re.search(r'uddg=([^&]+)', href)
                if match:
                    from urllib.parse import unquote
                    actual_url = unquote(match.group(1))

            if clean_title:
                results.append({
                    "title": clean_title,
                    "url": actual_url,
                    "snippet": clean_snippet,
                })

        return results


# Auto-register
tool_registry.register(WebSearchTool())
