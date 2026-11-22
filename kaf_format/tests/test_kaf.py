"""KAF 格式核心测试:round-trip、篡改检测、密钥不匹配、结构健壮性。"""
import base64
import json
import os
import sys
import tempfile
import unittest
import struct
import contextlib
import io
import hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaf.format import pack_kaf, unpack_kaf, inspect_kaf
from kaf.keys import (
    generate_keypair, generate_content_key, wrap_content_key, unwrap_content_key,
    encrypt_aes_gcm, decrypt_aes_gcm,
)
from kaf.errors import KAFParseError, KAFDecryptError, KAFChecksumError, KAFVersionError
from kaf.builder import KAFBuilder, build_content_package


SAMPLE_MD = """# 第一章 细胞的分子组成

## 1.1 蛋白质

蛋白质是由氨基酸组成的生物大分子。

## 1.2 核酸

核酸包括 DNA 和 RNA。
"""


def _pack(payload: bytes, pub, private, size=4096):
    """便捷打包:返回 .kaf 字节 + 内容密钥(测试用)。"""
    key = generate_content_key()
    wrapped = wrap_content_key(key, pub)
    meta = {"file_id": "test-001", "title": "测试", "subject": "分子生物学",
            "node_count": 2, "created_at": "2026-09-05T10:00:00"}
    data = pack_kaf(payload, key, wrapped, meta)
    return data, key


class TestRoundTrip(unittest.TestCase):
    def test_pack_unpack_roundtrip(self):
        priv, pub = generate_keypair(2048)  # 测试用 2048, 生产 4096
        payload = json.dumps({"hello": "世界", "n": 1}, ensure_ascii=False).encode()
        data, _ = _pack(payload, pub, priv)
        result = unpack_kaf(data, priv)
        self.assertEqual(result["payload_bytes"], payload)
        self.assertTrue(result["checksum_ok"])

    def test_magic_and_version_fields(self):
        priv, pub = generate_keypair(2048)
        data, _ = _pack(b"abc", pub, priv)
        self.assertTrue(data.startswith(b"KNAF001\x00"))
        info = inspect_kaf(data)
        self.assertEqual(info["header"]["version"], 1)
        self.assertEqual(info["header"]["enc_algo"], "AES-256-GCM")
        self.assertEqual(info["header"]["wrap_algo"], "RSA-OAEP-SHA256")

    def test_padding_to_1024(self):
        priv, pub = generate_keypair(2048)
        data, _ = _pack(b"x" * 100, pub, priv)
        self.assertEqual(len(data) % 1024, 0)

    def test_wrong_key_fails_cleanly(self):
        priv_a, pub_a = generate_keypair(2048)
        priv_b, pub_b = generate_keypair(2048)
        payload = b"secret content"
        data, _ = _pack(payload, pub_a, priv_a)
        with self.assertRaises(KAFDecryptError):
            unpack_kaf(data, priv_b)

    def test_tamper_payload_detected(self):
        priv, pub = generate_keypair(2048)
        data, _ = _pack(b"content", pub, priv)
        info = inspect_kaf(data)
        offset = info["payload_offset"]
        tampered = bytearray(data)
        tampered[offset] ^= 0xFF  # 翻转 payload 第一个字节
        with self.assertRaises(KAFDecryptError):
            unpack_kaf(bytes(tampered), priv)

    def test_tamper_header_detected(self):
        priv, pub = generate_keypair(2048)
        data, _ = _pack(b"content", pub, priv)
        tamp = bytearray(data)
        start = 12 + struct.unpack(">I", data[8:12])[0] + 4
        tamp[start] ^= 0x01  # 改信封第一个字节(破坏 iv → GCM 解密失败)
        with self.assertRaises(KAFDecryptError):
            unpack_kaf(bytes(tamp), priv)

    def test_truncated_file_rejected(self):
        priv, pub = generate_keypair(2048)
        data, _ = _pack(b"content" * 50, pub, priv)
        info = inspect_kaf(data)
        # 只砍 padding → 文件仍可正常解析(设计允许)
        partial_padding = data[: info["payload_offset"] + info["payload_len"]]
        ok = unpack_kaf(partial_padding, priv)
        self.assertEqual(ok["payload_bytes"], b"content" * 50)
        # 砍到 payload 中间 → 必须拒绝
        cut = data[: info["payload_offset"] + info["payload_len"] - 20]
        with self.assertRaises(KAFParseError):
            unpack_kaf(cut, priv)

    def test_non_kaf_rejected(self):
        with self.assertRaises(KAFParseError):
            inspect_kaf(b"PK\x03\x04 not a kaf file")

    def test_checksum_mismatch_raises(self):
        # 构造 header 校验和与实际 payload 不符(模拟损坏)
        priv, pub = generate_keypair(2048)
        key = generate_content_key()
        wrapped = wrap_content_key(key, pub)
        payload = b"corrupt candidate"
        # 手动构建 header:校验和故意填错
        import hashlib
        import struct
        from kaf.format import MAGIC, ALGO_ENC, ALGO_WRAP
        header = {
            "file_id": "x", "title": "", "subject": "", "size": len(payload),
            "node_count": 0, "enc_algo": ALGO_ENC, "wrap_algo": ALGO_WRAP,
            "wrapped_key_len": len(wrapped),
            "checksum_sha256": hashlib.sha256(b"wrong").hexdigest(),
            "created_at": "", "version": 1,
        }
        hjson = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode()
        iv, enc = encrypt_aes_gcm(payload, key, hjson)  # AAD 需与 pack 端一致(绑定 header)
        data = MAGIC + struct.pack(">I", len(hjson)) + hjson + \
            struct.pack(">I", len(iv + wrapped)) + iv + wrapped + enc
        with self.assertRaises(KAFChecksumError):
            unpack_kaf(data, priv)

