""".kaf 二进制容器:打包 / 解析 / 检查。

布局(所有多字节整数均为 big-endian):

  +----------------+ 0x00  MAGIC  "KNAF001\\0" (8B)
  +----------------+ 0x08  HEADER_LEN (4B)
  +----------------+       HEADER (JSON, 明文, 仅元数据, 不含内容)
  +----------------+       ENVELOPE_LEN (4B)
  +----------------+       ENVELOPE (密钥信封, 变长)
  |                       |    iv(12B) | wrapped_key(RSA-OAEP 密文, 512B for RSA-4096) | secret(可选)
  +----------------+       PAYLOAD (AES-256-GCM 密文, 尾部 16B auth tag)
  +----------------+       PADDING (伪随机填充到 1KB 对齐)

要点:
  - PAYLOAD 的 GCM AAD 绑定 HEADER 原文, 防「换 header 冒充」。
  - ENVELOPE 里的 iv 是 PAYLOAD 的 GCM iv; wrapped_key 是被用户账号公钥
    RSA-OAEP 包裹的内容密钥 —— 文件里没有任何明文密钥。
  - PADDING 使文件总长对齐 1KB, 不暴露内容真实大小; 解析时按 header 声明
    的 size 截断 payload, padding 不参与解密。
"""
import hashlib
import json
import os
import struct

from .errors import KAFParseError, KAFVersionError, KAFChecksumError
from .keys import encrypt_aes_gcm, decrypt_aes_gcm

MAGIC = b"KNAF001\x00"
FORMAT_VERSION = 1
ALGO_ENC = "AES-256-GCM"
ALGO_WRAP = "RSA-OAEP-SHA256"
PAD_BLOCK = 1024

# 本版本信封结构:iv(12) + wrapped_key(长度由 header.wrapped_key_len 声明) + secret(可选)
IV_LEN = 12
GCM_TAG_LEN = 16


def _build_header(meta: dict, size: int, wrapped_key_len: int) -> dict:
    return {
        "file_id": meta.get("file_id", ""),
        "title": meta.get("title", ""),
        "subject": meta.get("subject", ""),
        "size": size,
        "node_count": meta.get("node_count", 0),
        "enc_algo": ALGO_ENC,
        "wrap_algo": ALGO_WRAP,
        "wrapped_key_len": wrapped_key_len,
        "checksum_sha256": hashlib.sha256(b"").hexdigest(),  # 下方用真实 payload 覆写
        "created_at": meta.get("created_at", ""),
        "version": FORMAT_VERSION,
    }


def pack_kaf(payload_plaintext: bytes, content_key: bytes, wrapped_key: bytes,
             meta: dict, secret: bytes = b"") -> bytes:
    """打包 .kaf 文件字节流。

    Args:
        payload_plaintext: 明文内容包(JSON bytes)。
        content_key: AES-256 内容密钥。
        wrapped_key: 内容密钥的 RSA-OAEP 包裹体(账号公钥加密, 512B)。
        meta: 元数据 dict(file_id/title/subject/node_count/created_at)。
        secret: 未来扩展的密钥权限信息(默认空)。

    Returns:
        完整 .kaf 文件字节。
    """
    checksum = hashlib.sha256(payload_plaintext).hexdigest()

    header = _build_header(meta, len(payload_plaintext), len(wrapped_key))
    header["checksum_sha256"] = checksum
    header_json = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    # 先建 header,再用 header 原文作为 GCM-AAD 加密 —— 保证 pack/unpack 两端 AAD 一致
    iv, enc = encrypt_aes_gcm(payload_plaintext, content_key, header_json)

    envelope = iv + wrapped_key + secret

    out = MAGIC
    out += struct.pack(">I", len(header_json))
    out += header_json
    out += struct.pack(">I", len(envelope))
    out += envelope
    out += enc

    # 1KB 对齐伪随机填充:总长 = N * 1024
    pad_len = (PAD_BLOCK - (len(out) % PAD_BLOCK)) % PAD_BLOCK
    if pad_len:
        out += os.urandom(pad_len)
    return out


