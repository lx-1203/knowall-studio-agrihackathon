"""Persistent control only; inputs and credentials never enter this table."""
from sqlalchemy import Boolean, Column, DateTime, String
from app.database import Base
from app.models.agent_run import utc_now


class AgentBackgroundTask(Base):
    __tablename__ = 'agent_background_tasks'
    run_id = Column(String(36), primary_key=True)
    user_id = Column(String(36), nullable=False, index=True)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=utc_now)
