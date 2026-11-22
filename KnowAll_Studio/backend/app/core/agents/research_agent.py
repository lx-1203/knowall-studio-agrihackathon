"""Deep Research Agent — 4-phase agentic research pipeline.

Pipeline:
  1. Rephrase: refine the research topic (optional user confirmation)
  2. Decompose: break into N sub-topics
  3. Research: agentic loop per sub-topic (THINK → search → collect → FINISH)
  4. Report: generate structured report with citations
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator
from urllib.parse import quote, urlsplit

from app.core.tools import execute_tool

logger = logging.getLogger("knowall.research")


class ResearchStatus(str, Enum):
    PENDING = "pending"
    REPHRASING = "rephrasing"
    DECOMPOSING = "decomposing"
    RESEARCHING = "researching"
    REPORTING = "reporting"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class SubTopic:
    id: str
    title: str
    description: str
    findings: list[dict] = field(default_factory=list)
    status: str = "pending"  # pending / researching / done


@dataclass
class ResearchTask:
    task_id: str
    topic: str
    refined_topic: str = ""
    sub_topics: list[SubTopic] = field(default_factory=list)
    report: str = ""
    sources: list[dict] = field(default_factory=list)
    status: ResearchStatus = ResearchStatus.PENDING
    progress: float = 0.0
    error: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    user_id: str = "local_user"


class ResearchAgent:
    """Multi-phase agentic research pipeline."""

    def __init__(self):
        self._tasks: dict[str, ResearchTask] = {}
        self._running: set[asyncio.Task] = set()
        self._closing = False

    async def start_research(
        self,
        topic: str,
        task_id: str | None = None,
        max_subtopics: int = 8,
        user_id: str = "local_user",
    ) -> ResearchTask:
        """Start a new research task. Returns immediately; research runs in background."""
        if self._closing:
            raise RuntimeError("Research service is shutting down")
        task_id = task_id or str(uuid.uuid4())[:8]
        task = ResearchTask(task_id=task_id, topic=topic, user_id=user_id)
        self._tasks[task_id] = task

        # Run research in background
        job = asyncio.create_task(self._run_pipeline(task, max_subtopics))
        self._running.add(job)
        job.add_done_callback(lambda finished: self._finish(task, finished))

        return task

    def _finish(self, task: ResearchTask, job: asyncio.Task) -> None:
        self._running.discard(job)
        # A task cancelled before its first step never enters the pipeline's try.
        if job.cancelled() and task.status not in (ResearchStatus.COMPLETED, ResearchStatus.ERROR):
            task.status = ResearchStatus.ERROR
            task.error = "Research cancelled"
            task.completed_at = time.time()

    async def close(self) -> None:
        """Stop accepting work and await cancellation/resource cleanup."""
        self._closing = True
        jobs = list(self._running)
        for job in jobs:
            if not job.cancelling():
                job.cancel()
        cleanup = asyncio.gather(*jobs, return_exceptions=True)
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # Cancelling the closer must not cancel a job's async finally again.
            await cleanup
            raise
        self._running.difference_update(jobs)

    def get_task(self, task_id: str, user_id: str | None = None) -> ResearchTask | None:
        task = self._tasks.get(task_id)
        return task if task and (user_id is None or task.user_id == user_id) else None

    def list_tasks(self, user_id: str | None = None) -> list[dict]:
        return [
            {
                "task_id": t.task_id,
                "topic": t.topic,
                "refined_topic": t.refined_topic,
                "status": t.status.value,
                "progress": t.progress,
                "sub_topic_count": len(t.sub_topics),
                "source_count": len(t.sources),
                "created_at": t.created_at,
                "completed_at": t.completed_at,
            }
            for t in sorted(self._tasks.values(), key=lambda x: x.created_at, reverse=True)
            if user_id is None or t.user_id == user_id
        ]

    async def stream_progress(self, task_id: str) -> AsyncIterator[dict]:
        """SSE stream of research progress events."""
        task = self._tasks.get(task_id)
        if not task:
            yield {"event": "error", "data": "Task not found"}
            return

        last_data = None
        while task.status not in (ResearchStatus.COMPLETED, ResearchStatus.ERROR):
            data = {
                "status": task.status.value,
                "progress": task.progress,
                "topic": task.refined_topic or task.topic,
                "sub_topics": [
                    {"title": st.title, "status": st.status}
                    for st in task.sub_topics
                ],
            }
            # A phase can span multiple subtopics; emit changed public state
            # while suppressing unchanged polling snapshots for each subscriber.
            if data != last_data:
                yield {"event": "progress", "data": data}
                last_data = data
            await asyncio.sleep(0.5)

        # Final event
        if task.status == ResearchStatus.COMPLETED:
            yield {
                "event": "complete",
                "data": {
                    "report": task.report,
                    "sources": task.sources,
                    "sub_topics": [
                        {"title": st.title, "status": st.status, "finding_count": len(st.findings)}
                        for st in task.sub_topics
                    ],
                },
            }
        else:
            yield {"event": "error", "data": task.error}

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    async def _run_pipeline(self, task: ResearchTask, max_subtopics: int):
        """Execute the full 4-phase research pipeline."""
        from app.core.api_scheduler.client import _current_user_id
        token = _current_user_id.set(task.user_id)
        try:
            # Phase 1: Rephrase
            task.status = ResearchStatus.REPHRASING
            task.progress = 0.05
            task.refined_topic = await self._rephrase(task.topic)

            # Phase 2: Decompose
            task.status = ResearchStatus.DECOMPOSING
            task.progress = 0.15
            task.sub_topics = await self._decompose(task.refined_topic, max_subtopics)

            # Phase 3: Research each sub-topic
            task.status = ResearchStatus.RESEARCHING
            total = len(task.sub_topics)
            for i, sub in enumerate(task.sub_topics):
                sub.status = "researching"
                findings = await self._research_subtopic(sub, task.refined_topic)
                sub.findings = findings
                sub.status = "done"
                task.progress = 0.2 + 0.6 * (i + 1) / total

            # Phase 4: Report
            task.status = ResearchStatus.REPORTING
            task.progress = 0.85
            task.report, task.sources = await self._generate_report(task)

            task.status = ResearchStatus.COMPLETED
            task.progress = 1.0
            task.completed_at = time.time()

            logger.info("Research completed: %s (%.1fs)", task.task_id,
                        task.completed_at - task.created_at)

        except asyncio.CancelledError:
            task.status = ResearchStatus.ERROR
            task.error = "Research cancelled"
            task.completed_at = time.time()
            raise
        except Exception as e:
            task.status = ResearchStatus.ERROR
            task.error = str(e)
            task.completed_at = time.time()
            logger.error("Research failed: %s — %s", task.task_id, e)
        finally:
            _current_user_id.reset(token)

    async def _rephrase(self, topic: str) -> str:
        """Phase 1: Refine the research topic."""
        from app.core.api_scheduler import api_client

        prompt = f"""请将以下研究主题细化为一个具体、明确的研究问题。保留原始意图，但使表述更精确、更适合学术研究。

