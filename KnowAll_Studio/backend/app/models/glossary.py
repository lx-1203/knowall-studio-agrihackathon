"""名词解释库（Glossary）模型。"""
from sqlalchemy import Column, String, Text, DateTime, JSON, UniqueConstraint, Index
from app.database import Base
# 复用 app.models 里已定义的 gen_uuid/now（models/__init__.py 顶部）
from app.models import gen_uuid, now


class GlossaryTerm(Base):
    """系统级名词解释条目。

    术语来自知识资料库（知识节点/题库/文档标题），经提取管道归一去重后入库。
    同 (subject, term) 唯一——同一缩写在不同学科各一条（如 PCR 在分子生物学/医学）。
    重名/别名归一进 aliases。
    """
    __tablename__ = "glossary_terms"
    __table_args__ = (
        UniqueConstraint("subject", "term", name="uq_glossary_subject_term"),
        Index("ix_glossary_subject", "subject"),
        Index("ix_glossary_chapter", "chapter"),
    )

    id = Column(String(36), primary_key=True, default=gen_uuid)
    term = Column(String(256), nullable=False)          # 术语规范名（如 PCR / 复制子）
    aliases = Column(JSON, default=list)                # 别名/同义词数组（如 ["聚合酶链式反应"]）
    definition = Column(Text)                           # 解释正文（精简版）
    full_explanation = Column(Text)                     # 完整解释（知识节点原文）
    subject = Column(String(64), nullable=False, default="综合")  # 学科（如 分子生物学）
    chapter = Column(String(256))                       # 章节（如 第三章 DNA的复制）
    source_document_id = Column(String(36))             # 来源文档
    source_node_id = Column(String(64))                 # 来源知识节点
    source_summary_id = Column(String(36))              # 来源总纲
    source_type = Column(String(20), default="document")   # authoritative/web/ai/document
    source_label = Column(String(512))                      # 展示文本（如 "资料库《xxx.docx》" / "网络出处：example.com" / "AI 生成（deepseek-chat）"）
    related_terms = Column(JSON, default=list)          # 关联术语名
    tags = Column(JSON, default=list)                   # 必考/重点/了解/易错
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    def __repr__(self):
        return f"<GlossaryTerm {self.subject}/{self.term}>"
