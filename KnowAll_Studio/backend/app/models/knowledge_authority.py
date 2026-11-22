"""Verified external references; deliberately no upload/evidence foreign keys."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String

from app.database import Base
from app.models.knowledge_network import now


class AuthoritySourceSearch(Base):
    __tablename__ = 'network_authority_searches'
    concept_id = Column(String(68), ForeignKey('network_concepts.id', ondelete='CASCADE'), primary_key=True)
    sources = Column(JSON, nullable=False, default=list)
    attempts = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=now, onupdate=now)
