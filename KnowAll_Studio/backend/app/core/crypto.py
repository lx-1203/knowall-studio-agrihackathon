"""API Key secure storage using AES encryption"""
import os
import base64
import logging
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from app.config import DATA_DIR
from app.core.secret_file import load_or_create_secret

logger = logging.getLogger("knowall.crypto")

_KEY_FILE = Path(os.environ.get("KNOWALL_SECRET_KEY_FILE", str(DATA_DIR / ".secret_key")))


def _get_or_create_key() -> bytes:
    """Reuse an existing valid key; publish one complete key across processes."""
    try:
        key = load_or_create_secret(_KEY_FILE, Fernet.generate_key)
        Fernet(key)  # Refuse corrupt existing bytes; never silently rotate a key.
        return key
    except (OSError, ValueError) as e:
        logger.error("Failed to read or create encryption key file: %s", e)
        raise RuntimeError(f"无法读取或创建加密密钥文件，请检查原密钥、磁盘和权限: {e}") from e


def _read_key() -> bytes:
    """Read existing secret material only; recovery never creates a new key."""
    try:
        key = _KEY_FILE.read_bytes()
        Fernet(key)
        return key
    except (OSError, ValueError) as exc:
        raise RuntimeError("无法读取原加密密钥，请恢复原密钥文件后重试") from exc


def get_fernet(*, create: bool = True) -> Fernet:
    """Return the app-wide Fernet instance (用于加密 KAF 账号私钥等敏感文件)。"""
    return Fernet(_get_or_create_key() if create else _read_key())


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt an API key. Returns Fernet token (already base64-encoded)."""
    try:
        fernet = Fernet(_get_or_create_key())
    except Exception as e:
        raise RuntimeError(f"加密模块初始化失败: {e}")
    encrypted = fernet.encrypt(plaintext.encode("utf-8"))
    # Fernet.encrypt already returns base64-encoded bytes; decode to string
    return encrypted.decode("ascii")


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt an API key. Returns the original plaintext."""
    try:
        fernet = get_fernet(create=False)
    except Exception as e:
        raise ValueError(f"加密模块初始化失败: {e}")
    try:
        # New format: Fernet token directly (starts with 'gAAAAAB')
        if ciphertext.startswith("gAAAAAB"):
            return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        # Old format: double base64-encoded (Fernet token wrapped in base64)
        try:
            encrypted = base64.b64decode(ciphertext.encode("ascii"))
        except Exception:
            # Assume new format without the gAAAAAB prefix
            pass
        else:
            try:
                return fernet.decrypt(encrypted).decode("utf-8")
            except InvalidToken:
                pass
        # Last resort: treat as direct Fernet token
        return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise ValueError("API 密钥解密失败，密钥文件可能已损坏或被替换")
    except Exception as e:
        raise ValueError(f"API 密钥解密失败: {e}")
