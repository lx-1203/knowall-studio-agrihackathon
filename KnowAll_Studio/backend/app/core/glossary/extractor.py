"""Glossary term extraction: parse knowledge nodes / quizzes / document headings,
normalize aliases, dedupe, and idempotently upsert into ``glossary_terms``.

纯核心提取逻辑（无 HTTP / 无 LLM），可脱离数据库单测；数据库交互仅出现在
``sync_glossary_from_db`` / ``export_glossary_snapshot``。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from sqlalchemy import select, func

from app.models import (
    Document,
    Flashcard,
    GlossaryTerm,
    KnowledgePointNode,
    KnowledgeSummary,
    QuestionBank,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------- 正则（任务规范中给定，勿改） ----------------------------

# 详细解释 bullet 正则（兼容 **详细解释**： 与 详细解释： 两种写法）
_DEF_RE = re.compile(r'^\s*[-*]\s*(?:\*\*)?详细解释(?:\*\*)?[：:]\s*(.+)$', re.MULTILINE)
# 关联概念 bullet
_REL_RE = re.compile(r'^\s*[-*]\s*(?:\*\*)?(?:关联概念|关联知识|相关内容)(?:\*\*)?[：:]\s*(.+)$', re.MULTILINE)
# 示例 bullet
_EX_RE = re.compile(r'^\s*[-*]\s*(?:\*\*)?(?:示例|举例|例如)(?:\*\*)?[：:]\s*(.+)$', re.MULTILINE)
# 标题中的括号别名：中文（英文） / 英文（中文）
_ALIAS_PAREN = re.compile(r'[（(]([^（）()]+)[）)]')
# 术语标题里的 emoji 与标签
_TAG_RE = re.compile(r'[🔴🟡🟢🟠]|【[^】]*】')
# 用于归一化的标点（保留中文字符与字母数字）
_PUNCT_RE = re.compile(r'[\s,，、。；;：:（）()\-—/·]+')
# term_definition 题目文本的 "请解释以下术语：" 前缀（兼容冒号变体）
_TERM_PREFIX_RE = re.compile(r'^\s*(?:请解释)?以下术语[：:]\s*')
# bullet 行首标记（如 "- **详细解释**："），用于清洗 full_explanation
_MARKER_RE = re.compile(r'^\s*[-*]\s*(?:\*\*)?[^：:\n]+[：:]\s*')

_ALIAS_MAX_LEN = 64


# ---------------------------- 纯函数（解析 / 别名 / 归一） ----------------------------

def _uniq(items: list[str]) -> list[str]:
    """去重保序（丢弃空值）。"""
    return list(dict.fromkeys([i for i in items if i]))


def parse_explanation_bullets(explanation: str) -> dict:
    """从知识节点 explanation 解析出 定义/关联概念/示例。

    返回 ``{"definition": str, "related": list[str], "example": str}``。
    """
    definition = ""
    related: list[str] = []
    example = ""

    m = _DEF_RE.search(explanation or "")
    if m:
        definition = m.group(1).strip()
    else:
        # 无 bullet 格式：去掉以 # 开头的标题行与空行，剩余作为 definition
        lines = [
            ln.strip()
            for ln in (explanation or "").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        definition = "\n".join(lines)

    rm = _REL_RE.search(explanation or "")
    if rm:
        parts = re.split(r"[、，,]", rm.group(1))
        related = [p.strip() for p in parts if p.strip()]

    em = _EX_RE.search(explanation or "")
    if em:
        example = em.group(1).strip()

    # 解析后不应残留 ** 标记
    definition = definition.replace("**", "")
    return {"definition": definition, "related": related, "example": example}


def extract_aliases_from_title(title: str) -> list[str]:
    """从术语标题提取别名：括号内内容 + 去 emoji/标签/括号后的规范简称。"""
    aliases: list[str] = []
    for m in _ALIAS_PAREN.finditer(title or ""):
        a = m.group(1).strip()
        if a and len(a) <= _ALIAS_MAX_LEN:
            aliases.append(a)
    base = _TAG_RE.sub("", title or "").strip()
    base = _ALIAS_PAREN.sub("", base).strip("（）() ") or (title or "")
    if base and len(base) <= _ALIAS_MAX_LEN:
        aliases.append(base)
    return _uniq(aliases)


def canonicalize(name: str) -> str:
    """归一化：去标点/空白 + 小写（用于同义词去重）。"""
    return _PUNCT_RE.sub("", name or "").lower()


def strip_term_definition_prefix(text: str) -> str:
    """剥离 term_definition 题目文本的前缀（如 "请解释以下术语："）。

    兼容 "请解释以下术语：" / "请解释以下术语:" / "以下术语：" 等写法；
    若文本不含该前缀（无冒号形式），原样返回。
    """
    return _TERM_PREFIX_RE.sub("", text or "")


def _clean_full_explanation(explanation: str) -> str:
    """完整解释：去掉标题行/空行，并剥掉 bullet 行首的 "xx：" 标记（保留正文）。"""
    lines = [ln.strip() for ln in (explanation or "").splitlines() if ln.strip()]
    body = [ln for ln in lines if not ln.startswith("#")] or lines
    return "\n".join(_MARKER_RE.sub("", ln) for ln in body)


def build_term_candidates(
    nodes: list[dict],
    subject: str,
    chapter: str = "",
    source_type: str = "document",
    source_label: str = "",
) -> list[dict]:
    """将知识节点列表转换为术语候选（每节点一条候选）。"""
    candidates: list[dict] = []
    for node in nodes:
        title = node.get("title") or ""
        if (node.get("level") == 1) and any(k in title for k in ("总结", "大全")):
            continue  # L1 的总览/总结节点不作为术语
        if not (node.get("explanation") or "").strip():
            continue  # 无解释的章节节点（L1/L2 标题/Cover）不作为术语
        aliases = extract_aliases_from_title(title)
        parsed = parse_explanation_bullets(node.get("explanation") or "")
        candidates.append({
            "term": title,
            "aliases": aliases,
            "definition": parsed["definition"],
            "full_explanation": _clean_full_explanation(node.get("explanation") or ""),
            "subject": subject,
            "chapter": chapter,
            "source_node_id": node.get("id"),
            "source_summary_id": node.get("summary_id"),
            "source_type": source_type,
            "source_label": source_label,
            "tags": list(node.get("tags") or []),
            "related_terms": parsed["related"],
        })
    return candidates


def merge_candidate(a: dict, b: dict) -> dict:
    """合并两个同词条候选（归一以 a 为主，保留更详细的解释与所有来源）。"""
    merged = dict(a)
    merged["aliases"] = _uniq(list(a.get("aliases") or []) + list(b.get("aliases") or []))
    merged["related_terms"] = _uniq(list(a.get("related_terms") or []) + list(b.get("related_terms") or []))
    merged["tags"] = _uniq(list(a.get("tags") or []) + list(b.get("tags") or []))
    if len(b.get("definition") or "") > len(a.get("definition") or ""):
        # b 的解释更详细：definition 与 full_explanation 一并替换
        merged["definition"] = b.get("definition")
        merged["full_explanation"] = b.get("full_explanation")
    else:
        if not (a.get("full_explanation") or "") and b.get("full_explanation"):
            merged["full_explanation"] = b.get("full_explanation")
    for key in ("chapter", "source_summary_id", "source_document_id"):
        if not merged.get(key) and b.get(key):
            merged[key] = b[key]
    # 来源标记：保留首个非空 label / 非空 type（a 优先）
    if not merged.get("source_label") and b.get("source_label"):
        merged["source_label"] = b["source_label"]
    if not merged.get("source_type") and b.get("source_type"):
        merged["source_type"] = b["source_type"]
    merged["source_node_id"] = a.get("source_node_id")  # 保留首个来源
    return merged


# ---------------------------- 入库 / 快照（db 交互） ----------------------------

async def sync_glossary_from_db(
    db: AsyncSession,
    subject: str = "综合",
    summary_id: str | None = None,
    snapshot_path: str | Path | None = None,
    document_id: str | None = None,
) -> dict:
    """从 知识节点 + 题库(term_definition) + 文档标题 提取术语，归一去重后幂等 upsert。

    ``document_id`` 提供时只处理该文档的元数据（标题来源），用于"上传落库即触发"。
    返回 ``{"added": N, "updated": M, "total": T}``。
    """
    candidates: list[dict] = []

    # ---- 1) 知识节点（L2 标题为章节，L3 继承父 L2 标题） ----
    query = select(KnowledgePointNode)
    if summary_id:
        query = query.where(KnowledgePointNode.summary_id == summary_id)
    nodes = list((await db.execute(query)).scalars().all())

    # ---- 0) AI 来源 label 用 model_used：summary_id 直接查；否则按节点 summary_id 关联查 ----
    summary_ids = {n.summary_id for n in nodes if n.summary_id}
    if summary_id:
        summary_ids.add(summary_id)
    model_used_by_summary: dict[str, str | None] = {}
    if summary_ids:
        sum_rows = (await db.execute(
            select(KnowledgeSummary).where(KnowledgeSummary.id.in_(summary_ids))
        )).scalars().all()
        model_used_by_summary = {s.id: s.model_used for s in sum_rows}

    def _ai_label_for(sum_id: str | None) -> str:
        """AI 来源展示文本：AI 生成（模型名）；无 summary 关联时兜底 deepseek-chat。"""
        return f"AI 生成（{model_used_by_summary.get(sum_id) or 'deepseek-chat'}）"

    node_by_id = {n.id: n for n in nodes}
    for n in nodes:
        if n.level == 2:
            chapter = n.title or ""  # L2 节点标题即章节名
        else:
            parent = node_by_id.get(n.parent_id or "")
            chapter = parent.title if parent is not None and parent.level == 2 else ""
        candidates.extend(build_term_candidates(
            [{k: getattr(n, k) for k in ("id", "title", "explanation", "level", "tags", "summary_id")}],
            subject=subject,
            chapter=chapter,
            source_type="ai",
            source_label=_ai_label_for(n.summary_id),
        ))

    # ---- 2) 题库 term_definition 题型（上限 2000 保险丝） ----
    qb_rows = list((await db.execute(
        select(QuestionBank)
        .where(QuestionBank.question_type == "term_definition")
        .limit(2000)
    )).scalars().all())
    for q in qb_rows:
        text = strip_term_definition_prefix(q.question_text).strip()
        if not text:
            continue
        answer = (q.correct_answer or "").strip()
        analysis = (q.analysis or "").strip()
        text = text[:256]
        candidates.append({
            "term": text,
            "aliases": [text],
            "definition": answer,
            "full_explanation": analysis or answer,
            "subject": subject,
            "chapter": "",
            "source_node_id": None,
            "source_summary_id": summary_id,
            "source_document_id": None,
            "source_type": "ai",
            "source_label": _ai_label_for(summary_id),
            "tags": list(q.tags or []),
            "related_terms": [],
        })

    # ---- 3) Flashcards (card_type=definition) 卡面 ----
    cards = list((await db.execute(
        select(Flashcard).where(Flashcard.card_type == "definition").limit(2000)
    )).scalars().all())
    for card in cards:
        front = (card.front or "").strip()[:256]
        if not front:
            continue
        back = (card.back or "").strip()
        hints = (card.hints or "").strip()
        definition = back or hints
        # 无 summary 外键：经 knowledge_point_id -> 知识节点 -> summary_id 关联，查不到兜底 deepseek-chat
        node = node_by_id.get(card.knowledge_point_id or "")
        card_summary_id = node.summary_id if node is not None else None
        candidates.append({
            "term": front,
            "aliases": [front],
            "definition": definition,
            "full_explanation": back,
            "subject": subject,
            "chapter": "",
            "source_node_id": card.knowledge_point_id or None,
            "source_summary_id": card_summary_id,
            "source_document_id": None,
            "source_type": "ai",
            "source_label": _ai_label_for(card_summary_id),
            "tags": [],
            "related_terms": [],
        })

    # ---- 4) 文档 metadata.headings（document_id 聚焦时只处理该文档） ----
    doc_query = select(Document)
    if document_id:
        doc_query = doc_query.where(Document.id == document_id)
    docs = list((await db.execute(doc_query)).scalars().all())
    for doc in docs:
        meta = doc.metadata_ if isinstance(doc.metadata_, dict) else {}
        headings = meta.get("headings")
        if isinstance(headings, dict):
            entries = list(headings.values())
        elif isinstance(headings, list):
            entries = list(headings)
        else:
            entries = []
        # 来源判定：URL 导入 -> web；上传文件 -> authoritative
        url = meta.get("url") or ""
        if url:
            source_type, source_label = "web", f"网络出处：{urlparse(url).netloc or url[:80]}"
        else:
            source_type, source_label = "authoritative", f"资料库《{doc.filename}》"
        for entry in entries:
            title = (entry.get("title") if isinstance(entry, dict) else entry) or ""
            title = str(title).strip()[:_ALIAS_MAX_LEN]
            if not title:
                continue
            candidates.append({
                "term": title,
                "aliases": [title],
                "definition": "",
                "full_explanation": "",
                "subject": subject,
                "chapter": "",
                "source_node_id": None,
                "source_summary_id": None,
                "source_document_id": doc.id,
                "source_type": source_type,
                "source_label": source_label,
                "tags": [],
                "related_terms": [],
            })

    # ---- 5) (subject, canonicalize(term)) 归一去重后 upsert ----
    merged_map: dict[tuple, dict] = {}
    for c in candidates:
        key = (c["subject"], canonicalize(c["term"]))
        if key in merged_map:
            merged_map[key] = merge_candidate(merged_map[key], c)
        else:
            merged_map[key] = c

    added = updated = 0
    # 批量取回已存在的 (subject, term) 行。原实现对每个候选术语单独查一次，
    # 而一份文档可能产出数百个候选。merged_map 以 (subject, canonicalize(term))
    # 为键，因此其中的 (subject, term) 组合必然唯一，批量查询不会产生同批内碰撞。
    # Guarded by tests/test_glossary/test_extractor_upsert_queries.py.
    existing_rows: dict[tuple[str, str], GlossaryTerm] = {}
    candidate_values = list(merged_map.values())
    if candidate_values:
        subjects = {c["subject"] for c in candidate_values}
        terms = {c["term"] for c in candidate_values}
        found = (await db.execute(
            select(GlossaryTerm).where(
                GlossaryTerm.subject.in_(subjects),
                GlossaryTerm.term.in_(terms),
            )
        )).scalars().all()
        for r in found:
            existing_rows[(r.subject, r.term)] = r

    for cand in merged_map.values():
        row = existing_rows.get((cand["subject"], cand["term"]))
        if row is None:
            db.add(GlossaryTerm(
                term=cand["term"],
                aliases=cand["aliases"],
                definition=cand["definition"],
                full_explanation=cand["full_explanation"],
                subject=cand["subject"],
                chapter=cand["chapter"] or None,
                source_document_id=cand.get("source_document_id") or None,
                source_node_id=cand.get("source_node_id") or None,
                source_summary_id=cand.get("source_summary_id") or None,
                source_type=cand.get("source_type") or "document",
                source_label=cand.get("source_label") or None,
                related_terms=cand["related_terms"],
                tags=cand["tags"],
            ))
            added += 1
        else:
            # 幂等更新：并集去重；definition 取更长（人工修正的更短定义不被覆盖）
            row.aliases = _uniq(list(row.aliases or []) + list(cand["aliases"] or []))
            row.related_terms = _uniq(list(row.related_terms or []) + list(cand["related_terms"] or []))
            row.tags = _uniq(list(row.tags or []) + list(cand["tags"] or []))
            if len(cand["definition"] or "") > len(row.definition or ""):
                row.definition = cand["definition"]
            if not row.full_explanation and cand.get("full_explanation"):
                row.full_explanation = cand["full_explanation"]
            if not row.chapter and cand.get("chapter"):
                row.chapter = cand["chapter"]
            if not row.source_node_id and cand.get("source_node_id"):
                row.source_node_id = cand["source_node_id"]
            if not row.source_document_id and cand.get("source_document_id"):
                row.source_document_id = cand["source_document_id"]
            if not row.source_summary_id and cand.get("source_summary_id"):
                row.source_summary_id = cand["source_summary_id"]
            # 来源分级：仅在默认 document 时允许升级（不覆盖已标定的 authoritative/web/ai）
            if (not row.source_type or row.source_type == "document") and cand.get("source_type") not in (None, "", "document"):
                row.source_type = cand["source_type"]
            if not row.source_label and cand.get("source_label"):
                row.source_label = cand["source_label"]
            updated += 1

    # 清理孤儿空定义行：本次不再产生候选、且解释为空的旧行
    # （如被跳过的 L1/L2 章节节点），避免空卡片残留
    removed = 0
    if merged_map:
        known_terms = {c["term"] for c in merged_map.values()}
        orphan_stmt = select(GlossaryTerm).where(
            GlossaryTerm.subject == subject,
            func.trim(func.coalesce(GlossaryTerm.definition, "")) == "",
        )
        orphans = (await db.execute(orphan_stmt)).scalars().all()
        for o in orphans:
            if o.term not in known_terms:
                await db.delete(o)
                removed += 1

    await db.commit()
    snapshot = await export_glossary_snapshot(db, output_path=snapshot_path)
    logger.info("glossary sync done: added=%d updated=%d removed=%d total=%d", added, updated, removed, snapshot["total"])
    return {"added": added, "updated": updated, "total": snapshot["total"]}


async def export_glossary_snapshot(
    db: AsyncSession,
    subject: str | None = None,
    output_path: str | Path | None = None,
) -> dict:
    """导出全量（或单学科）术语快照为自研 .kgb 格式。

    ``output_path`` 提供时写该路径（测试用 tmp_path 隔离，避免污染生产快照）。
    返回快照 dict。
    """
    query = select(GlossaryTerm).order_by(GlossaryTerm.term)
    if subject:
        query = query.where(GlossaryTerm.subject == subject)
    rows = list((await db.execute(query)).scalars().all())
    snapshot = {
        # ---- 自研 .kgb(Knowledge Glossary Base) 格式头 ----
        "format": "KGB-1.0",
        "format_description": "自研词库格式：每个 entry 为一个术语条目，可直接按 term/aliases 检索",
        "updated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "subjects": sorted({r.subject for r in rows}),
        "total": len(rows),
        "entries": [
            {
                "term": r.term,
                "aliases": list(r.aliases or []),
                "definition": r.definition or "",
                "subject": r.subject,
                "chapter": r.chapter or "",
                "source_type": r.source_type or "document",
                "source_label": r.source_label or "",
                "related_terms": list(r.related_terms or []),
                "tags": list(r.tags or []),
            }
            for r in rows
        ],
    }
    if output_path is not None:
        snap_file = Path(output_path)
    else:
        # parents[3] = backend；默认写 backend/data/glossary_terms.kgb
        from app.config import DATA_DIR
        snap_file = DATA_DIR / "glossary_terms.kgb"
    snap_file.parent.mkdir(parents=True, exist_ok=True)
    snap_file.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return snapshot


def read_kgb_snapshot(path: str | Path) -> dict:
    """读取并校验 .kgb 快照文件，返回完整快照 dict。

    校验 format 头为 KGB-1.0；格式不符或文件非法时抛 ValueError。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f".kgb 快照不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f".kgb 快照不是合法 JSON: {e}") from e
    if data.get("format") != "KGB-1.0":
        raise ValueError(f"非 KGB-1.0 格式: {data.get('format')!r}")
    if not isinstance(data.get("entries"), list):
        raise ValueError(".kgb 快照缺少 entries 列表")
    return data
