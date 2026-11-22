import type { Page } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
export async function imageFixture(page: Page, format: 'png' | 'jpeg' | 'webp' = 'png') {
  const data = await page.evaluate(format => {
    const canvas = document.createElement('canvas'); canvas.width = 1200; canvas.height = 600
    const ctx = canvas.getContext('2d')!; ctx.fillStyle = '#e7f4f1'; ctx.fillRect(0, 0, 1200, 600)
    ctx.fillStyle = '#147d73'; ctx.fillRect(80, 80, 350, 440); ctx.fillStyle = '#d9a653'; ctx.fillRect(560, 80, 540, 440)
    ctx.fillStyle = '#183449'; ctx.font = '40px sans-serif'; ctx.fillText('Learning diagram', 470, 570)
    return canvas.toDataURL(`image/${format}`).split(',')[1]
  }, format)
  const body = Buffer.from(data, 'base64'), folder = path.resolve('../docs/open-source-integration/round-07-fixtures')
  await mkdir(folder, { recursive: true }); await writeFile(path.join(folder, `diagram.${format}`), body)
  return body
}
