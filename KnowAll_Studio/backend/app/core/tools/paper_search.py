"""Paper search tool — searches academic papers via arXiv API."""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

from app.core.tools.base import Tool, ToolResult, tool_registry

logger = logging.getLogger("knowall.tools.paper_search")


class PaperSearchTool(Tool):
    """Search for academic papers on arXiv."""

    @property
    def name(self) -> str:
        return "paper_search"

    @property
    def description(self) -> str:
        return "在arXiv上搜索学术论文。输入关键词，返回相关论文的标题、摘要和链接。"

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

            url = (
                f"http://export.arxiv.org/api/query"
                f"?search_query=all:{quote(query)}"
                f"&start=0&max_results={max_results}"
                f"&sortBy=relevance&sortOrder=descending"
            )

            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                xml_text = resp.text

            papers = self._parse_arxiv_xml(xml_text)

            return ToolResult(
                success=True,
                output=papers,
                metadata={"query": query, "result_count": len(papers)},
            )

        except Exception as e:
            logger.error("Paper search failed: %s", e)
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _parse_arxiv_xml(xml: str) -> list[dict]:
        """Parse arXiv Atom XML without external dependencies."""
        papers = []

        entries = re.findall(r'<entry>(.*?)</entry>', xml, re.DOTALL)

        for entry in entries:
            title = re.search(r'<title>(.*?)</title>', entry, re.DOTALL)
            summary = re.search(r'<summary>(.*?)</summary>', entry, re.DOTALL)
            link = re.search(r'<id>(.*?)</id>', entry)
            published = re.search(r'<published>(.*?)</published>', entry)
            authors = re.findall(r'<name>(.*?)</name>', entry)

            # Extract arxiv ID from link
            arxiv_id = ""
            if link:
                arxiv_id = link.group(1).split("/abs/")[-1] if "/abs/" in link.group(1) else link.group(1)

            papers.append({
                "title": re.sub(r'\s+', ' ', title.group(1)).strip() if title else "",
                "abstract": re.sub(r'\s+', ' ', summary.group(1)).strip()[:500] if summary else "",
                "arxiv_id": arxiv_id,
                "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
                "authors": authors[:5],
                "published": published.group(1)[:10] if published else "",
            })

        return papers


# Auto-register
tool_registry.register(PaperSearchTool())
