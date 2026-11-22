# KnowAll_Studio/backend/tests/test_glossary/test_sync.py
"""sync_glossary_from_db 集成测试：内存 SQLite（独立于主库 MySQL，绝不连真库）。

快照一律写 tmp_path（snapshot_path 传参），绝不触碰生产 backend/data/glossary_terms.json。
"""
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.glossary.extractor import sync_glossary_from_db
from app.database import Base
from app.models import (
    Document,
    Flashcard,
    GlossaryTerm,
    KnowledgePointNode,
    KnowledgeSummary,
    QuestionBank,
)


@pytest.fixture
async def glossary_db():
    """内存 SQLite + StaticPool（单一连接，跨 session 共享同一内存库）。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_sync_glossary_from_db_integration(glossary_db, tmp_path):
    db = glossary_db
    summary_id = "sum_test_1"
    doc_id = "doc_test_1"
    snapshot_path = tmp_path / "glossary_snapshot.json"
    db.add_all([
        KnowledgeSummary(
            id=summary_id, document_id=doc_id, content_md="# 概述\n正文",
            node_count=2, model_used="deepseek-chat",
        ),
        KnowledgePointNode(
            id="kp_L2_ch3", summary_id=summary_id, document_id=doc_id, parent_id=None,
            level=2, sequence=1, title="第三章 DNA的复制",
            explanation="#### 第三章 DNA的复制\n- **详细解释**：本章介绍DNA复制的全过程。", tags=[],
        ),
        KnowledgePointNode(
            id="kp_L3_rep", summary_id=summary_id, document_id=doc_id, parent_id="kp_L2_ch3",
            level=3, sequence=1, title="复制子（replicon）",
            explanation="#### 复制子（replicon）🔴【必考】\n"
                        "- **详细解释**：含一个起始区的复制单位。\n"
                        "- **关联概念**：复制叉、Ori\n",
            tags=["必考"],
        ),
        QuestionBank(
            id="q_term_1", question_type="term_definition",
            question_text="请解释以下术语：半保留复制",
            correct_answer="每个子代DNA保留一条母链", analysis=None, tags=[],
        ),
        Flashcard(
            id="fc_def_1", card_type="definition", front="复制叉",
            back="复制过程中形成的分叉结构", knowledge_point_id="kp_L3_rep",
        ),
        # 上传文件：metadata.headings + 无 url -> authoritative；标签模板含 filename
        Document(
            id="doc_head_1", filename="分子生物学讲义.docx", file_type="docx",
            sha256="sha_doc_head_1", status="ready", page_count=10,
            metadata_={"headings": {"H2": {"title": "DNA双螺旋结构"}}},
        ),
        # URL 导入：metadata 含 url -> web（netloc 截取）
        Document(
            id="doc_web_1", filename="web-article.html", file_type="url",
            sha256="sha_doc_web_1", status="ready", page_count=1,
            metadata_={
                "url": "https://example.com/wiki/dna",
                "headings": {"H2": {"title": "DNA复制机制"}},
            },
        ),
    ])
    await db.commit()

    result = await sync_glossary_from_db(
        db, subject="分子生物学", summary_id=summary_id, snapshot_path=snapshot_path,
    )
    # L2 节点 + L3 节点 + term_definition 题 + flashcard + 2 个文档标题 = 6 个词条
    assert result["added"] == 6
    assert result["total"] == 6

    # 快照写入 tmp_path（不污染生产 backend/data/glossary_terms.json），内容齐全
    assert snapshot_path.exists()
    snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snap["format"] == "KGB-1.0"  # 自研 .kgb 格式头
    assert snap["total"] == 6
    assert len(snap["entries"]) == 6
    assert {t["term"] for t in snap["entries"]} >= {
        "复制子（replicon）", "第三章 DNA的复制", "半保留复制", "复制叉",
        "DNA双螺旋结构", "DNA复制机制",
    }

    # a) L3 子节点：chapter 继承父 L2 标题；AI 来源分级
    l3 = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "复制子（replicon）")
    )).scalars().first()
    assert l3 is not None
    assert l3.chapter == "第三章 DNA的复制"
    assert l3.subject == "分子生物学"
    assert l3.source_node_id == "kp_L3_rep"
    assert l3.source_summary_id == summary_id
    assert l3.source_type == "ai"
    assert l3.source_label == "AI 生成（deepseek-chat）"
    assert "必考" in (l3.tags or [])

    # b) L2 节点自身：chapter == 自身标题
    l2 = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "第三章 DNA的复制")
    )).scalars().first()
    assert l2 is not None
    assert l2.chapter == "第三章 DNA的复制"

    # d) term_definition 题：剥离前缀后的剩余部分作为 term
    q_row = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "半保留复制")
    )).scalars().first()
    assert q_row is not None
    assert q_row.definition == "每个子代DNA保留一条母链"

    # flashcard 来源：term=front，source_node_id 回链知识节点
    fc = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "复制叉")
    )).scalars().first()
    assert fc is not None
    assert fc.definition == "复制过程中形成的分叉结构"
    assert fc.source_node_id == "kp_L3_rep"

    # e) 文档标题来源（上传文件，无 url）：authoritative + 标签含 filename
    doc_row = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "DNA双螺旋结构")
    )).scalars().first()
    assert doc_row is not None
    assert doc_row.source_type == "authoritative"
    assert "分子生物学讲义.docx" in (doc_row.source_label or "")

    # f) 文档标题来源（URL 导入）：web + netloc
    web_row = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "DNA复制机制")
    )).scalars().first()
    assert web_row is not None
    assert web_row.source_type == "web"
    assert web_row.source_label == "网络出处：example.com"

    # c) 幂等：重复 sync 不重复插入（同样写 tmp 快照）
    before = (await db.execute(select(func.count()).select_from(GlossaryTerm))).scalar_one()
    result2 = await sync_glossary_from_db(
        db, subject="分子生物学", summary_id=summary_id, snapshot_path=snapshot_path,
    )
    after = (await db.execute(select(func.count()).select_from(GlossaryTerm))).scalar_one()
    assert result2["added"] == 0
    assert after == before == 6


async def test_sync_upsert_rules_two_rounds(glossary_db, tmp_path):
    """两轮同步：definition 只在严格更长时覆盖；source_type 会升级；AI 标签能走兜底。"""
    db = glossary_db
    summary_id = "sum_r2_1"
    snapshot_path = tmp_path / "snapshot_r2.json"
    db.add_all([
        KnowledgeSummary(
            id=summary_id, document_id="doc_r2", content_md="# 概述\n正文",
            node_count=1, model_used="deepseek-chat",
        ),
        KnowledgePointNode(
            id="kp_r2_ai", summary_id=summary_id, document_id="doc_r2", parent_id=None,
            level=2, sequence=1, title="第二轮主题",
            explanation="#### 第二轮主题\n- **详细解释**：用于验证 upsert 规则。", tags=[],
        ),
        # 该节点指向一个不存在的 summary -> AI 来源标签必须走兜底
        KnowledgePointNode(
            id="kp_r2_orphan", summary_id="sum_missing_row", document_id="doc_r2",
            parent_id=None, level=2, sequence=2, title="无总结节点的术语",
            explanation="#### 无总结节点的术语\n- **详细解释**：兜底标签验证。", tags=[],
        ),
        QuestionBank(
            id="q_r2_1", question_type="term_definition",
            question_text="请解释以下术语：半保留复制",
            correct_answer="每个子代DNA保留一条母链", analysis=None, tags=[],
        ),
        Document(
            id="doc_r2_head", filename="第二轮讲义.docx", file_type="docx",
            sha256="sha_r2_head", status="ready", page_count=1,
            metadata_={"headings": {"H2": {"title": "第二轮权威术语"}}},
        ),
    ])
    await db.commit()

    first = await sync_glossary_from_db(
        db, subject="分子生物学", snapshot_path=snapshot_path,
    )
    assert first["added"] == 4
    assert first["total"] == 4

    # (C) 无 summary 关联的节点：AI 标签走兜底（不是取某个模型名）
    orphan = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "无总结节点的术语")
    )).scalars().first()
    assert orphan is not None
    assert orphan.source_type == "ai"
    assert orphan.source_label == "AI 生成（deepseek-chat）"

    # 第一轮后的真实状态，作为第二轮「不被覆盖 / 被升级」的基线
    q_row = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "半保留复制")
    )).scalars().first()
    assert q_row is not None
    assert q_row.definition == "每个子代DNA保留一条母链"
    doc_row = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "第二轮权威术语")
    )).scalars().first()
    assert doc_row is not None
    assert doc_row.source_type == "authoritative"

    # 手工把状态改坏：definition 换成同长度但不同内容；source_type 降级为 document
    q_row.definition = "每个子代DNA保留两条母链"
    doc_row.source_type = "document"
    await db.commit()
    assert len(q_row.definition) == len("每个子代DNA保留一条母链")

    second = await sync_glossary_from_db(
        db, subject="分子生物学", snapshot_path=snapshot_path,
    )
    assert second["added"] == 0
    assert second["total"] == 4

    # (A) definition 只在严格更长时覆盖：同长度不覆盖（> 而不是 >=）
    after = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "半保留复制")
    )).scalars().first()
    assert after.definition == "每个子代DNA保留两条母链"

    # (B) source_type 升级：document -> 更强来源 authoritative
    upgraded = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "第二轮权威术语")
    )).scalars().first()
    assert upgraded.source_type == "authoritative"

    # (C') 兜底标签在第二轮同样成立
    orphan2 = (await db.execute(
        select(GlossaryTerm).where(GlossaryTerm.term == "无总结节点的术语")
    )).scalars().first()
    assert orphan2.source_label == "AI 生成（deepseek-chat）"

