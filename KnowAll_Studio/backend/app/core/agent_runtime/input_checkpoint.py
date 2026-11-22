"""Explicit encrypted snapshots only. Reading never executes or recreates a key."""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from app.core.crypto import get_fernet
from app.models.agent_input_checkpoint import AgentInputCheckpoint
from app.models import Conversation


class InputUnavailable(RuntimeError):
    pass


class InputNotOwned(RuntimeError):
    pass


class HistoryItem(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    role: Literal['user', 'assistant']
    content: str = Field(max_length=8000)


class InputPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    version: Literal[1]
    run_id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=36)
    scope: Literal['readonly_documents']
    message: str = Field(min_length=1, max_length=8000)
    requested_model: str = Field(min_length=1, max_length=256)
    original_conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    history: list[HistoryItem] = Field(max_length=20)


def bounded_history(history):
    # Retain conversation text only: tool objects, user credentials, adapters,
    # and unrecognized metadata can never enter this encrypted envelope.
    items=[]; remaining=32000
    for item in reversed(history):
        if not isinstance(item,dict) or item.get('role') not in ('user','assistant') or not isinstance(item.get('content'),str):
            continue
        content=item['content'][:min(8000,remaining)]
        items.append({'role':item['role'],'content':content});remaining-=len(content)
        if len(items)==20 or remaining==0:break
    return list(reversed(items))


def create_checkpoint(run_id,user_id,conversation_id,model,message,history):
    try:
        payload=InputPayload(version=1,run_id=run_id,user_id=user_id,scope='readonly_documents',
            message=message,requested_model=model,original_conversation_id=conversation_id,history=bounded_history(history))
        ciphertext=get_fernet().encrypt(payload.model_dump_json().encode('utf-8')).decode('ascii')
        return AgentInputCheckpoint(run_id=run_id,user_id=user_id,version=1,input_ciphertext=ciphertext)
    except (RuntimeError,ValidationError,OSError,ValueError) as error:
        raise InputUnavailable('Input checkpoint could not be encrypted') from error


def is_missing_table(error):
    message=str(error).lower()
    return 'agent_input_checkpoints' in message and any(marker in message for marker in (
        'no such table','does not exist',"doesn't exist",'undefinedtable'))


async def metadata(db,run_ids,user_id):
    # Deliberately select identifiers only. Lists never fetch or decrypt tokens.
    return set((await db.scalars(select(AgentInputCheckpoint.run_id).where(
        AgentInputCheckpoint.run_id.in_(run_ids),AgentInputCheckpoint.user_id==user_id))).all())


async def load_owned(db,row,user_id):
    cp=await db.scalar(select(AgentInputCheckpoint).where(
        AgentInputCheckpoint.run_id==row.id,AgentInputCheckpoint.user_id==user_id))
    if cp is None:return None,None
    try:
        raw=get_fernet(create=False).decrypt(cp.input_ciphertext.encode('ascii'))
        if len(raw)>160000:raise ValueError('Oversized payload')
        decoded=json.loads(raw)
        if not isinstance(decoded,dict) or type(decoded.get('version')) is not int:
            raise ValueError('Invalid version')
        payload=InputPayload.model_validate(decoded)
        if (cp.version!=1 or payload.version!=cp.version or payload.run_id!=cp.run_id or
                payload.run_id!=row.id or payload.user_id!=cp.user_id or payload.user_id!=user_id or
                row.user_id!=user_id or payload.scope!=row.scope or not payload.message.strip() or
                not payload.requested_model.strip() or sum(len(item.content) for item in payload.history)>32000):
            raise ValueError('Checkpoint binding mismatch')
    except (RuntimeError,InvalidToken,ValidationError,ValueError,TypeError,UnicodeError) as error:
        raise InputUnavailable('Input checkpoint cannot be read') from error
    if payload.original_conversation_id:
        original=await db.scalar(select(Conversation.id).where(
            Conversation.id==payload.original_conversation_id,Conversation.user_id==user_id))
        if original is None:raise InputNotOwned('Original conversation unavailable')
    return cp,payload
