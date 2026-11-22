"""名词解释库（Glossary）API"""
from typing import Annotated
from fastapi import APIRouter, HTTPException, Depends, Path, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, func, or_, Text
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from app.database import get_db
from app.models import GlossaryTerm
from app.core.glossary.extractor import sync_glossary_from_db
from app.core.auth import get_optional_user

router = APIRouter(prefix="/api/v1/glossary", tags=["glossary"])
snapshot_router = APIRouter(tags=["glossary"])  # 无前缀：为 molbio-quiz.html 等静态页提供快照


@snapshot_router.get("/glossary_terms.kgb")
async def serve_glossary_snapshot():
    """返回自研 .kgb 词库快照（molbio-quiz.html 离线加载；文件由 sync 管道导出）。"""
    from pathlib import Path
    from app.config import DATA_DIR
    snap = DATA_DIR / "glossary_terms.kgb"
    if not snap.exists():
        raise HTTPException(404, "词库快照尚未生成，请先触发 /api/v1/glossary/sync")
    return FileResponse(str(snap), media_type="application/json; charset=utf-8")


class SyncRequest(BaseModel):
    subject: str = "综合"
    summary_id: str | None = None


def _like_escape(s: str) -> str:
    """转义 LIKE 模式中的通配符（须配合 escape='\\\\' 使用，防止用户输入 %/_ 误匹配）。"""
    return (s or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_dict(t: GlossaryTerm) -> dict:
    """GlossaryTerm -> JSON dict。"""
    return {
        "id": t.id,
        "term": t.term,
        "aliases": list(t.aliases or []),
        "definition": t.definition or "",
        "full_explanation": t.full_explanation or "",
        "subject": t.subject,
        "chapter": t.chapter or "",
        "source_document_id": t.source_document_id,
        "source_node_id": t.source_node_id,
        "source_summary_id": t.source_summary_id,
        "source_type": t.source_type or "document",
        "source_label": t.source_label or "",
        "related_terms": list(t.related_terms or []),
        "tags": list(t.tags or []),
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


@router.get("/terms")
async def list_terms(
    subject: str | None = None,
    chapter: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """分页查询术语库，支持学科/章节/关键词筛选。"""
    conds = []
    if subject:
        conds.append(GlossaryTerm.subject == subject)
    if chapter:
        conds.append(GlossaryTerm.chapter == chapter)
    if q:
        like_q = _like_escape(q)
        conds.append(or_(
            GlossaryTerm.term.like(f"%{like_q}%", escape="\\"),
            GlossaryTerm.definition.like(f"%{like_q}%", escape="\\"),
            GlossaryTerm.aliases.cast(Text).like(f"%{like_q}%", escape="\\"),
        ))

    count_stmt = select(func.count()).select_from(GlossaryTerm)
    stmt = select(GlossaryTerm).order_by(GlossaryTerm.term)
    if conds:
        count_stmt = count_stmt.where(*conds)
        stmt = stmt.where(*conds)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = list((await db.execute(
        stmt.offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_to_dict(r) for r in rows],
    }


@router.get("/terms/suggest")
async def suggest_terms(
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """术语前缀/子串联想（返回术语规范名列表，去重）"""
    like_q = _like_escape(q)
    stmt = (
        select(GlossaryTerm.term)
        .where(or_(
            GlossaryTerm.term.like(f"%{like_q}%", escape="\\"),
            GlossaryTerm.aliases.cast(Text).like(f"%{like_q}%", escape="\\"),
        ))
        .distinct()
        .order_by(GlossaryTerm.term)
        .limit(limit)
    )
    terms = list((await db.execute(stmt)).scalars().all())
    return {"suggestions": terms}


@router.get("/terms/{term}")
async def get_term(
    term: str = Path(..., max_length=256),
    subject: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """按名称查询术语（支持同名词条的学科区分：可选 subject 过滤）。

    同名多条且未传 subject 时返回第一条（按字典序 subject；由 get_term_variants 展示全量）。
    """
    stmt = select(GlossaryTerm).where(GlossaryTerm.term == term)
    if subject:
        stmt = stmt.where(GlossaryTerm.subject == subject)
    row = (await db.execute(stmt)).scalars().first()

    if row is None:
        # 按别名回退（aliases 为 JSON 列，cast 后 LIKE '"别名"' 精确匹配数组内元素）
        like_term = _like_escape(term)
        alias_stmt = select(GlossaryTerm).where(
            GlossaryTerm.aliases.cast(Text).like(f'%"{like_term}"%', escape="\\")
        ).order_by(GlossaryTerm.subject)
        if subject:
            alias_stmt = alias_stmt.where(GlossaryTerm.subject == subject)
        row = (await db.execute(alias_stmt)).scalars().first()

    if row is None:
        raise HTTPException(404, f"术语不存在: {term}")
    return _to_dict(row)


@router.get("/terms/{term}/variants")
async def get_term_variants(
    term: str = Path(..., max_length=256),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """查询同名词条的所有学科变体（跨学科缩写歧义多候选展示）。"""
    stmt = (
        select(GlossaryTerm)
        .where(GlossaryTerm.term == term)
        .order_by(GlossaryTerm.subject)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        like_term = _like_escape(term)
        rows = list((await db.execute(
            select(GlossaryTerm)
            .where(GlossaryTerm.aliases.cast(Text).like(f'%"{like_term}"%', escape="\\"))
            .order_by(GlossaryTerm.subject)
        )).scalars().all())
    return {"items": [_to_dict(r) for r in rows]}


@router.post("/sync")
async def sync_glossary(
    req: SyncRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """手动触发词库同步（从知识节点/题库/文档标题提取，幂等 upsert）。"""
    result = await sync_glossary_from_db(db, subject=req.subject, summary_id=req.summary_id)
    return {"ok": True, "added": result["added"], "updated": result["updated"], "total": result["total"]}


@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """词库统计：总量 + 学科列表。"""
    total = (await db.execute(select(func.count()).select_from(GlossaryTerm))).scalar_one()
    subject_rows = list((await db.execute(
        select(GlossaryTerm.subject).distinct()
    )).scalars().all())
    return {
        "total": total,
        "subjects": sorted(subject_rows),
        "snapshot_path": "data/glossary_terms.kgb",
    }
