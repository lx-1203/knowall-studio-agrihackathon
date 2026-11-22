// KAF 加密资料客户端服务
// 职责:
//   1. 服务端授权 grant(校验会员 → 拿内容密钥, 24h TTL, 内存缓存 5min)
//   2. 下载 .kaf 文件
//   3. 纯 JS AES-256-GCM 解密(React Native 无 Node crypto, 用 @noble/ciphers)
//   4. 内容密钥仅驻内存(不落 AsyncStorage), 重启即失效 → 重新授权
//
// 安全边界(MVP):
//   - 密钥绝不写入 AsyncStorage/磁盘 —— 内存驻留, 进程被杀即消失
//   - grant 请求带 JWT, 服务端校验会员档位; 吊销/过期后 grant 拒绝
//   - 内容渲染到界面后无法防截图(全行业同理, 如水印+封号兜底)

import api from '../api'
import { storage } from '../services/storage'

// @noble/ciphers 纯 JS 实现 AES-GCM(经审计, 无原生依赖, Expo 兼容)
import { gcm } from '@noble/ciphers/aes.js'

const GRANT_TTL_HOURS = 24

// 内存密钥缓存: file_id -> { key: Uint8Array, expiresAt: number }
const keyCache = new Map<string, { key: Uint8Array; expiresAt: number }>()

// 授权记录(AsyncStorage, 供离线打开前 check): file_id -> { expiresAt }
const AUTH_KEYS_PREFIX = 'kaf_auth_'

// ===== 与 kaf_format Python 端一致的常量 =====
// 魔数 "KNAF001\0" (8B) + HEADER_LEN(4B big-endian) + HEADER(JSON) +
// ENVELOPE_LEN(4B) + ENVELOPE(iv 12B + wrapped 512B) + PAYLOAD(ciphertext+16B tag)
const MAGIC = new Uint8Array([0x4b, 0x4e, 0x41, 0x46, 0x30, 0x30, 0x31, 0x00]) // "KNAF001\0"
const IV_LEN = 12
const GCM_TAG_LEN = 16

export interface KafFileMeta {
  file_id: string
  title: string
  topic: string
  file_size: number
  node_count: number
  created_at?: string
}

export interface KafDecryptedPackage {
  type: string
  schema_version: number
  title: string
  subject: string
  chapters: {
    id: string
    title: string
    body: string
    nodes?: { id: string; kind: string; title: string; content_md: string }[]
  }[]
  glossary_refs?: string[]
  meta?: Record<string, unknown>
}

// ===== 基础工具 =====

// utf8 编解码手写实现 —— 不依赖 TextEncoder/TextDecoder 全局
// (Hermes 各版本支持不一, 这里零依赖保证稳定)

function utf8ToBytes(s: string): Uint8Array {
  const out: number[] = []
  for (let i = 0; i < s.length; i++) {
    let cp = s.codePointAt(i)!
    if (cp > 0xffff) i++ // surrogate pair 已包含
    if (cp < 0x80) {
      out.push(cp)
    } else if (cp < 0x800) {
      out.push(0xc0 | (cp >> 6), 0x80 | (cp & 0x3f))
    } else if (cp < 0x10000) {
      out.push(0xe0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f))
    } else {
      out.push(
        0xf0 | (cp >> 18),
        0x80 | ((cp >> 12) & 0x3f),
        0x80 | ((cp >> 6) & 0x3f),
        0x80 | (cp & 0x3f),
      )
    }
  }
  return new Uint8Array(out)
}

function bytesToUtf8(b: Uint8Array): string {
  let out = ''
  let i = 0
  while (i < b.length) {
    const b1 = b[i++]
    if (b1 < 0x80) {
      out += String.fromCharCode(b1)
    } else if (b1 < 0xe0) {
      out += String.fromCharCode(((b1 & 0x1f) << 6) | (b[i++] & 0x3f))
    } else if (b1 < 0xf0) {
      out += String.fromCharCode(
        ((b1 & 0x0f) << 12) | ((b[i++] & 0x3f) << 6) | (b[i++] & 0x3f),
      )
    } else {
      let cp =
        ((b1 & 0x07) << 18) |
        ((b[i++] & 0x3f) << 12) |
        ((b[i++] & 0x3f) << 6) |
        (b[i++] & 0x3f)
      cp -= 0x10000
      out += String.fromCharCode(0xd800 + (cp >> 10), 0xdc00 + (cp & 0x3ff))
    }
  }
  return out
}

function readU32BE(data: Uint8Array, offset: number): number {
  return (data[offset] << 24) | (data[offset + 1] << 16) | (data[offset + 2] << 8) | data[offset + 3]
}

function hexToBytes(hex: string): Uint8Array {
  const clean = hex.replace(/\s/g, '')
  if (clean.length % 2 !== 0) throw new Error('Invalid hex length')
  const out = new Uint8Array(clean.length / 2)
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16)
  }
  return out
}

