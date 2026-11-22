"""Opt-in encrypted input, separate from the four-column background control."""
from sqlalchemy import Column, DateTime, Integer, String, Text, CheckConstraint
from app.database import Base
from app.models.agent_run import utc_now


class AgentInputCheckpoint(Base):
    __tablename__ = 'agent_input_checkpoints'
    __table_args__ = (CheckConstraint('version = 1', name='ck_agent_input_checkpoints_version'),)
    run_id = Column(String(36), primary_key=True)
    user_id = Column(String(36), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    input_ciphertext = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now)
