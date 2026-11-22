"""KAF 服务核心:密钥管理 / 文件发布 / 授权签发(token-grant 模型)。

流程:
  1. 首次启动自动生成/加载「账号密钥对」(私钥 Fernet 加密落盘, 公钥上游给发布管道)
  2. publish(): 用账号公钥把 MD/内容包 打包成 .kaf, 写入 data/documents/kaf/, 登记 kaf_files
  3. grant(): 校验登录 + 会员档位 + 有效期，返回明文内容密钥和有效期
     已交付客户端的密钥无法离线吊销
  4. revoke(): 阻止后续 grant/check

密钥会话缓存: 同账号同文件 5 分钟内重复 grant 直接命中内存缓存(省一次 RSA);
跨进程重启则回 DB 查 kaf_grants 有效记录。
"""
import logging
import hashlib
import os
from datetime import timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import DATA_DIR, settings
from pathlib import Path, PurePosixPath, PureWindowsPath
from app.core.crypto import get_fernet
from app.models.kaf import KafFile, KafGrant, utcnow

logger = logging.getLogger("knowall.kaf")

KAF_DIR = Path(settings.document_dir) / "kaf"
_KEY_DIR = DATA_DIR / "kaf_keys"
_PRIV_KEY_FILE = _KEY_DIR / "account_priv.pem.enc"
_PUB_KEY_FILE = _KEY_DIR / "account_pub.pem"
GRANT_TTL_HOURS = 24        # 授权有效期(会员过期/吊销前的最大窗口)
GRANT_MEMO_TTL_SECONDS = 300  # 同账号同文件 grant 内存缓存 5 分钟
DEFAULT_TIERS = ("free", "pro", "enterprise")


class KAFKeyUnavailableError(RuntimeError):
    """Account key storage is unavailable; retry after restoring original files."""


class KAFContentUnavailableError(RuntimeError):
    """Authorized content cannot be read because of a storage outage."""


class KAFSourceConflictError(RuntimeError):
    """The database already contains a publication claiming this source."""


class KAFPublicationUncertainError(RuntimeError):
    """Keep the generated identity when a commit outcome cannot be proved."""

    def __init__(self, file_id: str):
        self.file_id = file_id
        super().__init__("发布结果暂无法确认，请先查询资料状态")


def publication_path(file_id: str) -> Path:
    """Resolve the current content mount, never a publisher's historic path."""
    if (not file_id or file_id in {'.', '..'} or '\x00' in file_id
            or PurePosixPath(file_id).name != file_id
            or PureWindowsPath(file_id).name != file_id):
        raise LookupError("文件身份无效，请联系管理员")
    root = KAF_DIR.resolve()
    path = (root / f"{file_id}.kaf").resolve()
    if path.parent != root:
        raise LookupError("文件位置无效，请联系管理员")
    return path


def _read_publication(file_id: str, digest: str) -> bytes:
    data = publication_path(file_id).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise LookupError("文件数据损坏，请联系管理员")
    return data


def _write_publication(path: Path, data: bytes):
    # Opening outside the cleanup block protects an existing published file.
    handle = path.open("xb")
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("无法清理本次未完成的 KAF 写入")
        raise


def _discard_unregistered(path: Path, file_id: str, digest: str):
    # Never clean historical files, foreign paths, or changed content.
    try:
        if path.resolve().parent != KAF_DIR.resolve() or path.name != f"{file_id}.kaf":
            return
        if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            path.unlink()
    except OSError:
        logger.exception("无法清理本次数据库拒绝的 KAF 文件")