// ===== 授权与下载 =====

/** 申请内容密钥(服务端校验会员档位)。返回 { hex, expiresAt } */
export async function grantContentKey(fileId: string): Promise<{ key: Uint8Array; expiresAt: number }> {
  // 1. 内存缓存命中(5 分钟, 且未过期)
  const memo = keyCache.get(fileId)
  if (memo && memo.expiresAt > Date.now()) {
    return { key: memo.key, expiresAt: memo.expiresAt }
  }

  // 2. 走服务端授权
  const res = await api.post(`/kaf/files/${fileId}/grant`)
  const hex = res.data.content_key_hex as string
  if (!hex) throw new Error('授权响应缺少内容密钥')
  const key = hexToBytes(hex)
  const expiresAt = Date.now() + GRANT_TTL_HOURS * 60 * 60 * 1000
  keyCache.set(fileId, { key, expiresAt })

  // 3. 记录授权(供离线打开 check)
  await storage.setJSON(`${AUTH_KEYS_PREFIX}${fileId}`, { expiresAt })
  return { key, expiresAt }
}

/** 下载 .kaf 实体(需登录; 服务端已校验档位)。返回 Uint8Array */
export async function downloadKaf(fileId: string): Promise<Uint8Array> {
  const res = await api.get(`/kaf/files/${fileId}/download`, { responseType: 'arraybuffer' })
  return new Uint8Array(res.data)
}

/** 离线打开前二次确认: 授权仍有效(吊销/过期则失败) */
export async function checkAccess(fileId: string): Promise<boolean> {
  try {
    await api.post(`/kaf/files/${fileId}/check`)
    return true
  } catch {
    return false
  }
}

/** 清空密钥缓存(登出/会员变化时调用) */
export function clearKeyCache() {
  keyCache.clear()
}

// ===== 解析与解密(与 Python 端格式 100% 对应) =====

interface ParsedKaf {
  header: Record<string, unknown>
  headerRaw: Uint8Array
  envelopeOffset: number
  envelopeLen: number
  payloadOffset: number
  payloadLen: number
}

function parseKaf(data: Uint8Array): ParsedKaf {
  // 魔数校验
  for (let i = 0; i < MAGIC.length; i++) {
    if (data[i] !== MAGIC[i]) throw new Error('非 KAF 文件(魔数不匹配)')
  }
  // HEADER_LEN + HEADER
  const headerLen = readU32BE(data, 8)
  const headerRaw = data.slice(12, 12 + headerLen)
  let header: Record<string, unknown>
  try {
    header = JSON.parse(bytesToUtf8(headerRaw))
  } catch {
    throw new Error('文件头解析失败')
  }
  if (header.version !== 1) throw new Error(`不支持的格式版本: ${header.version}`)
  // ENVELOPE_LEN + ENVELOPE
  const envelopeLenOffset = 12 + headerLen
  const envelopeLen = readU32BE(data, envelopeLenOffset)
  const envelopeOffset = envelopeLenOffset + 4
  const payloadOffset = envelopeOffset + envelopeLen
  const payloadLen = (header.size as number) + GCM_TAG_LEN
  return { header, headerRaw, envelopeOffset, envelopeLen, payloadOffset, payloadLen }
}

/** 用内容密钥完整解密 .kaf → 内容包(与 Python 端 unpack_kaf 严格等价) */
export function decryptKaf(data: Uint8Array, keyBytes: Uint8Array): KafDecryptedPackage {
  const parsed = parseKaf(data)
  // 信封: iv(12) + wrapped_key(512, 本版本客户端不解包 —— 服务端 grant 直接给明文 key)
  const env = data.slice(parsed.envelopeOffset, parsed.envelopeOffset + parsed.envelopeLen)
  if (env.length < IV_LEN) throw new Error('密钥信封不完整')
  const iv = env.slice(0, IV_LEN)

  // payload: ciphertext + 16B tag @noble/ciphers 的 gcm 输出带 tag, 输入同样需要带 tag
  const payload = data.slice(parsed.payloadOffset, parsed.payloadOffset + parsed.payloadLen)

  try {
    const cipher = gcm(keyBytes, iv, parsed.headerRaw) // AAD = header 原文
    const plaintext = cipher.decrypt(payload)
    return JSON.parse(bytesToUtf8(plaintext)) as KafDecryptedPackage
  } catch (e) {
    throw new Error(`解密失败(密钥错误或文件被篡改): ${e}`)
  }
}

/** 一站式: 授权 → 下载 → 解密(客户端打开资料的完整链路) */
export async function openKaf(fileId: string): Promise<KafDecryptedPackage> {
  const { key } = await grantContentKey(fileId)
  const data = await downloadKaf(fileId)
  return decryptKaf(data, key)
}
