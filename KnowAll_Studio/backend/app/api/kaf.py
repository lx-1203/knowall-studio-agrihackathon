"""KAF 加密资料 API:申请授权(核心)/ 会话密钥 / 吊销 / 发布(管理)。

客户端打开 .kaf 流程:
  1. GET  /api/v1/kaf/files                 → 资料列表(公开元数据)
  2. GET  /api/v1/kaf/files/{file_id}       → 资料详情(元数据)
  3. GET  /api/v1/kaf/files/{file_id}/download → 下载 .kaf 实体(登录 + 档位校验)
  4. POST /api/v1/kaf/files/{file_id}/grant → 申请内容密钥(校验会员档位, 24h 有效)
  5. 客户端用「内容密钥」本地 AES 解密 .kaf(0ms 网络, 渲染不卡)
"""
import logging
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.auth import get_current_user
from app.core.kaf_service import KAFKeyUnavailableError, KAFContentUnavailableError, get_kaf_service, publication_path
from app.database import get_db
from app.models.kaf import KafFile
from app.models.subscription import UserTier
from app.models.user import User

logger = logging.getLogger("knowall.kaf.api")
router = APIRouter(prefix="/api/v1/kaf", tags=["kaf"])


class _RecoverableKAFFileResponse(FileResponse):
    """Keep native Range/HEAD delivery, recover stat faults before headers."""

    async def __call__(self, scope, receive, send):
        started = False

        async def tracked_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)

        try:
            await super().__call__(scope, receive, tracked_send)
            return
        except RuntimeError as exc:
            # Starlette 1.2.0 wraps only its missing-file stat failure this way.
            if started or not isinstance(exc.__context__, FileNotFoundError):
                raise
            status, detail, headers = 404, '文件数据缺失', None
        except OSError:
            if started:
                raise
            status, detail, headers = 503, '资料文件服务暂不可用，请稍后重试或联系管理员', {'Retry-After':'5'}
        from fastapi.responses import JSONResponse
        await JSONResponse({'detail':detail}, status_code=status, headers=headers)(scope, receive, send)


def _key_unavailable():
    return HTTPException(503, "资料密钥服务暂不可用，请稍后重试或联系管理员恢复原密钥",
                         headers={"Retry-After": "5"})


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _user_tier(user_id: str, db: AsyncSession) -> str:
    """查询用户当前订阅档位(free 兜底)。"""
    result = await db.execute(select(UserTier).where(UserTier.user_id == user_id))
    tier = result.scalar_one_or_none()
    if tier is None:
        return "free"
    if tier.expires_at and tier.expires_at.replace(tzinfo=None) < _now_naive():
        return "free"
    return tier.tier or "free"


async def _get_existing(db: AsyncSession, file_id: str) -> KafFile:
    result = await db.execute(select(KafFile).where(KafFile.file_id == file_id))
    f = result.scalar_one_or_none()
    if f is None:
        raise HTTPException(404, "文件不存在")
    return f


async def _get_published(db: AsyncSession, file_id: str) -> KafFile:
    f = await _get_existing(db, file_id)
    if not f.is_published:
        raise HTTPException(404, "文件不存在")
    return f


# ---------- 公开: 资料列表/详情 ----------

@router.get("/files")
async def list_files(limit: int = 50, topic: str = "", db: AsyncSession = Depends(get_db)):
    """公开资料库列表(已发布资料, 只含元数据)。"""
    stmt = select(KafFile).where(KafFile.is_published == True)  # noqa: E712
    if topic:
        stmt = stmt.where(KafFile.topic == topic)
    result = await db.execute(stmt.limit(limit))
    rows = result.scalars().all()
    return {
        "items": [
            {
                "file_id": f.file_id,
                "title": f.title,
                "topic": f.topic,
                "file_size": f.file_size,
                "node_count": f.node_count,
                "created_at": f.created_at.isoformat(),
            }
            for f in rows
        ],
        "total": len(rows),
    }


@router.get("/files/{file_id}")
async def get_file(file_id: str, db: AsyncSession = Depends(get_db)):
    """资料详情(公开元数据)。"""
    f = await _get_published(db, file_id)
    return {
        "file_id": f.file_id,
        "title": f.title,
        "topic": f.topic,
        "file_size": f.file_size,
        "node_count": f.node_count,
        "created_at": f.created_at.isoformat(),
    }


# ---------- 登录: 下载 / 授权 ----------