async def register_publication(db: AsyncSession, kf: KafFile):
    """Register an exclusively created file; commit errors have unknown outcome."""
    from sqlalchemy import inspect
    from sqlalchemy.exc import IntegrityError, DBAPIError, SQLAlchemyError
    from sqlalchemy.ext.asyncio import AsyncEngine

    path, file_id, digest = Path(kf.storage_path), kf.file_id, kf.sha256
    # begin_nested() flushes existing caller work before creating its savepoint.
    # At this point a transient KAF object has not been added, so a failure
    # cannot have registered this new file. Never apply this to attached or
    # persisted objects, and leave the caller's failed transaction untouched.
    is_new = inspect(kf).transient
    source_claim = (kf.source_doc_id or None) if is_new else kf.source_claim
    if is_new:
        kf.source_claim = source_claim
    try:
        transaction = await db.begin_nested()
    except Exception:
        if is_new:
            await run_in_threadpool(_discard_unregistered, path, file_id, digest)
        raise
    db.add(kf)
    try:
        # Native flush detects constraints before any COMMIT is issued.
        await db.flush()
    except Exception as exc:
        try:
            await transaction.rollback()
        except Exception:
            logger.exception("KAF 发布回滚失败，保留文件供恢复")
        else:
            if is_new:
                await run_in_threadpool(_discard_unregistered, path, file_id, digest)
            # Classify only native UNIQUE violations, never NOT NULL/FK failures.
            # MySQL current read sees a competing commit beyond an old RR snapshot.
            unique_error = isinstance(exc, IntegrityError) and (
                getattr(exc.orig, 'sqlite_errorcode', None) == 2067 or
                (db.bind.dialect.name == 'mysql' and exc.orig.args and exc.orig.args[0] == 1062)
            )
            if is_new and unique_error and source_claim:
                existing = await db.scalar(select(KafFile.file_id).where(
                    KafFile.source_claim == source_claim).with_for_update().limit(1))
                if existing is not None:
                    raise KAFSourceConflictError('该来源已有发布资料') from exc
        raise
    # Keep the savepoint open until the outer commit. Releasing it separately
    # can itself commit on SQLite's legacy transaction mode. A lost COMMIT
    # response can mean success; do not unlink on this exception.
    # Snapshot only after native flush has assigned defaults and the primary key.
    # Timestamps can lose fractional precision on MySQL; compare the actual
    # publication identity/content/access metadata rather than clock formatting.
    fields = ('id', 'file_id', 'sha256', 'source_claim', 'source_doc_id',
              'storage_path', 'title', 'topic', 'enabled_tiers', 'is_published',
              'file_size', 'node_count', 'source_doc_filename')
    expected = tuple(getattr(kf, name) for name in fields)
    try:
        await db.commit()
    except (ConnectionError, DBAPIError) as exc:
        if isinstance(exc, DBAPIError) and not exc.connection_invalidated:
            raise
        # Never reset a failed caller transaction or expire its business objects.
        # A connection-bound session may join an external, uncommitted transaction.
        # Only a healthy, closed engine-bound transaction can release a slot and
        # obtain an independent, committed read without a pool-one deadlock.
        if is_new and db.is_active and not db.in_transaction() and isinstance(db.bind, AsyncEngine):
            columns = [getattr(KafFile, name) for name in fields]
            try:
                async with AsyncSession(bind=db.bind) as confirmation:
                    row = (await confirmation.execute(select(*columns).where(
                        KafFile.id == expected[0]))).first()
                if row is not None and tuple(row) == expected:
                    await db.refresh(kf)
                    if tuple(getattr(kf, name) for name in fields) == expected:
                        logger.info('KAF 提交响应丢失，已核实资料登记: %s', file_id)
                        return
            except (SQLAlchemyError, ConnectionError):
                logger.warning('KAF 提交结果核查不可用: %s', file_id)
        raise KAFPublicationUncertainError(file_id) from exc