原始主题：{topic}

请只返回细化后的研究主题（一句话），不要添加其他说明。"""

        result = await api_client.chat(prompt, task_type="research")
        refined = result.strip().strip("\"' \t\r\n").strip() if isinstance(result, str) else ''
        return refined or topic

    async def _decompose(self, topic: str, max_subtopics: int) -> list[SubTopic]:
        """Phase 2: Decompose into sub-topics."""
        from app.core.api_scheduler import api_client

        prompt = f"""请将以下研究主题分解为 {max_subtopics} 个子课题。每个子课题应该是一个独立的研究方向，覆盖主题的不同方面。

研究主题：{topic}

请以JSON数组格式返回，每个元素包含 title 和 description 字段。
示例：
[
  {{"title": "子课题1标题", "description": "该子课题的研究方向和重点"}},
  {{"title": "子课题2标题", "description": "该子课题的研究方向和重点"}}
]

只返回JSON数组，不要添加其他文字。"""

        result = await api_client.chat(prompt, task_type="research")
        try:
            if not isinstance(result, str):
                raise ValueError("Invalid research subtopics")
            # Extract JSON from response
            json_match = result.strip()
            if json_match.startswith("```"):
                import re
                match = re.search(r'\[.*\]', json_match, re.DOTALL)
                if match:
                    json_match = match.group(0)

            items = json.loads(json_match)
            if not isinstance(items, list) or not items:
                raise ValueError("No research subtopics")
            selected = items[:max_subtopics]
            if any(
                not isinstance(item, dict)
                or not isinstance(item.get('title'), str)
                or not item['title'].strip()
                or not isinstance(item.get('description', ''), str)
                for item in selected
            ):
                raise ValueError("Invalid research subtopic fields")
            return [
                SubTopic(
                    id=str(uuid.uuid4())[:8],
                    title=item['title'].strip(),
                    description=item.get('description', '').strip(),
                )
                for item in selected
            ]
        except (json.JSONDecodeError, TypeError, ValueError):
            # Fallback: create a single sub-topic
            return [SubTopic(id=str(uuid.uuid4())[:8], title=topic, description=topic)]

    async def _research_subtopic(self, sub: SubTopic, main_topic: str) -> list[dict]:
        """Phase 3: Research a single sub-topic using tools."""
        findings = []

        # Search web
        search_result = await execute_tool("web_search", query=f"{sub.title} {main_topic}", max_results=5)
        if search_result.success:
            for r in search_result.output:
                findings.append({
                    "source": "web",
                    "title": r.get("title", ""),
                    "snippet": r.get("snippet", ""),
                    "url": r.get("url", ""),
                })

        # Search papers
        paper_result = await execute_tool("paper_search", query=f"{sub.title}", max_results=3)
        if paper_result.success:
            for p in paper_result.output:
                findings.append({
                    "source": "arxiv",
                    "title": p.get("title", ""),
                    "snippet": p.get("abstract", ""),
                    "url": p.get("url", ""),
                })

        # Search local knowledge base
        kb_result = await execute_tool("knowledge_search", query=sub.title, top_k=3)
        if kb_result.success and kb_result.output:
            for r in kb_result.output.get("results", []):
                findings.append({
                    "source": "knowledge_base",
                    "title": r.get("title", ""),
                    "snippet": r.get("text", ""),
                    "url": "",
                    "chunk_id": r.get("id", ""),
                    "metadata": r.get("metadata") or {},
                })

        return findings

    async def _generate_report(self, task: ResearchTask) -> tuple[str, list[dict]]:
        """Phase 4: Generate a structured research report."""
        from app.core.api_scheduler import api_client

        # Number complete findings before model generation. Repeated URLs and
        # local chunks retain their first assigned number across subtopics.
        unique_sources = []
        source_numbers = {}
        findings_text = []
        remaining = 6000
        for sub in task.sub_topics:
            heading = f"\n## {sub.title[:200]}\n"
            if len(heading) > remaining:
                break
            findings_text.append(heading)
            remaining -= len(heading)
            for f in sub.findings:
                metadata = f.get('metadata') or {}
                doc_id = metadata.get('doc_id')
                chunk_id = f.get('chunk_id', '')
                url = f.get('url') or ''
                if urlsplit(url).scheme.lower() not in ('http', 'https'):
                    url = ''
                key = ('url', url) if url else (
                    ('local', doc_id, chunk_id or metadata.get('chunk_index')) if doc_id else None
                )
                number = source_numbers.get(key, len(unique_sources) + 1) if key else None
                title = f.get('title') or (f'资料 {doc_id}' if doc_id else '未定位发现')
                locator = url or (f"/materials/{quote(str(doc_id), safe='')} 页码 {metadata.get('page_range') or '未标注'}" if doc_id else '未定位来源')
                label = f'[来源{number}] ' if number else ''
                entry = f"- {label}{title[:200]} ({locator}): {str(f.get('snippet') or '')[:200]}\n"
                if len(entry) > remaining:
                    continue
                findings_text.append(entry)
                remaining -= len(entry)
                if key and key not in source_numbers:
                    source_numbers[key] = number
                    source = {'source_id': number, 'title': title, 'url': url, 'source': f.get('source', '')}
                    if not url:
                        source.update(doc_id=doc_id, chunk_id=chunk_id, page_range=metadata.get('page_range'))
                    unique_sources.append(source)

        findings_combined = ''.join(findings_text)

        prompt = f"""你是一个专业的研究助手。请根据以下研究发现，撰写一份结构化的研究报告。