def _parse_header(data: bytes) -> tuple:
    """解析 MAGIC + HEADER_LEN + HEADER,返回 (header, header_json_raw, envelope_offset)。"""
    if len(data) < 12:
        raise KAFParseError("文件过短, 不是有效的 KAF 文件")
    if not data.startswith(MAGIC):
        raise KAFParseError("非 KAF 文件(魔数不匹配)")
    (header_len,) = struct.unpack(">I", data[8:12])
    if header_len > len(data) - 12:
        raise KAFParseError("文件头长度越界")
    header_raw = data[12:12 + header_len]
    try:
        header = json.loads(header_raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise KAFParseError("文件头解析失败") from e

    if header.get("version") != FORMAT_VERSION:
        raise KAFVersionError(f"不支持的格式版本: {header.get('version')}")
    if header.get("enc_algo") != ALGO_ENC:
        raise KAFVersionError(f"不支持的加密算法: {header.get('enc_algo')}")
    if header.get("wrap_algo") != ALGO_WRAP:
        raise KAFVersionError(f"不支持的密钥包裹算法: {header.get('wrap_algo')}")

    return header, header_raw, 12 + header_len


def inspect_kaf(data: bytes) -> dict:
    """只读检查 .kaf(不解密),返回结构信息与元数据。"""
    header, _header_raw, envelope_offset = _parse_header(data)

    if envelope_offset + 4 > len(data):
        raise KAFParseError("文件不完整(缺少信封长度)")
    (envelope_len,) = struct.unpack(">I", data[envelope_offset:envelope_offset + 4])
    payload_offset = envelope_offset + 4 + envelope_len

    payload_len = header.get("size", 0) + GCM_TAG_LEN
    payload_end = payload_offset + payload_len
    if payload_end > len(data):
        raise KAFParseError("文件不完整(payload 被截断)")

    return {
        "header": header,
        "header_len": envelope_offset - 12,
        "envelope_offset": envelope_offset + 4,
        "envelope_len": envelope_len,
        "payload_offset": payload_offset,
        "payload_len": payload_len,
        "file_size": len(data),
        "padding": len(data) - (payload_offset + payload_len),
        "iv": data[envelope_offset + 4:envelope_offset + 4 + IV_LEN].hex(),
    }


def unpack_kaf(data: bytes, private_key) -> dict:
    """解析 + 完整解密 .kaf。

    Returns:
        {"header": dict, "payload_bytes": bytes, "checksum_ok": bool}
    Raises:
        KAFParseError / KAFVersionError / KAFDecryptError / KAFChecksumError
    """
    from .keys import unwrap_content_key

    info = inspect_kaf(data)
    header = info["header"]
    header_raw = data[12:12 + info["header_len"]]

    envelope = data[info["envelope_offset"]:info["envelope_offset"] + info["envelope_len"]]
    wrapped_key_len = header.get("wrapped_key_len", 512)
    if len(envelope) < IV_LEN + wrapped_key_len:
        raise KAFParseError("密钥信封不完整")
    iv = envelope[:IV_LEN]
    wrapped_key = envelope[IV_LEN:IV_LEN + wrapped_key_len]

    enc_payload = data[info["payload_offset"]:info["payload_offset"] + info["payload_len"]]
    if len(enc_payload) < info["payload_len"]:
        raise KAFParseError("文件不完整(payload 被截断)")

    content_key = unwrap_content_key(wrapped_key, private_key)
    plaintext = decrypt_aes_gcm(iv, enc_payload, content_key, header_raw)

    actual = hashlib.sha256(plaintext).hexdigest()
    if actual != header.get("checksum_sha256"):
        raise KAFChecksumError("内容校验和不匹配(文件可能损坏)")

    return {"header": header, "payload_bytes": plaintext, "checksum_ok": True}
