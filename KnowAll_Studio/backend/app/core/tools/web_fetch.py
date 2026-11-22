"""Web fetch tool — fetches and extracts text content from URLs."""
from __future__ import annotations

import logging
import re
from typing import Any

from app.core.tools.base import Tool, ToolResult, tool_registry

logger = logging.getLogger("knowall.tools.web_fetch")


class WebFetchTool(Tool):
    """Fetch a URL and extract its text content."""

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "抓取网页内容并提取正文文本。输入URL，返回页面主要文本内容。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的网页URL",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "最大返回字符数",
                    "default": 5000,
                },
            },
            "required": ["url"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        url = kwargs.get("url", "")
        max_chars = kwargs.get("max_chars", 5000)

        if not url:
            return ToolResult(success=False, error="url is required")

        # SSRF guard: refuse private/loopback/internal targets so the tool
        # cannot be used to reach cloud metadata or intranet services.
        try:
            from app.core.mcp.client import _validate_sse_url
            _validate_sse_url(url)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))

        try:
            import httpx

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")

                if "text/html" in content_type:
                    text = self._extract_text(resp.text)
                else:
                    text = resp.text

            text = text[:max_chars]

            return ToolResult(
                success=True,
                output={"url": url, "content": text, "length": len(text)},
                metadata={"url": url, "content_type": content_type},
            )

        except Exception as e:
            logger.error("Web fetch failed for %s: %s", url, e)
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _extract_text(html: str) -> str:
        """Extract visible text from HTML without BeautifulSoup."""
        # Remove script/style tags
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Decode common entities
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&nbsp;', ' ').replace('&quot;', '"')
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text


# Auto-register
tool_registry.register(WebFetchTool())
