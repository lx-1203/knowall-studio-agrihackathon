// Binary conversion works in Hermes as well as browsers; no Buffer/btoa dependency.
export function bytesDataUri(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  if (bytes.length > 16 * 1024 * 1024) throw new Error('预览图片过大，请使用电脑阅读')
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
  const blocks: string[] = []
  for (let start = 0; start < bytes.length; start += 12000) {
    let block = ''
    const end = Math.min(start + 12000, bytes.length)
    for (let i = start; i < end; i += 3) {
      const value = (bytes[i] << 16) | ((bytes[i + 1] || 0) << 8) | (bytes[i + 2] || 0)
      block += alphabet[value >>> 18] + alphabet[(value >>> 12) & 63] +
        (i + 1 < bytes.length ? alphabet[(value >>> 6) & 63] : '=') +
        (i + 2 < bytes.length ? alphabet[value & 63] : '=')
    }
    blocks.push(block)
  }
  return `data:image/png;base64,${blocks.join('')}`
}

export function originalKind(fileType: string): 'pdf' | 'image' | null {
  const type = (fileType || '').toLowerCase()
  return type === 'pdf' ? 'pdf' : ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff'].includes(type) ? 'image' : null
}

export function originalPosition(saved: unknown, count: number): { page: number; zoom: number } {
  const value = saved as { page?: number; zoom?: number } | null
  const page = Number.isInteger(value?.page) ? Math.max(1, Math.min(value!.page!, Math.max(1, count))) : 1
  const zoom = value?.zoom && [1, 1.5, 2].includes(value.zoom) ? value.zoom : 1
  return { page, zoom }
}
