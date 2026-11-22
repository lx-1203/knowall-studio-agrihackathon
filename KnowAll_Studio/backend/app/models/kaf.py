"""KAF 加密格式数据模型:文件索引 / 密钥授权(token-grant 模型)。

授权模型(MVP):
  - 服务端持有账号公私钥对(私钥 Fernet 加密落盘, 见 app/core/kaf_service.py)
  - 客户端用 JWT 调用 grant 接口, 服务端校验会员状态后返回明文内容密钥。
  - 服务端吊销能阻止后续授权和在线检查；已经交付客户端的文件和密钥
    无法被服务端收回，离线解密仍可能成功。
"""
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint
from app.database import Base
from app.models import gen_uuid, now


class KafFile(Base):
    """.kaf 文件索引(发布管道写入; 实体文件存 data/documents/kaf/)。"""
    __tablename__ = "kaf_files"
    __table_args__ = (UniqueConstraint("source_claim", name="uq_kaf_files_source_claim"),)

    id = Column(String(36), primary_key=True, default=gen_uuid)
    file_id = Column(String(64), unique=True, nullable=False, index=True)  # .kaf header 里的 file_id
    topic = Column(String(64), nullable=False)          # 学科/主题, 如 "分子生物学"
    title = Column(String(255), nullable=False)
    file_size = Column(Integer, default=0)
    node_count = Column(Integer, default=0)
    source_doc_id = Column(String(64), nullable=True, index=True)  # UUID or summary:UUID; legacy duplicates allowed
    source_doc_filename = Column(String(500), default="")
    storage_path = Column(String(500), nullable=False)  # data/documents/kaf/<file_id>.kaf
    sha256 = Column(String(64), nullable=False)
    is_published = Column(Boolean, default=True)        # False = 下架, grant 接口拒绝
    enabled_tiers = Column(String(200), default="free,pro,enterprise")  # 逗号分隔
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
    # Nullable for anonymous files and retained historical duplicates.
    source_claim = Column(String(64), nullable=True)


class KafGrant(Base):
    """授权状态记录；续期会更新已有记录，吊销保留历史状态。"""
    __tablename__ = "kaf_grants"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    file_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tier = Column(String(20), default="free")           # 授权时的订阅档位
    expires_at = Column(DateTime, nullable=True)        # UTC, None = 永不过期
    revoked = Column(Boolean, default=False)            # True = 已吊销(封号/退款)
    created_at = Column(DateTime, default=now)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
