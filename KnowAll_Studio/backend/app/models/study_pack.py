"""User-owned, source-versioned learning packs reuse the existing resource tables."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, UniqueConstraint
from app.database import Base


class StudyPack(Base):
    __tablename__ = 'study_packs'
    __table_args__ = (UniqueConstraint('user_id', 'cache_key', name='uq_study_pack_user_cache'),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False, index=True)
    document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    source_hash = Column(String(64), nullable=False)
    cache_key = Column(String(64), nullable=True)
    config = Column(JSON, nullable=False, default=dict)
    sections = Column(JSON, nullable=False, default=list)
    summary_id = Column(String(36), ForeignKey('knowledge_summaries.id', ondelete='SET NULL'))
    tree_id = Column(String(36), ForeignKey('knowledge_trees.id', ondelete='SET NULL'))
    deck_id = Column(String(36), ForeignKey('decks.id', ondelete='SET NULL'))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