@router.get("/files/{file_id}/download")
async def download_file(file_id: str, current_user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """下载 .kaf 实体。零信任: 下载即校验档位(防绕过客户端直接拉文件)。"""
    f = await _get_published(db, file_id)
    service = get_kaf_service()
    tier = await _user_tier(current_user.id, db)
    if not service.tier_allows(f, tier):
        raise HTTPException(403, "当前会员等级无权下载该资料")
    if await service._has_revocation(db, file_id, current_user.id):
        raise HTTPException(403, "该资料授权已被管理员吊销")

    try:
        path = await run_in_threadpool(publication_path, f.file_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "资料文件服务暂不可用，请稍后重试或联系管理员",
                            headers={"Retry-After": "5"}) from exc
    return _RecoverableKAFFileResponse(
        str(path),
        media_type="application/octet-stream",
        filename=f"{f.title}.kaf",
    )


@router.post("/files/{file_id}/grant")
async def grant_content_key(file_id: str, current_user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    """申请内容密钥(核心): 校验会员档位后返回, 24h 内同账号缓存。

    MVP 客户端把密钥仅驻内存(不落明文), 会员过期/吊销后 grant 自然拒绝。
    """
    service = get_kaf_service()
    tier = await _user_tier(current_user.id, db)
    try:
        payload = await service.grant(db, file_id, current_user.id, tier)
    except KAFKeyUnavailableError as exc:
        raise _key_unavailable() from exc
    except KAFContentUnavailableError as exc:
        raise HTTPException(503, "资料文件服务暂不可用，请稍后重试或联系管理员",
                            headers={"Retry-After": "5"}) from exc
    except LookupError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return payload


@router.post("/files/{file_id}/check")
async def check_access(file_id: str, current_user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """客户端离线打开前二次确认: 授权是否仍有效(吊销/过期则失败)。"""
    f = await _get_published(db, file_id)
    service = get_kaf_service()
    tier = await _user_tier(current_user.id, db)
    if not service.tier_allows(f, tier):
        raise HTTPException(403, "当前会员等级无权访问该资料")
    ok, info = await service.check_active_grant(db, file_id, current_user.id)
    if not ok:
        raise HTTPException(403, "授权已失效，请重新申请")
    return info


# ---------- 管理: 发布 / 吊销 / 恢复 ----------

def require_kaf_admin(current_user: User = Depends(get_current_user)) -> User:
    """Only database administrators may call KAF management routes."""
    if not current_user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return current_user


admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_kaf_admin)])


class KAFPublishRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore')
    markdown: str | None = ''
    topic: str = Field(default='', max_length=64)
    title: str = Field(default='', max_length=255)
    source_doc_id: str | None = Field(default=None, max_length=64)
    source_doc_filename: str = Field(default='', max_length=500)
    enabled_tiers: str = Field(default='free,pro,enterprise', max_length=200)


@admin_router.post("/publish", status_code=201)
async def admin_publish(body: KAFPublishRequest, db: AsyncSession = Depends(get_db)):
    """管理员发布: 传 markdown 原文 → 生成 .kaf 并登记。"""
    md_text = (body.markdown or "").strip()
    if not md_text:
        raise HTTPException(400, "markdown 内容不能为空")
    service = get_kaf_service()
    try:
        kf = await run_in_threadpool(service.publish_from_markdown,
            md_text,
            topic=body.topic,
            title=body.title,
            source_doc_id=body.source_doc_id,
            source_doc_filename=body.source_doc_filename,
            enabled_tiers=body.enabled_tiers,
        )
    except KAFKeyUnavailableError as exc:
        raise _key_unavailable() from exc
    from app.core.kaf_service import register_publication, KAFSourceConflictError, KAFPublicationUncertainError
    try:
        await register_publication(db, kf)
    except KAFSourceConflictError as exc:
        raise HTTPException(409, '该来源已有发布资料') from exc
    except KAFPublicationUncertainError as exc:
        raise HTTPException(503, {
            'code': 'publication_outcome_unknown',
            'message': '发布结果暂无法确认，请先查询资料状态，避免重复发布',
            'file_id': exc.file_id,
            'status_url': f'/api/v1/kaf/files/{exc.file_id}',
        }) from exc
    await db.refresh(kf)
    logger.info("KAF published: %s (%s, %d B)", kf.file_id, kf.title, kf.file_size)
    return {"file_id": kf.file_id, "title": kf.title, "size": kf.file_size}


@admin_router.post("/{file_id}/revoke")
async def admin_revoke(file_id: str, body: dict | None = None,
                       db: AsyncSession = Depends(get_db)):
    """吊销授权。body 可选 user_id；已下发的离线密钥无法由服务器收回。"""
    await _get_existing(db, file_id)
    service = get_kaf_service()
    user_id = (body or {}).get("user_id")
    count = await service.revoke(db, file_id, user_id)
    return {"revoked": count}


@admin_router.post("/{file_id}/unpublish")
async def admin_unpublish(file_id: str, db: AsyncSession = Depends(get_db)):
    """下架: grant/download 立即拒绝, 已授权客户端 check 也会失败。"""
    f = await _get_published(db, file_id)
    f.is_published = False
    await db.commit()
    service = get_kaf_service()
    await service.revoke(db, file_id)
    return {"detail": "已下架并吊销全部授权"}


@admin_router.post("/{file_id}/restore")
async def admin_restore(file_id: str, body: dict | None = None,
                        db: AsyncSession = Depends(get_db)):
    """恢复先前吊销的授权。body 可选 user_id；离线密钥仍不能被服务器收回。"""
    await _get_existing(db, file_id)
    service = get_kaf_service()
    user_id = (body or {}).get("user_id")
    count = await service.restore(db, file_id, user_id)
    return {"restored": count}


router.include_router(admin_router)
