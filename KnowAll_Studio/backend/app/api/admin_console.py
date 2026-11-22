"""Authenticated administrator console. Legacy settings routes remain compatible."""
from datetime import datetime, timedelta, timezone
from importlib import import_module
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.auth import get_current_user
from app.core.utc_time import utc_iso
from app.database import get_db
from app.models import (APICallLog, APIKey, AnswerRecord, Document, DocumentChunk,
                        Flashcard, QuestionBank, ReviewLog, User)
from app.models.system_config import SystemConfig

router = APIRouter(prefix="/api/v1/admin/console", tags=["admin-console"])


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class UserUpdate(StrictRequest):
    is_active: bool


class DocumentUpdate(StrictRequest):
    filename: str = Field(min_length=1, max_length=500)


class QuestionUpdate(StrictRequest):
    question_text: str | None = Field(default=None, min_length=1, max_length=100000)
    answer: str | None = Field(default=None, min_length=1, max_length=100000)
    analysis: str | None = Field(default=None, max_length=100000)
    difficulty: str | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def reject_empty_or_null_update(self):
        if not self.model_fields_set or any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("至少提交一个有效修改字段")
        return self


class AddKeyRequest(StrictRequest):
    provider: Literal["openai", "deepseek", "anthropic", "qwen", "zhipu", "ollama"]
    api_key: str = Field(min_length=1, max_length=256, pattern=r"^[\x21-\x7e]+$")
    key_alias: str = Field(default="", max_length=100)
    permission_level: str = Field(default="personal", min_length=1, max_length=50)


class ModelRequest(StrictRequest):
    model: str = Field(min_length=1, max_length=100)
    fallback_models: list[str] = Field(default_factory=list, max_length=20)
    daily_token_limit: int = Field(default=1000000, ge=1, le=2147483647)

    @field_validator("fallback_models")
    @classmethod
    def validate_fallbacks(cls, values):
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 100 for value in cleaned):
            raise ValueError("备用模型名称长度应为 1 至 100")
        return cleaned


def user_data(user):
    return {"id": user.id, "username": user.username, "email": user.email,
            "is_active": bool(user.is_active), "is_admin": bool(user.is_admin),
            "created_at": utc_iso(user.created_at)}


def document_data(document):
    # Arbitrary metadata can contain original content and paths: keep an allowlist.
    return {"id": document.id, "filename": document.filename, "file_type": document.file_type,
            "file_size": document.file_size, "status": document.status, "page_count": document.page_count,
            "created_at": utc_iso(document.created_at),
            "updated_at": utc_iso(document.updated_at) if document.updated_at else None}


def question_data(question, source_doc_id=None):
    return {"id": question.id, "question_text": question.question_text, "answer": question.correct_answer,
            "analysis": question.analysis, "question_type": question.question_type,
            "options": question.options or [], "difficulty": question.difficulty,
            "tags": question.tags or [], "source_doc_id": source_doc_id,
            "created_at": utc_iso(question.created_at)}


@router.get("/access")
async def access(user: User = Depends(get_current_user)):
    return {"is_admin": bool(user.is_admin)}


@router.get("/overview")
async def overview(days: int = Query(7), db: AsyncSession = Depends(get_db),
                   admin: User = Depends(require_admin)):
    if days not in (7, 30):
        raise HTTPException(422, "days 只支持 7 或 30")
    instant = datetime.now(timezone.utc)
    today = instant.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    end = today + timedelta(days=1)
    totals = {}
    for name, model in (("users", User), ("documents", Document), ("questions", QuestionBank),
                        ("flashcards", Flashcard), ("reviews", ReviewLog)):
        totals[name] = await db.scalar(select(func.count()).select_from(model)) or 0
    totals["active_users"] = await db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
    token_sum = func.coalesce(APICallLog.tokens_input, 0) + func.coalesce(APICallLog.tokens_output, 0)
    success_sum = func.sum(case((APICallLog.success.is_(True), 1), else_=0))
    period_filter = (APICallLog.created_at >= start, APICallLog.created_at < end)
    calls, successes, tokens, cost, duration = (await db.execute(select(
        func.count(), success_sum, func.sum(token_sum), func.sum(APICallLog.cost_estimate),
        func.avg(APICallLog.duration_ms)).where(*period_filter))).one()
    date_expr = func.date(APICallLog.created_at)
    daily_rows = (await db.execute(select(date_expr, func.count(), success_sum, func.sum(token_sum))
                                  .where(*period_filter).group_by(date_expr))).all()
    daily_map = {str(day): (count, success, tokens) for day, count, success, tokens in daily_rows}
    daily = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date().isoformat()
        count, success, daily_tokens = daily_map.get(day, (0, 0, 0))
        daily.append({"date": day, "calls": count, "successes": success or 0, "tokens": daily_tokens or 0})
    models = (await db.execute(select(APICallLog.model_name, func.count(), func.sum(token_sum))
                              .where(*period_filter).group_by(APICallLog.model_name)
                              .order_by(func.count().desc(), APICallLog.model_name))).all()
    types = (await db.execute(select(Document.file_type, func.count()).group_by(Document.file_type)
                             .order_by(func.count().desc(), Document.file_type))).all()
    attempts, correct = (await db.execute(select(func.count(),
        func.sum(case((AnswerRecord.is_correct.is_(True), 1), else_=0))))).one()
    return {"totals": totals,
            "period": {"days": days, "calls": calls, "successes": successes or 0, "tokens": tokens or 0,
                       "cost": cost or 0, "avg_duration_ms": round(duration or 0, 2)},
            "daily": daily, "models": [{"model": model, "calls": count, "tokens": tokens or 0} for model, count, tokens in models],
            "document_types": [{"type": kind, "count": count} for kind, count in types],
            "learning": {"attempts": attempts, "correct": correct or 0,
                         "accuracy": round(100 * (correct or 0) / attempts, 2) if attempts else 0},
            "generated_at": utc_iso(instant)}


