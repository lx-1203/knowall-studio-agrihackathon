// 交叉验证脚本: 用与移动端 kaf.ts 一致的 TS 逻辑, 解密 Python 端生成的 .kaf
// 运行: npx tsx scripts/cross_check.ts
import { readFileSync } from 'fs'
import { gcm } from '@noble/ciphers/aes'

// ===== 与 kaf.ts 完全一致的函数 =====

const MAGIC = new Uint8Array([0x4b, 0x4e, 0x41, 0x46, 0x30, 0x30, 0x31, 0x00])
const IV_LEN = 12
const GCM_TAG_LEN = 16

function utf8ToBytes(s: string): Uint8Array {
  const out: number[] = []
  for (let i = 0; i < s.length; i++) {
    let cp = s.codePointAt(i)!
    if (cp > 0xffff) i++
    if (cp < 0x80) out.push(cp)
    else if (cp < 0x800) out.push(0xc0 | (cp >> 6), 0x80 | (cp & 0x3f))
    else if (cp < 0x10000) out.push(0xe0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f))
    else out.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3f), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f))
  }
  return new Uint8Array(out)
}

function bytesToUtf8(b: Uint8Array): string {
  let out = ''
  let i = 0
  while (i < b.length) {
    const b1 = b[i++]
    if (b1 < 0x80) out += String.fromCharCode(b1)
    else if (b1 < 0xe0) out += String.fromCharCode(((b1 & 0x1f) << 6) | (b[i++] & 0x3f))
    else if (b1 < 0xf0) out += String.fromCharCode(((b1 & 0x0f) << 12) | ((b[i++] & 0x3f) << 6) | (b[i++] & 0x3f))
    else {
      let cp = ((b1 & 0x07) << 18) | ((b[i++] & 0x3f) << 12) | ((b[i++] & 0x3f) << 6) | (b[i++] & 0x3f)
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
  const out = new Uint8Array(clean.length / 2)
  for (let i = 0; i < out.length; i++) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16)
  return out
}

function decryptKaf(data: Uint8Array, key: Uint8Array): any {
  // 魔数
  for (let i = 0; i < MAGIC.length; i++) if (data[i] !== MAGIC[i]) throw new Error('非 KAF 文件')
  const headerLen = readU32BE(data, 8)
  const headerRaw = data.slice(12, 12 + headerLen)
  const header = JSON.parse(bytesToUtf8(headerRaw))
  const envLenOffset = 12 + headerLen
  const envLen = readU32BE(data, envLenOffset)
  const envOffset = envLenOffset + 4
  const payloadOffset = envOffset + envLen
  const payloadLen = header.size + GCM_TAG_LEN
  const env = data.slice(envOffset, envOffset + envLen)
  if (env.length < IV_LEN) throw new Error('信封不完整')
  const iv = env.slice(0, IV_LEN)
  const payload = data.slice(payloadOffset, payloadOffset + payloadLen)
  const cipher = gcm(key, iv, headerRaw)
  const plaintext = cipher.decrypt(payload)
  return JSON.parse(bytesToUtf8(plaintext))
}

// ===== 跑交叉验证 =====

const data = readFileSync('examples/cross_test.kaf')
const key = hexToBytes(readFileSync('examples/cross_key.hex', 'utf-8').trim())

const pkg = decryptKaf(new Uint8Array(data), key)
console.log('TS 解密成功!')
console.log('  title:', pkg.title)
console.log('  subject:', pkg.subject)
console.log('  chapters:', pkg.chapters.length)
for (const ch of pkg.chapters) {
  console.log(`   - [${ch.title}] ${ch.body.slice(0, 30)}...`)
}
const expected = pkg.chapters.some((c: any) => c.body.includes('DNA 双螺旋'))
console.log('内容完整:', expected ? '✅' : '❌')
if (!expected) process.exit(1)