class TestKeyManagement(unittest.TestCase):
    def test_wrap_unwrap_roundtrip(self):
        priv, pub = generate_keypair(2048)
        key = generate_content_key()
        wrapped = wrap_content_key(key, pub)
        self.assertEqual(unwrap_content_key(wrapped, priv), key)

    def test_key_sizes(self):
        key = generate_content_key()
        self.assertEqual(len(key), 32)  # AES-256

    def test_aes_gcm_roundtrip_with_aad(self):
        key = generate_content_key()
        iv, enc = encrypt_aes_gcm(b"hello", key, b"aad-data")
        self.assertEqual(decrypt_aes_gcm(iv, enc, key, b"aad-data"), b"hello")
        with self.assertRaises(KAFDecryptError):
            decrypt_aes_gcm(iv, enc, key, b"other-aad")  # AAD 不匹配


class TestBuilder(unittest.TestCase):
    def test_build_from_markdown_roundtrip(self):
        priv, pub = generate_keypair(2048)
        data = KAFBuilder(pub).build(SAMPLE_MD, subject="分子生物学", title="细胞组成")
        result = unpack_kaf(data, priv)
        package = json.loads(result["payload_bytes"].decode("utf-8"))
        self.assertEqual(package["subject"], "分子生物学")
        self.assertGreaterEqual(len(package["chapters"]), 1)
        self.assertTrue(any("蛋白质" in c["body"] for c in package["chapters"]))

    def test_content_package_structure(self):
        pkg = build_content_package(SAMPLE_MD, subject="分子生物学")
        chapters = pkg["chapters"]
        self.assertEqual(chapters[0]["title"], "第一章 细胞的分子组成")
        body_all = "\n".join(c["body"] for c in chapters)
        self.assertIn("蛋白质是由氨基酸组成的生物大分子", body_all)

    def test_intro_goes_to_ch000(self):
        md = "这是一段导言。\n\n# 第一章\n正文。"
        pkg = build_content_package(md)
        self.assertEqual(pkg["chapters"][0]["id"], "ch-000-intro")
        self.assertIn("导言", pkg["chapters"][0]["body"])

    def test_bold_line_heading_detected(self):
        """整行加粗 `**标题**` 视为章节标题(问答/讲义类笔记的常见写法)。"""
        md = "**绪论+第一章 分子生物学发展史**\n\nDNA；RNA；某些蛋白质\n\n**DNA的双螺旋结构**\n\n小沟和大沟。"
        pkg = build_content_package(md)
        titles = [c["title"] for c in pkg["chapters"]]
        self.assertIn("绪论+第一章 分子生物学发展史", titles)
        self.assertIn("DNA的双螺旋结构", titles)
        bodies = " | ".join(c["body"] for c in pkg["chapters"])
        self.assertIn("小沟和大沟", bodies)


