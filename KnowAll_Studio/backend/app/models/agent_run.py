"""Durable terminal receipts; running rows are not automatically resumed."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON, CheckConstraint, Index
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AgentRun(Base):
    __tablename__='agent_runs'
    __table_args__=(
        CheckConstraint("status IN ('queued','running','waiting_user','completed','partial','failed','cancelled')",name='ck_agent_runs_status'),
        Index('ix_agent_runs_owner_conversation','user_id','conversation_id','created_at'),
    )
    id=Column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    user_id=Column(String(36),nullable=False,index=True)
    # Retain identifiers when a conversation is removed; read APIs recheck its
    # current ownership. No restrictive FK may block normal chat deletion.
    conversation_id=Column(String(36),nullable=True)
    assistant_message_id=Column(String(36),nullable=True,unique=True)
    status=Column(String(32),nullable=False)
    scope=Column(String(32),nullable=False,default='readonly_documents')
    model=Column(String(256),nullable=False)
    sources=Column(JSON,nullable=False,default=list)
    steps=Column(JSON,nullable=False,default=list)
    error_code=Column(String(64),nullable=True)
    created_at=Column(DateTime,nullable=False,default=utc_now)
    completed_at=Column(DateTime,nullable=True)
