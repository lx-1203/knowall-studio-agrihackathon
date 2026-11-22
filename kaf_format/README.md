# KAF — KnowAll Format

自研知识库加密文件格式:Markdown 内容 → 加密二进制容器 `.kaf`,防爬取、防二次传播。

## 设计要点

- **内部源格式是 MD**,`.kaf` 只是分发格式 —— 内容生产(MarkItDown/知识总纲管道)完全不受影响
- 文件里**没有明文密钥**:内容密钥被用户账号公钥 RSA-OAEP(SHA-256) 包裹后写入文件
  密钥信封,客户端私钥存于手机安全区(Android Keystore / iOS Keychain),联网验证授权后解包
- AES-256-GCM 认证加密:篡改任意一个字节都会解密失败
- 明文头仅含元数据(标题/学科/大小/校验和),不含任何内容;文件尾 1KB 对齐伪随机填充,
  不暴露真实内容大小

## 二进制布局

```
0x00  MAGIC "KNAF001\0" (8B)
0x08  HEADER_LEN (4B, big-endian)
      HEADER (JSON 元数据, 明文)
      ENVELOPE_LEN (4B)
      ENVELOPE: iv(12B) + wrapped_key(512B, RSA-4096) + secret(可选)
      PAYLOAD: AES-256-GCM 密文 (+16B auth tag), AAD 绑定 HEADER
      PADDING: 伪随机填充至 1KB 对齐
```

## CLI 用法

```bash
# 1. 生成账号密钥对(私钥存客户端安全区, 公钥上传服务端)
python -m kaf.cli keygen --out keys/

# 2. Markdown → .kaf(用账号公钥加密)
python -m kaf.cli convert 文档.md --out 文档.kaf --pub keys/pub.pem \
    --subject 分子生物学 --title "分子生物学总结"

# 3. 只读检查结构(不解密)
python -m kaf.cli inspect 文档.kaf

# 4. 用私钥解密验证(仅服务端/开发用)
python -m kaf.cli decrypt 文档.kaf --priv keys/priv.pem --out payload.json
```

## 代码结构

| 模块 | 职责 |
|---|---|
| `kaf/format.py` | 二进制容器 pack/unpack/inspect,偏移与校验 |
| `kaf/keys.py` | 密钥对生成、内容密钥生成/包裹/解包、AES-GCM 加解密 |
| `kaf/builder.py` | Markdown → 内容包 JSON(章节/节点拆分)→ .kaf |
| `kaf/errors.py` | 分层异常(解析失败/版本不兼容/解密失败/校验失败) |
| `kaf/cli/` | 命令行工具 |
| `tests/test_kaf.py` | 16 项测试:round-trip、篡改检测、密钥不匹配、截断、加粗标题拆分 |

## 已知边界(诚实声明)

- 手机 App 渲染明文时,内容必然进入内存 —— 截图/OCR 无法技术根除,靠水印 + 封号兜底
- 客户端密钥必须存硬件安全区(Kotlin/Swift 原生层),**不能放进 JS 层**
- 服务端需实现「登录 → 签发包裹体 → 过期吊销」授权流(见 `keys.py` 的 meta 说明)
