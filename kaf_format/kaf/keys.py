"""密钥管理:内容密钥生成/包裹/解包。

密钥模型(账号授权下发):
  - 每个内容包随机生成一把内容密钥 K(AES-256)
  - K 通过用户账号公钥 RSA-OAEP(SHA-256) 包裹后放进 .kaf 文件
  - 用户客户端持有私钥(Android Keystore / iOS Keychain), 联网解包
  - 文件里没有 K 本身 → 转发文件 ≠ 可解密

密钥文件格式(本地 JSON, 服务端用于导入):
  pub.pem / priv.pem 为标准 PEM,另有 json meta 便于管理。
"""
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from .errors import KAFDecryptError

# RSA 密钥与包裹参数
RSA_KEY_SIZE = 4096
RSA_PADDING = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None,
)
_CONTENT_KEY_SIZE = 32  # AES-256


def generate_keypair(key_size: int = RSA_KEY_SIZE) -> tuple:
    """生成账号密钥对,返回 (private_key, public_key) 对象。

    private 交给客户端安全存储,public 上传服务端用于包裹内容密钥。
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    return private_key, private_key.public_key()


def save_keypair(private_key, public_key, directory: str) -> dict:
    """把密钥对写入目录:PEM + meta.json。返回 meta 信息用于导入。"""
    os.makedirs(directory, exist_ok=True)
    priv_path = os.path.join(directory, "priv.pem")
    pub_path = os.path.join(directory, "pub.pem")
    with open(priv_path, "wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    with open(pub_path, "wb") as f:
        f.write(
            public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
    meta = {
        "type": "account-keypair",
        "key_size": public_key.key_size,
        "created_at": None,  # 调用方自行补时间
        "pub_pem": pub_path,
        "priv_pem": priv_path,
    }
    with open(os.path.join(directory, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def load_private_key(pem_path: str):
    """加载 PEM 私钥。"""
    with open(pem_path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def load_public_key(pem_path: str):
    """加载 PEM 公钥。"""
    with open(pem_path, "rb") as f:
        return serialization.load_pem_public_key(f.read())


def generate_content_key() -> bytes:
    """生成随机内容密钥(AES-256)。"""
    return os.urandom(_CONTENT_KEY_SIZE)


def wrap_content_key(content_key: bytes, public_key) -> bytes:
    """用账号公钥包裹内容密钥 → bytes(RSA-OAEP 密文)。

    该 bytes 写入 .kaf 的 KEY_ENVELOPE; 文件里不出现明文密钥。
    """
    return public_key.encrypt(content_key, RSA_PADDING)


def unwrap_content_key(wrapped: bytes, private_key) -> bytes:
    """用账号私钥解包出内容密钥。

    失败(密钥不匹配/被篡改)抛 KAFDecryptError —— 上层据此提示
    「无权限打开此文件」而非「文件损坏」。
    """
    try:
        return private_key.decrypt(wrapped, RSA_PADDING)
    except Exception as e:  # ValueError: 解密失败
        raise KAFDecryptError("无法解包内容密钥(无授权或密钥不匹配)") from e


def encrypt_aes_gcm(plaintext: bytes, key: bytes, aad: bytes = b"") -> tuple:
    """AES-256-GCM 加密,返回 (iv, ciphertext_with_tag)。"""
    iv = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(iv)).encryptor()
    encryptor.authenticate_additional_data(aad)
    ct = encryptor.update(plaintext) + encryptor.finalize()
    return iv, ct + encryptor.tag


def decrypt_aes_gcm(iv: bytes, data: bytes, key: bytes, aad: bytes = b"") -> bytes:
    """AES-256-GCM 解密;数据被篡改时抛 KAFDecryptError(GCM tag 校验失败)。"""
    tag, ct = data[-16:], data[:-16]
    try:
        decryptor = Cipher(algorithms.AES(key), modes.GCM(iv, tag)).decryptor()
        decryptor.authenticate_additional_data(aad)
        return decryptor.update(ct) + decryptor.finalize()
    except Exception as e:
        raise KAFDecryptError("解密失败(密钥错误或密文被篡改)") from e
