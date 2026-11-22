"""KAF (KnowAll Format) — 自研知识库加密文件格式。

格式设计:
  - 二进制容器,魔数 "KNAF001\\0"(8 字节,含版本)
  - 明文 JSON Header(仅元数据,不含内容)
  - 内容密钥 AES-256-GCM 加密 PAYLOAD
  - 内容密钥被 RSA-OAEP(SHA-256) 包裹(账号公钥)
  - 文件尾部 1KB 对齐伪随机填充(伪装大小,防特征识别)

形如:
  +----------------+ 0x00 MAGIC "KNAF001\0" (8B)
  +----------------+ 0x08 HEADER_LEN (4B, big-endian)
  +----------------+ HEADER (JSON, 明文)
  +----------------+ KEY_ENVELOPE (变长: iv + wrapped_key + auth_tag)
  +----------------+ PAYLOAD (AES-256-GCM 密文 + 16B tag)
  +----------------+ PADDING (伪随机, 填充到 1KB 对齐)
"""
from .errors import KAFError, KAFDecryptError, KAFChecksumError, KAFVersionError, KAFParseError
from .keys import (
    generate_keypair,
    generate_content_key,
    wrap_content_key,
    unwrap_content_key,
)
from .format import (
    MAGIC,
    FORMAT_VERSION,
    ALGO_ENC,
    ALGO_WRAP,
    pack_kaf,
    unpack_kaf,
    inspect_kaf,
)
from .builder import KAFBuilder, build_from_markdown

__version__ = "0.1.0"

__all__ = [
    "KAFError", "KAFDecryptError", "KAFChecksumError", "KAFVersionError", "KAFParseError",
    "generate_keypair", "generate_content_key", "wrap_content_key", "unwrap_content_key",
    "MAGIC", "FORMAT_VERSION", "ALGO_ENC", "ALGO_WRAP",
    "pack_kaf", "unpack_kaf", "inspect_kaf",
    "KAFBuilder", "build_from_markdown",
]