@router.get("/users")
async def users(search: str = Query("", max_length=200), page: int = Query(1, ge=1),
                page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db),
                admin: User = Depends(require_admin)):
    query = select(User)
    if search.strip():
        value = search.strip()
        query = query.where(or_(User.username.contains(value, autoescape=True), User.email.contains(value, autoescape=True)))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await db.scalars(query.order_by(User.created_at.desc(), User.id).offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [user_data(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, req: UserUpdate, db: AsyncSession = Depends(get_db),
                      admin: User = Depends(require_admin)):
    if user_id == admin.id and not req.is_active:
        raise HTTPException(400, "不能禁用当前管理员账户")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    user.is_active = req.is_active
    await db.commit()
    return user_data(user)


@router.get("/documents")
async def documents(search: str = Query("", max_length=200), page: int = Query(1, ge=1),
                    page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db),
                    admin: User = Depends(require_admin)):
    query = select(Document)
    if search.strip():
        query = query.where(Document.filename.contains(search.strip(), autoescape=True))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await db.scalars(query.order_by(Document.created_at.desc(), Document.id).offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [document_data(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/documents/{document_id}")
async def document_detail(document_id: str, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "资料不存在")
    return document_data(document)


@router.patch("/documents/{document_id}")
async def update_document(document_id: str, req: DocumentUpdate, db: AsyncSession = Depends(get_db),
                          admin: User = Depends(require_admin)):
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "资料不存在")
    document.filename = req.filename
    await db.commit()
    await db.refresh(document)
    return document_data(document)


@router.get("/questions")
async def questions(search: str = Query("", max_length=200), page: int = Query(1, ge=1),
                    page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db),
                    admin: User = Depends(require_admin)):
    query = select(QuestionBank, DocumentChunk.doc_id).outerjoin(DocumentChunk, QuestionBank.source_chunk_id == DocumentChunk.id)
    if search.strip():
        value = search.strip()
        query = query.where(or_(QuestionBank.question_text.contains(value, autoescape=True),
                               QuestionBank.correct_answer.contains(value, autoescape=True),
                               cast(QuestionBank.tags, String).contains(value, autoescape=True),
                               cast(QuestionBank.tags, String).contains(json.dumps(value, ensure_ascii=True)[1:-1], autoescape=True)))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await db.execute(query.order_by(QuestionBank.created_at.desc(), QuestionBank.id).offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [question_data(row, source_id) for row, source_id in rows], "total": total, "page": page, "page_size": page_size}


@router.patch("/questions/{question_id}")
async def update_question(question_id: str, req: QuestionUpdate, db: AsyncSession = Depends(get_db),
                          admin: User = Depends(require_admin)):
    question = await db.get(QuestionBank, question_id)
    if question is None:
        raise HTTPException(404, "题目不存在")
    for key, value in req.model_dump(exclude_unset=True).items():
        setattr(question, "correct_answer" if key == "answer" else key, value)
    await db.commit()
    source_id = await db.scalar(select(DocumentChunk.doc_id).where(DocumentChunk.id == question.source_chunk_id)) if question.source_chunk_id else None
    return question_data(question, source_id)


def _legacy_admin():
    # Keep scheduler/vector dependencies out of business routes until needed.
    return import_module("app.api.admin")


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    from sqlalchemy import text
    from app.core.admin_console_runtime import scheduler_status
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok", "timestamp": utc_iso(datetime.now(timezone.utc)),
            "api_scheduler": scheduler_status()}


@router.get("/logs")
async def logs(limit: int = Query(50, ge=1, le=500), db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    return await _legacy_admin().usage_logs(limit=limit, db=db)


@router.get("/keys")
async def keys(db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    rows = (await db.scalars(select(APIKey).order_by(APIKey.created_at.desc(), APIKey.id))).all()
    return [{"id": key.id, "provider": key.provider, "key_alias": key.key_alias,
             "permission_level": key.permission_level, "key_masked": "********",
             "is_active": bool(key.is_active)} for key in rows]


@router.post("/keys")
async def add_key(req: AddKeyRequest, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    from app.core.crypto import encrypt_api_key
    from app.core.admin_console_runtime import sync_provider_keys
    encrypted = await run_in_threadpool(encrypt_api_key, req.api_key)
    key = APIKey(provider=req.provider, key_encrypted=encrypted, key_alias=req.key_alias,
                 permission_level=req.permission_level)
    db.add(key)
    await db.commit()
    await sync_provider_keys(db, key.provider, key.user_id)
    return {"status": "added", "provider": key.provider, "key_id": key.id}


@router.delete("/keys/{key_id}")
async def delete_key(key_id: str, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    from app.core.admin_console_runtime import PROVIDERS, sync_provider_keys
    key = await db.get(APIKey, key_id)
    if key is None:
        raise HTTPException(404, "密钥不存在")
    provider, owner = key.provider, key.user_id
    await db.delete(key)
    await db.flush()
    if provider in PROVIDERS:
        await validate_active_key(db, provider, owner)
    await db.commit()
    if provider in PROVIDERS:
        await sync_provider_keys(db, provider, owner)
    return {"status": "deleted"}


@router.put("/keys/{key_id}/toggle")
async def toggle_key(key_id: str, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    from app.core.admin_console_runtime import PROVIDERS, sync_provider_keys
    key = await db.get(APIKey, key_id)
    if key is None:
        raise HTTPException(404, "密钥不存在")
    if key.provider not in PROVIDERS:
        raise HTTPException(422, "该历史服务商不支持启停，可以删除后改用支持的服务商")
    key.is_active = not key.is_active
    if key.is_active:
        await validate_key_ciphertext(key.key_encrypted)
    await db.flush()
    await validate_active_key(db, key.provider, key.user_id)
    await db.commit()
    await sync_provider_keys(db, key.provider, key.user_id)
    return {"status": "updated", "key_id": key.id, "is_active": bool(key.is_active)}


async def validate_active_key(db, provider, owner):
    """Validate the new winner before committing a potentially unrecoverable write."""
    active = await db.scalar(select(APIKey).where(APIKey.provider == provider,
        APIKey.user_id == owner, APIKey.is_active.is_(True))
        .order_by(APIKey.created_at.desc(), APIKey.id.desc()).limit(1))
    if active is not None:
        await validate_key_ciphertext(active.key_encrypted)


async def validate_key_ciphertext(ciphertext):
    from app.core.crypto import decrypt_api_key
    try:
        await run_in_threadpool(decrypt_api_key, ciphertext)
    except ValueError:
        raise HTTPException(422, "密钥无法解密，请删除损坏密钥并重新添加")


@router.get("/models")
async def model_config(db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    from app.config import settings
    row = await db.scalar(select(SystemConfig).where(SystemConfig.key == "model_config"))
    if row:
        try:
            config = ModelRequest.model_validate(json.loads(row.value)).model_dump()
        except (ValueError, TypeError):
            raise HTTPException(500, "已存储模型配置无效，请重新保存")
    else:
        config = {"model": settings.default_model, "fallback_models": [], "daily_token_limit": settings.daily_token_limit}
    return {**config, "scope": "default_requests", "quota_scope": "new_accounts_without_custom_quota"}


@router.post("/models")
async def save_model(req: ModelRequest, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    from app.core.admin_console_runtime import apply_model_config
    row = await db.scalar(select(SystemConfig).where(SystemConfig.key == "model_config"))
    value = json.dumps(req.model_dump(), ensure_ascii=False)
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key="model_config", value=value, description="Administrator default generation settings"))
    await db.commit()
    await apply_model_config(db)
    return {"status": "configured", **req.model_dump(), "scope": "default_requests",
            "quota_scope": "new_accounts_without_custom_quota"}


@router.get("/cache/stats")
async def cache_stats(admin: User = Depends(require_admin)):
    return await _legacy_admin().cache_stats()


@router.delete("/cache/clear")
async def clear_cache(db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    return await _legacy_admin().clear_cache(db=db)


@router.get("/quota/status")
async def quota_status(admin: User = Depends(require_admin)):
    return await _legacy_admin().quota_status(current_user=admin)