研究主题：{task.refined_topic}

研究发现：
{findings_combined}

请按照以下结构撰写报告：
1. 引言（研究背景和目的）
2. 各子课题的详细分析
3. 综合讨论（各子课题之间的联系）
4. 结论与展望

要求：
- 使用Markdown格式
- 在适当位置用 [来源N] 标注引用
- 只使用研究发现中已提供的来源编号，不得编造编号；未定位来源的发现不能作为有编号的引用
- 报告应该详细、有深度、逻辑清晰
- 使用中文撰写"""

        report = await api_client.chat(prompt, task_type="research", max_tokens=8192)
        if not isinstance(report, str) or not report.strip():
            raise ValueError("研究报告生成为空，请重试")

        valid_numbers = {s['source_id'] for s in unique_sources}
        markers = re.findall(r'\[来源([^\]]*)\]', report)
        if any(not marker.strip().isdecimal() or int(marker.strip()) not in valid_numbers for marker in markers):
            raise ValueError("研究报告引用了未提供的来源编号，请重试")

        # Add source list to report
        if unique_sources:
            report += "\n\n## 参考来源\n\n"
            for i, s in enumerate(unique_sources, 1):
                link = s['url'] or f"/materials/{quote(str(s['doc_id']), safe='')}"
                page = f"，页码 {s['page_range']}" if s.get('page_range') else ''
                report += f"{i}. [{s['title']}]({link}) ({s['source']}{page})\n"

        return report, unique_sources


# Module singleton
research_agent = ResearchAgent()
