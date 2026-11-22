"""Owned knowledge compilations, independent of original document storage."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, JSON, String
from app.database import Base


class KnowledgeCollection(Base):
    __tablename__ = 'knowledge_collections'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False, index=True)
    title = Column(String(120), nullable=False)
    subject = Column(String(80), nullable=False)
    points = Column(JSON, nullable=False)
    provenance = Column(JSON, nullable=False, default=dict)
    review_status = Column(String(24), nullable=False, default='needs_review')
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