if __name__ == "__main__":
    unittest.main()


FROZEN_KAF_B64 = (
    "S05BRjAwMQAAAAE1eyJmaWxlX2lkIjoiZnJvemVuLTAwMSIsInRpdGxlIjoi5Ya757uT5qC35L6LIiwic3ViamVjdCI6IuWIhuWtkOeUn+eJqeWtpiIsInNpemUiOjQwLCJub2RlX2NvdW50IjoxLCJlbmNfYWxnbyI6IkFFUy0yNTYtR0NNIiwid3JhcF9hbGdvIjoiUlNBLU9BRVAtU0hBMjU2Iiwid3JhcHBlZF9rZXlfbGVuIjoyNTYsImNoZWNrc3VtX3NoYTI1NiI6IjczZThlYmNkZDJlMTUzYjYxYmI2NTBlODNhNTA5Zjg4MTJlYzQ1MGUxMjkyNzQ1YzY2ODU2NzhhZWYxNGZlOGEiLCJjcmVhdGVkX2F0IjoiMjAyNi0xMC0wMlQwMDowMDowMCIsInZlcnNpb24iOjF9AAABDGRq2PcjKF9qAO8BjY1ifi0hqwWGKi3b/Pgn+35Bq6Ua9yGw/JtIjf1v/fjh4nRACQeQBbHS+UoeNI73Hz5jTgG1PpIyNYvN6mciSdlYT5UsdTINmQ/PiHF3ytYC/TtjQM6oRN3MBYbF4C+SsGErBWcLCRC+TqXQlQn5xYKp7IWiI+UalARERJzL8cWqS+TvUMDvc/u0/WjvXT2tJo3ItZvLSilaw9YrGlEHoHJxncLb2WBZvN1j30tzMoOM44V/HQ9ujjR1rJXC6YRvrvFtVrX0Ep9ramU1h822G3NXilbYyC07kaC7jk2Pgyf1o7++ozjjtRaN3JG5MMSrfZJgOyofGlfT2QISSFuQy8uOfKwOj2l10/dod93bplx82t3kOs05eFfsGXsxtVyxw1/cGtv5V548OQL1y8ajDjXAJY2AUgRnGSAUeXqQyZz0/ICLTeRM4N3Cdktx3ayQTv5ZsswVINIMA3vNAwMYYiIxdavUVin8bJmkCcqq3Gx1t3RBIysHnWT1Xsmk84G2SB+K+PyvW+67bE7Z79Ti9xMZQkpO+5XbnREF8DztssnXCWvvyKRmF1DBxoO54FeJB2Rc/fY1kqVlDywcjuu0BOuUjzijsg6tZGv2gSY0kjTmsK+cWfBsKCgxUqb70u5mQT3+YMQMrud4D+pVjMLESiyf997C6afyASwhaJjYF6Za5E2H4EO8qQRjTnMRa/Bw0Z5/5aS+/lS+TULYGPY8d3hmBxSRALXr2TFrRkF6e74rvgN1cwIzJkRUjwblicBsadBUblxeuI4Rj1HgoNh2hJrON6ZMxfGB23fzWORMOb00BeArYfzndNpLfb795p+JUjFerEF75wNuRN97+7Ki8KRxMkQfOo7kFJMSunKsgbvliUIFRB6qVg2C90PvfbLmYOCwvRGG4sqZN6AWURTqPA=="
)
FROZEN_PAYLOAD_LEN = 40
FROZEN_CHECKSUM = "73e8ebcdd2e153b61bb650e83a509f8812ec450e1292745c6685678aef14fe8a"