class KAFService:
    """KAF 服务:单例, 持账号密钥对 + 内存授权缓存。"""

    def __init__(self):
        self._private_key = None
        self._public_key = None
        self._grant_cache: dict[tuple, dict] = {}

    # ---------- 账号密钥对 ----------

    def _load_or_create_keypair(self):
        from filelock import Timeout
        from cryptography.exceptions import UnsupportedAlgorithm
        from cryptography.fernet import InvalidToken

        try:
            self._load_keypair_files()
        except (OSError, ValueError, InvalidToken, Timeout, UnsupportedAlgorithm) as exc:
            raise KAFKeyUnavailableError("KAF账号密钥不可用，请恢复原密钥文件后重试") from exc

    def _load_keypair_files(self):
        """加载(或首次生成)账号密钥对; 私钥 Fernet 加密落盘。"""
        if self._private_key is not None and self._public_key is not None:
            return
        from filelock import FileLock
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from app.core.secret_file import load_or_create_secret

        _KEY_DIR.mkdir(parents=True, exist_ok=True)
        # The encrypted private file is authoritative. A missing public file can
        # be recovered from it, including a failed first publication.
        with FileLock(str(_KEY_DIR / "account_keypair.lock"), timeout=10):
            if not _PRIV_KEY_FILE.exists() and _PUB_KEY_FILE.exists():
                raise KAFKeyUnavailableError("KAF账号私钥缺失，请恢复原私钥文件后重试")

            def create_private():
                from kaf.keys import generate_keypair
                private, _ = generate_keypair(4096)
                pem = private.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
                try:
                    fernet = get_fernet()
                except RuntimeError as exc:
                    raise KAFKeyUnavailableError("KAF加密密钥不可用") from exc
                return fernet.encrypt(pem)

            enc = load_or_create_secret(_PRIV_KEY_FILE, create_private)
            try:
                fernet = get_fernet(create=False)
            except RuntimeError as exc:
                raise KAFKeyUnavailableError("KAF原加密密钥不可用") from exc
            pem = fernet.decrypt(enc)
            private = serialization.load_pem_private_key(pem, password=None)
            if not isinstance(private, rsa.RSAPrivateKey):
                raise KAFKeyUnavailableError("KAF账号私钥不是RSA密钥")
            derived = private.public_key()
            pub_pem = derived.public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo)
            public = serialization.load_pem_public_key(
                load_or_create_secret(_PUB_KEY_FILE, lambda: pub_pem))
            if not isinstance(public, rsa.RSAPublicKey) or public.public_numbers() != derived.public_numbers():
                raise KAFKeyUnavailableError("KAF账号公私钥不匹配，请恢复原密钥文件后重试")
            # Failed reads or writes must never leave a usable partial cache.
            self._private_key, self._public_key = private, public
        logger.info("KAF account keypair ready (%d-bit)", self._public_key.key_size)

    @property
    def public_key(self):
        self._load_or_create_keypair()
        return self._public_key

    @property
    def private_key(self):
        self._load_or_create_keypair()
        return self._private_key

    # ---------- 发布 ----------

    def publish_from_markdown(
        self,
        md_text: str,
        topic: str,
        title: str = "",
        source_doc_id: str | None = None,
        source_doc_filename: str = "",
        glossary_refs: list | None = None,
        enabled_tiers: str = "free,pro,enterprise",
        file_id: str | None = None,
    ) -> KafFile:
        """MD → .kaf 实体文件 + KafFile 索引记录(同步函数, 供发布管道调用)。"""
        import hashlib
        import uuid

        from kaf.builder import KAFBuilder

        self._load_or_create_keypair()
        KAF_DIR.mkdir(parents=True, exist_ok=True)

        file_id = file_id or uuid.uuid4().hex
        data = KAFBuilder(self.public_key).build(
            md_text,
            subject=topic,
            title=title or "未命名资料",
            source_doc=source_doc_filename,
            glossary_refs=glossary_refs,
            file_id=file_id,
        )
        path = KAF_DIR / f"{file_id}.kaf"
        _write_publication(path, data)

        return KafFile(
            file_id=file_id,
            topic=topic,
            title=title or "未命名资料",
            file_size=len(data),
            node_count=0,
            source_doc_id=source_doc_id,
            source_claim=source_doc_id or None,
            source_doc_filename=source_doc_filename,
            storage_path=str(path),
            sha256=hashlib.sha256(data).hexdigest(),
            is_published=True,
            enabled_tiers=enabled_tiers,
        )

    def publish_from_package(self, package: dict, meta: dict) -> KafFile:
        """内容包 dict → .kaf(供题库/总纲等结构化内容发布)。"""
        import hashlib
        import uuid

        from kaf.builder import KAFBuilder

        self._load_or_create_keypair()
        KAF_DIR.mkdir(parents=True, exist_ok=True)

        file_id = meta.get("file_id") or uuid.uuid4().hex
        data = KAFBuilder(self.public_key).build_from_package(package, {
            **meta, "file_id": file_id,
        })
        path = KAF_DIR / f"{file_id}.kaf"
        _write_publication(path, data)

        return KafFile(
            file_id=file_id,
            topic=meta.get("topic", ""),
            title=meta.get("title", "未命名资料"),
            file_size=len(data),
            node_count=meta.get("node_count", 0),
            source_doc_id=meta.get("source_doc_id"),
            source_claim=meta.get("source_doc_id") or None,
            source_doc_filename=meta.get("source_doc_filename", ""),
            storage_path=str(path),
            sha256=hashlib.sha256(data).hexdigest(),
            is_published=meta.get("is_published", True),
            enabled_tiers=meta.get("enabled_tiers", "free,pro,enterprise"),
        )

    # ---------- 授权 ----------

    def tier_allows(self, kaf_file: KafFile, tier: str) -> bool:
        allowed = [t.strip() for t in (kaf_file.enabled_tiers or "").split(",") if t.strip()]
        return tier in allowed

    async def grant(self, db: AsyncSession, file_id: str, user_id: str, tier: str) -> dict:
        """签发授权:校验文件存在 + 发布状态 + 档位 → 返回内容密钥。

        Returns:
            {"file_id", "content_key_hex", "tier", "expires_at", "cached"}
        Raises:
            LookupError 文件不存在/未发布
            PermissionError 档位不足或授权已被管理员吊销
        """
        # 先查文件元数据(发布状态/档位每次实时校验, 不做缓存 —— 防止
        # 下架后内存缓存仍放行); 授权结果才进内存缓存。
        result = await db.execute(select(KafFile).where(KafFile.file_id == file_id))
        kf = result.scalar_one_or_none()
        if kf is None:
            raise LookupError("文件不存在")
        if not kf.is_published:
            raise LookupError("文件已下架")
        if not self.tier_allows(kf, tier):
            raise PermissionError("当前会员等级无权访问该资料")

        # 任一历史 revoked=True 是持续拒绝标记；旧版本曾在吊销后重签，
        # 因此不能只检查当前缓存关联的 grant 或最新一条有效 grant。
        if await self._has_revocation(db, file_id, user_id):
            self._grant_cache.pop((file_id, user_id), None)
            raise PermissionError("该资料授权已被管理员吊销")

        # Denied requests must not touch keys or turn an access decision into
        # a key-service outage. Keep key I/O off the event loop after validation.
        await run_in_threadpool(self._load_or_create_keypair)

        # 内存缓存(5 分钟): 档位一致才命中; 档位变化/过期即刻失效
        memo_key = (file_id, user_id)
        memo = self._grant_cache.get(memo_key)
        now = utcnow()
        if (memo and memo["tier"] == tier and memo["cache_expires_at"] > now
                and memo["expires_at"] > now):
            result = await db.execute(
                select(KafGrant).where(
                    KafGrant.id == memo["grant_id"],
                    KafGrant.file_id == file_id,
                    KafGrant.user_id == user_id,
                    KafGrant.revoked == False,  # noqa: E712
                ).execution_options(populate_existing=True)
            )
            active_grant = result.scalar_one_or_none()
            grant_expiry = (
                active_grant.expires_at.replace(tzinfo=timezone.utc)
                if active_grant and active_grant.expires_at else None
            )
            if (active_grant and active_grant.tier == tier
                    and (grant_expiry is None or grant_expiry > now)):
                expires = min(memo["expires_at"], grant_expiry) if grant_expiry else memo["expires_at"]
                return {
                    "file_id": file_id,
                    "content_key_hex": memo["content_key_hex"],
                    "tier": tier,
                    "expires_at": expires.isoformat(),
                    "cached": True,
                }
            self._grant_cache.pop(memo_key, None)

        # 从 .kaf 解包内容密钥(服务端持有账号私钥)
        from kaf.format import inspect_kaf
        from kaf.errors import KAFDecryptError

        try:
            data = await run_in_threadpool(_read_publication, kf.file_id, kf.sha256)
        except FileNotFoundError as exc:
            raise LookupError("文件数据缺失，请联系管理员") from exc
        except OSError as exc:
            raise KAFContentUnavailableError("资料文件暂不可读取") from exc
        try:
            info = inspect_kaf(data)
        except Exception:
            raise LookupError("文件数据损坏，请联系管理员")
        if info["header"].get("file_id") != kf.file_id:
            raise LookupError("文件身份不匹配，请联系管理员")

        try:
            content_key = await run_in_threadpool(self._unwrap_content_key, data)
        except KAFDecryptError as exc:
            raise LookupError("文件密钥信封损坏，请联系管理员") from exc
        issued_at = utcnow()
        expires = issued_at + timedelta(hours=GRANT_TTL_HOURS)
        payload = {
            "file_id": kf.file_id,
            "content_key_hex": content_key.hex(),
            "tier": tier,
            "expires_at": expires.isoformat(),
            "cached": False,
        }

        # 落审计记录
        existing = await db.execute(
            select(KafGrant).where(
                KafGrant.file_id == file_id,
                KafGrant.user_id == user_id,
                KafGrant.revoked == False,  # noqa: E712
            ).order_by(KafGrant.created_at.desc(), KafGrant.id.desc()).limit(1)
        )
        grant = existing.scalar_one_or_none()
        if grant:
            grant.tier = tier
            grant.expires_at = expires.replace(tzinfo=None)
        else:
            grant = KafGrant(
                file_id=file_id,
                user_id=user_id,
                tier=tier,
                expires_at=expires.replace(tzinfo=None),
            )
            db.add(grant)
        await db.commit()

        # 吊销可能在首次检查后、授权写入期间由另一会话完成。
        # commit 后重新读取，避免在已观察到吊销时继续返回明文密钥。
        if await self._has_revocation(db, file_id, user_id):
            self._grant_cache.pop(memo_key, None)
            raise PermissionError("该资料授权已被管理员吊销")

        self._grant_cache[memo_key] = {
            "content_key_hex": content_key.hex(),
            "expires_at": expires,
            "cache_expires_at": issued_at + timedelta(seconds=GRANT_MEMO_TTL_SECONDS),
            "tier": tier,
            "grant_id": grant.id,
        }
        return payload

    async def _has_revocation(self, db: AsyncSession, file_id: str, user_id: str) -> bool:
        result = await db.execute(
            select(KafGrant).where(
                KafGrant.file_id == file_id,
                KafGrant.user_id == user_id,
                KafGrant.revoked == True,  # noqa: E712
            ).limit(1).execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none() is not None

    def _unwrap_content_key(self, data: bytes) -> bytes:
        """从 .kaf 信封解包内容密钥(服务端持账号私钥)。"""
        from kaf.format import inspect_kaf, IV_LEN
        from kaf.keys import unwrap_content_key

        info = inspect_kaf(data)
        iv_len = IV_LEN
        wkl = info["header"].get("wrapped_key_len", 512)
        env = data[info["envelope_offset"]:info["envelope_offset"] + info["envelope_len"]]
        wrapped = env[iv_len:iv_len + wkl]
        return unwrap_content_key(wrapped, self.private_key)

    async def revoke(self, db: AsyncSession, file_id: str, user_id: str | None = None) -> int:
        """吊销已有授权并阻止后续签发；返回本次吊销的授权行数。"""
        stmt = update(KafGrant).where(
            KafGrant.file_id == file_id, KafGrant.revoked == False  # noqa: E712
        )
        if user_id is not None:
            stmt = stmt.where(KafGrant.user_id == user_id)
        result = await db.execute(stmt.values(revoked=True))
        await db.commit()
        # 清内存缓存
        keys = [k for k in self._grant_cache if k[0] == file_id and (user_id is None or k[1] == user_id)]
        for k in keys:
            self._grant_cache.pop(k, None)
        return result.rowcount or 0

    async def restore(self, db: AsyncSession, file_id: str, user_id: str | None = None) -> int:
        """解除指定用户或文件范围的持续拒绝；返回本次恢复的历史授权行数。"""
        stmt = update(KafGrant).where(
            KafGrant.file_id == file_id, KafGrant.revoked == True  # noqa: E712
        )
        if user_id is not None:
            stmt = stmt.where(KafGrant.user_id == user_id)
        # nullable Boolean 三态：True=持续拒绝，False=有效授权，None=已恢复的旧记录。
        # 不改成 False，以免复活旧密钥授权或形成多条有效 grant。
        result = await db.execute(stmt.values(revoked=None))
        await db.commit()
        keys = [k for k in self._grant_cache if k[0] == file_id and (user_id is None or k[1] == user_id)]
        for k in keys:
            self._grant_cache.pop(k, None)
        return result.rowcount or 0

    # ---------- 认证辅助 ----------

    async def check_active_grant(self, db: AsyncSession, file_id: str, user_id: str) -> tuple[bool, dict]:
        """检查某用户对某文件是否有未过期授权(用于客户端离线打开前二次确认)。"""
        if await self._has_revocation(db, file_id, user_id):
            return False, {}
        result = await db.execute(
            select(KafGrant).where(
                KafGrant.file_id == file_id,
                KafGrant.user_id == user_id,
                KafGrant.revoked == False,  # noqa: E712
            ).order_by(KafGrant.created_at.desc(), KafGrant.id.desc()).limit(1)
        )
        grant = result.scalar_one_or_none()
        if grant is None:
            return False, {}
        if grant.expires_at and grant.expires_at.replace(tzinfo=timezone.utc) < utcnow():
            return False, {}
        return True, {"tier": grant.tier, "expires_at": grant.expires_at.isoformat() if grant.expires_at else None}


_service: KAFService | None = None


def get_kaf_service() -> KAFService:
    global _service
    if _service is None:
        _service = KAFService()
    return _service
