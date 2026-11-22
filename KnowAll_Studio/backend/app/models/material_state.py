"""Private material fields and durable operation receipts; independent note/position revisions."""
from sqlalchemy import Column, String, Integer, JSON, DateTime
from app.database import Base

class MaterialState(Base):
    __tablename__ = 'material_states'
    user_id = Column(String(36), primary_key=True)
    document_id = Column(String(36), primary_key=True)
    kind = Column(String(16), primary_key=True)
    revision = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False)
    source_version = Column(String(64), nullable=True)
    updated_at = Column(DateTime, nullable=False)

class MaterialStateOperation(Base):
    __tablename__ = 'material_state_operations'
    user_id = Column(String(36), primary_key=True)
    document_id = Column(String(36), primary_key=True)
    kind = Column(String(16), primary_key=True)
    operation_id = Column(String(80), primary_key=True)
    request_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)