class TestFormatCompatibility(unittest.TestCase):
    """冻结样例 + 版本/对齐契约（第 46 轮 T14）：把「格式本身」也钉住。"""

    def test_frozen_sample_still_parses(self):
        """冻结的 .kaf 样例必须能被当前版本读出来（MAGIC/头部布局/字段名不变）。"""
        data = base64.b64decode(FROZEN_KAF_B64)
        info = inspect_kaf(data)
        header = info["header"]

        self.assertEqual(header["version"], 1)
        self.assertEqual(header["file_id"], "frozen-001")
        self.assertEqual(header["enc_algo"], "AES-256-GCM")
        self.assertEqual(header["wrap_algo"], "RSA-OAEP-SHA256")
        self.assertEqual(header["checksum_sha256"], FROZEN_CHECKSUM)
        self.assertEqual(info["payload_len"], FROZEN_PAYLOAD_LEN + 16)
        self.assertEqual(len(bytes.fromhex(info["iv"])), 12)
        self.assertEqual(info["file_size"], len(data))
        self.assertEqual(info["file_size"] % 1024, 0)

    def test_packed_file_is_1kb_aligned(self):
        """打包产物按 1KB 对齐（格式文档承诺：总长 = N × 1024），且 padding 是尾部余量。"""
        priv, pub = generate_keypair(2048)
        data, _ = _pack(b"x" * 600, pub, priv)

        self.assertEqual(len(data) % 1024, 0)
        info = inspect_kaf(data)
        self.assertEqual(
            info["padding"],
            len(data) - (info["payload_offset"] + info["payload_len"]),
        )
        self.assertGreater(info["padding"], 0)

    def test_version_mismatch_raises(self):
        """头部版本号不是 1 时必须抛 KAFVersionError，而不是当成好文件读。"""
        priv, pub = generate_keypair(2048)
        data, _ = _pack(b"payload for version check", pub, priv)

        header_len = struct.unpack(">I", data[8:12])[0]
        header = json.loads(data[12:12 + header_len].decode("utf-8"))
        header["version"] = 2
        new_header = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        patched = data[:8] + struct.pack(">I", len(new_header)) + new_header + data[12 + header_len:]

        with self.assertRaises(KAFVersionError):
            inspect_kaf(patched)



class TestCli(unittest.TestCase):
    """CLI 端到端（第 49 轮补）：keygen → convert → inspect → decrypt + 错误路径。"""

    def test_cli_keygen_convert_inspect_decrypt_roundtrip(self):
        from kaf.cli.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            keys_dir = os.path.join(tmp, "keys")
            main(["keygen", "--out", keys_dir, "--key-size", "2048"])
            names = os.listdir(keys_dir)
            priv_name = [n for n in names if n.endswith("priv.pem")]
            pub_name = [n for n in names if n.endswith("pub.pem")]
            self.assertEqual(len(priv_name), 1, names)
            self.assertEqual(len(pub_name), 1, names)
            priv_path = os.path.join(keys_dir, priv_name[0])
            pub_path = os.path.join(keys_dir, pub_name[0])

            md_path = os.path.join(tmp, "note.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# 标题\n\n正文内容正文内容")
            kaf_path = os.path.join(tmp, "note.kaf")

            main(["convert", md_path, "--out", kaf_path, "--pub", pub_path,
                  "--subject", "分子生物学", "--title", "笔记"])
            self.assertTrue(os.path.exists(kaf_path))
            with open(kaf_path, "rb") as f:
                data = f.read()
            self.assertEqual(len(data) % 1024, 0)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main(["inspect", kaf_path])
            info = json.loads(buf.getvalue())
            self.assertEqual(info["header"]["title"], "笔记")
            self.assertEqual(info["header"]["subject"], "分子生物学")

            out_json = os.path.join(tmp, "decrypted.json")
            main(["decrypt", kaf_path, "--priv", priv_path, "--out", out_json])
            with open(out_json, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertIn("正文内容", json.dumps(payload, ensure_ascii=False))

    def test_cli_inspect_rejects_non_kaf_file(self):
        from kaf.cli.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.kaf")
            with open(bad, "wb") as f:
                f.write(b"this is not a kaf file")
            with self.assertRaises(KAFParseError):
                main(["inspect", bad])

