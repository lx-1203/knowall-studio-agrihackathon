"""Authentication API: register, login, profile, password reset"""
import re
import html
import time
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, field_validator

from app.database import get_db
from app.core.utc_time import utc_iso
from app.config import settings
from app.models.user import User
from app.core.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Simple in-memory rate limiter for auth endpoints
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 10      # max attempts per window


def _check_rate_limit(key: str) -> None:
    """Check and update rate limit. Raises 429 if exceeded."""
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW
    # Prune old entries
    _rate_limit_store[key] = [t for t in _rate_limit_store[key] if t > window_start]
    if len(_rate_limit_store[key]) >= _RATE_LIMIT_MAX:
        raise HTTPException(429, "操作过于频繁，请稍后再试")
    _rate_limit_store[key].append(now)

# ---- Request / Response Schemas ----

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fff]{2,50}$")
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    confirm_password: str = ""

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if not _USERNAME_RE.match(v):
            raise ValueError("用户名需 2-50 字符，仅支持中英文、数字、下划线")
        return html.escape(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("邮箱格式不正确")
        if len(v) > 255:
            raise ValueError("邮箱长度不能超过 255 字符")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("密码不能少于 8 个字符")
        if len(v.encode("utf-8")) > 72:
            raise ValueError("密码的 UTF-8 长度不能超过 72 字节")
        return v

    @field_validator("confirm_password")
    @classmethod
    def validate_confirm(cls, v: str, info) -> str:
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("两次输入的密码不一致")
        return v


class CheckUsernameRequest(BaseModel):
    username: str


class LoginRequest(BaseModel):
    username: str   # 可以是用户名或邮箱
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


# ---- Routes ----

class LocalSessionRequest(BaseModel):
    user_id: str | None = None


@router.post("/local-session")
async def local_session(req: LocalSessionRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Create a normal JWT session only for explicitly enabled local launchers."""
    if settings.desktop_require_login or not settings.local_auto_login or not settings.debug:
        return {"enabled": False}
    loopback = {"localhost", "127.0.0.1", "::1"}
    origin = request.headers.get("origin")
    try:
        parsed_origin = urlsplit(origin) if origin is not None else None
        valid_origin = parsed_origin is None or (
            parsed_origin.hostname in loopback and parsed_origin.scheme in {"http", "https"}
        )
    except ValueError:
        valid_origin = False
    if (
        not request.client or request.client.host not in loopback
        or request.url.hostname not in loopback
        or request.headers.get("x-knowall-local") != "1"
        or not valid_origin
    ):
        raise HTTPException(403, "本地会话仅允许本机访问")

    user = await db.get(User, req.user_id) if req.user_id else None
    if user is not None and not user.is_active:
        raise HTTPException(403, "账户已被禁用")
    if user is None:
        result = await db.execute(
            select(User).where(User.is_active == True).order_by(User.created_at, User.id).limit(1)
        )
        user = result.scalar_one_or_none()
    if user is None:
        user = await db.get(User, "local_user")
        if user is not None and not user.is_active:
            raise HTTPException(403, "本地账户已被禁用")
    if user is None:
        user = User(
            id="local_user", username="本地用户", email="local@knowall.invalid",
            password_hash=hash_password(secrets.token_urlsafe(48)),
            is_active=True, is_admin=False,
        )
        db.add(user)
        try:
            await db.commit()
            await db.refresh(user)
        except IntegrityError:
            # A second tab may have created the fixed local account first.
            await db.rollback()
            user = await db.get(User, "local_user")
            if user is None:
                raise HTTPException(409, "本地账户名称冲突，请检查现有账户")
            if not user.is_active:
                raise HTTPException(403, "本地账户已被禁用")
    return {
        "enabled": True, "access_token": create_access_token(user.id), "token_type": "bearer",
        "user": {"id": user.id, "username": user.username, "email": user.email,
                 "created_at": utc_iso(user.created_at)},
    }

@router.post("/register", status_code=201)
async def register(req: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """注册新用户。用户名和邮箱全局唯一。"""
    _check_rate_limit(f"register:{request.client.host}")

    # 检查用户名是否已存在
    existing = await db.execute(
        select(User).where(User.username == req.username)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="用户名已被注册")

    # 检查邮箱是否已存在
    existing = await db.execute(
        select(User).where(User.email == req.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该邮箱已被注册")

    # 创建用户，密码 bcrypt 哈希，生成邮箱验证令牌
    verification_token = secrets.token_urlsafe(32)
    user = User(
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
        verification_token=verification_token,
        email_verified=False,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent registrations may pass both availability checks before
        # either insert commits. Keep the database uniqueness rule authoritative.
        await db.rollback()
        raise HTTPException(status_code=409, detail="用户名或邮箱已被注册") from None
    await db.refresh(user)

    # Never put verification credentials in application logs.
    import logging
    _logger = logging.getLogger("knowall.auth")
    _logger.info("User %s registered", user.id)

    # 注册成功直接返回 token（自动登录）
    token = create_access_token(user.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "email_verified": False,
            "created_at": utc_iso(user.created_at),
        },
    }


class VerifyEmailRequest(BaseModel):
    token: str


@router.post("/verify-email")
async def verify_email(req: VerifyEmailRequest, db: AsyncSession = Depends(get_db)):
    """Verify user email using the verification token."""
    result = await db.execute(
        select(User).where(User.verification_token == req.token.strip())
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(400, "无效的验证链接")
    if user.email_verified:
        return {"detail": "邮箱已验证，无需重复验证"}

    user.email_verified = True
    user.verification_token = None  # Clear token after use
    await db.commit()

    return {"detail": "邮箱验证成功"}


@router.post("/resend-verification")
async def resend_verification(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resend email verification token."""
    if current_user.email_verified:
        return {"detail": "邮箱已验证，无需重新发送"}

    verification_token = secrets.token_urlsafe(32)
    current_user.verification_token = verification_token
    await db.commit()

    import logging
    _logger = logging.getLogger("knowall.auth")
    _logger.info("Email verification requested for user %s", current_user.id)

    return {"detail": "验证邮件已重新发送，请查收邮箱"}


@router.post("/login")
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """登录：支持用户名或邮箱 + 密码"""
    _check_rate_limit(f"login:{request.client.host}")

    login_id = req.username.strip().lower()

    # 按用户名或邮箱查找
    result = await db.execute(
        select(User).where(
            (User.username == req.username.strip()) | (User.email == login_id)
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="账户已被禁用，请联系管理员")

    if not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(user.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "created_at": utc_iso(user.created_at),
        },
    }


@router.get("/me")
async def get_profile(current_user: User = Depends(get_current_user)):
    """获取当前登录用户信息（需携带 Bearer token）"""
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "phone": current_user.phone or "",
        "nickname": current_user.nickname or "",
        "is_active": bool(current_user.is_active),
        "created_at": utc_iso(current_user.created_at),
        "updated_at": utc_iso(current_user.updated_at) if current_user.updated_at else None,
    }


# ---- Username Availability ----

@router.post("/check-username")
async def check_username(req: CheckUsernameRequest, db: AsyncSession = Depends(get_db)):
    """Check if a username is available (doesn't exist yet)."""
    username = req.username.strip()
    if not _USERNAME_RE.match(username):
        return {"available": False, "reason": "用户名格式不正确（2-50字符，中英文、数字、下划线）"}
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none():
        return {"available": False, "reason": "用户名已被注册"}
    return {"available": True}


# ---- Password Reset ----

# In-memory token store (use Redis or DB in production)
_reset_tokens: dict[str, dict] = {}
_RESET_TOKEN_EXPIRE_MINUTES = 30


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Prepare a reset token; email delivery requires a configured mail service."""
    _check_rate_limit(f"forgot:{req.email}")

    result = await db.execute(select(User).where(User.email == req.email.strip().lower()))
    user = result.scalar_one_or_none()

    # Always return success to prevent email enumeration
    if not user:
        return {"detail": "如果该邮箱已注册，重置链接已发送"}

    # Generate reset token
    token = secrets.token_urlsafe(32)
    _reset_tokens[token] = {
        "user_id": user.id,
        "email": user.email,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=_RESET_TOKEN_EXPIRE_MINUTES),
    }

    # Email delivery is not implemented. Do not expose reset credentials in logs.
    import logging
    logger = logging.getLogger("knowall.auth")
    logger.info("Password reset requested for user %s", user.id)

    return {"detail": "如果该邮箱已注册，重置链接已发送"}


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Reset password using a valid reset token."""
    token_data = _reset_tokens.get(req.token)
    if not token_data:
        raise HTTPException(400, "无效或已过期的重置链接")

    expires = token_data["expires_at"]
    # Handle both aware and naive datetimes
    now = datetime.now(timezone.utc)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if now > expires:
        _reset_tokens.pop(req.token, None)
        raise HTTPException(400, "重置链接已过期，请重新申请")

    if len(req.new_password) < 8:
        raise HTTPException(400, "密码不能少于 8 个字符")
    if len(req.new_password.encode("utf-8")) > 72:
        raise HTTPException(400, "密码的 UTF-8 长度不能超过 72 字节")

    result = await db.execute(select(User).where(User.id == token_data["user_id"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "用户不存在")

    user.password_hash = hash_password(req.new_password)
    await db.commit()

    # Clean up used token
    _reset_tokens.pop(req.token, None)

    return {"detail": "密码重置成功，请使用新密码登录"}
