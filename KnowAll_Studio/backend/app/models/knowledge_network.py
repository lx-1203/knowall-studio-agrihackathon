"""Central identities with deliberately separate private evidence and learning state."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, DateTime, JSON, Text, ForeignKey, Boolean, UniqueConstraint
from app.database import Base


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NetworkConcept(Base):
    __tablename__ = 'network_concepts'
    id = Column(String(68), primary_key=True)
    name = Column(String(120), nullable=False)
    subject = Column(String(80), nullable=False, index=True)
    topic = Column(String(120), nullable=False)
    public_content = Column(JSON(none_as_null=True), nullable=True)
    review_status = Column(String(24), nullable=False, default='needs_review')
    created_at = Column(DateTime, nullable=False, default=now)


class ExtractionJob(Base):
    __tablename__ = 'network_extraction_jobs'
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), nullable=False, index=True)
    document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True)
    source_hash = Column(String(64), nullable=False)
    subject = Column(String(80), nullable=False, default='')
    model = Column(String(256), nullable=False)
    cache_key = Column(String(64), nullable=False, unique=True)
    status = Column(String(24), nullable=False, default='pending')
    point_count = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now)
    updated_at = Column(DateTime, nullable=False, default=now, onupdate=now)


class ConceptSource(Base):
    __tablename__ = 'network_concept_sources'
    id = Column(String(36), primary_key=True, default=uid)
    concept_id = Column(String(68), ForeignKey('network_concepts.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True)
    chunk_id = Column(String(36), ForeignKey('document_chunks.id', ondelete='CASCADE'), nullable=False)
    quote = Column(Text, nullable=False)
    quote_hash = Column(String(64), nullable=False)
    content = Column(JSON, nullable=False)
    reviewed = Column(Boolean, nullable=False, default=False)
    summary_id = Column(String(36), nullable=True)
    legacy_node_id = Column(String(36), nullable=True)
    __table_args__ = (UniqueConstraint('concept_id', 'user_id', 'document_id', 'chunk_id', 'quote_hash', name='uq_network_source'),)


class ConceptRelation(Base):
    __tablename__ = 'network_concept_relations'
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), nullable=False, index=True)
    source_id = Column(String(68), ForeignKey('network_concepts.id', ondelete='CASCADE'), nullable=False, index=True)
    target_id = Column(String(68), ForeignKey('network_concepts.id', ondelete='CASCADE'), nullable=False)
    relation_type = Column(String(24), nullable=False)
    is_public = Column(Boolean, nullable=False, default=False)
    reviewed = Column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint('user_id', 'source_id', 'target_id', 'relation_type', name='uq_network_relation'),)


class ConceptLearning(Base):
    __tablename__ = 'network_concept_learning'
    user_id = Column(String(36), primary_key=True)
    concept_id = Column(String(68), ForeignKey('network_concepts.id', ondelete='CASCADE'), primary_key=True)
    saved = Column(Boolean, nullable=False, default=False)
    mastery = Column(String(24), nullable=False, default='unseen')
    note = Column(Text, nullable=False, default='')
