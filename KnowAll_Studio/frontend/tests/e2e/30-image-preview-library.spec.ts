import { test, expect, E2E_TOKEN } from './fixtures'
import type { Page } from '@playwright/test'
import { imageFixture } from './image-fixture'

async function file(page: Page, type: string, body: Buffer) {
  const headers: string[] = []
  await page.route('**/api/v1/documents/image-fixture', r => r.fulfill({ json: { id: 'image-fixture', filename: `diagram.${type}`, file_type: type, status: 'ready', total_chunks: 1 } }))
  await page.route('**/api/v1/documents/image-fixture/chunks', r => r.fulfill({ json: { chunks: [{ id: 'image-section', index: 0, text: '学习图表', page_range: 'p1' }] } }))
  await page.route('**/api/v1/documents/image-fixture/raw', r => { headers.push(r.request().headers().authorization); return r.fulfill({ body, contentType: `image/${type}` }) })
  return headers
}
const preview = (page: Page) => page.locator('.ant-image-preview-img')
async function slides(page: Page, id: string, body: Buffer, count = 2) {
  const reads: { url: string; auth: string }[] = []
  await page.route(`**/api/v1/documents/${id}/slides`, r => { reads.push({ url: r.request().url(), auth: r.request().headers().authorization }); return r.fulfill({ json: { slides: Array.from({ length: count }, (_, i) => ({ index: i, image_url: `/api/v1/documents/${id}/slides/${i + 1}` })) } }) })
  await page.route(new RegExp(`/api/v1/documents/${id}/slides/\\d+$`), r => { reads.push({ url: r.request().url(), auth: r.request().headers().authorization }); return r.fulfill({ body, contentType: 'image/png' }) })
  return reads
}

for (const type of ['png', 'jpeg', 'webp'] as const) {
  test(`protected ${type} opens native original preview and keeps authorization`, async ({ page }) => {
    await page.goto('/dashboard'); const body = await imageFixture(page, type)
    const headers = await file(page, type, body)
    await page.goto('/materials/image-fixture')
    const img = page.getByRole('img', { name: `diagram.${type}`, exact: true }).first()
    await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(1200)
    await page.getByRole('button', { name: '放大查看', exact: true }).click()
    await expect(preview(page)).toBeVisible()
    await expect(preview(page)).toHaveAttribute('src', /^blob:/)
    await expect.poll(() => preview(page).evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(1200)
    expect(headers).toEqual([`Bearer ${E2E_TOKEN}`])
    await page.keyboard.press('Escape'); await expect(preview(page)).not.toBeVisible()
  })
}

test('native zoom/rotate/flip actions work with keyboard and restore opener focus', async ({ page }) => {
  await page.goto('/dashboard'); const body = await imageFixture(page)
  await file(page, 'png', body); await page.goto('/materials/image-fixture')
  const opener = page.getByRole('button', { name: '放大查看', exact: true })
  await opener.focus(); await page.keyboard.press('Enter'); await expect(preview(page)).toBeVisible()
  await expect(page.getByRole('button', { name: '缩小', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '放大', exact: true }).focus(); await page.keyboard.press('Enter')
  await expect.poll(() => preview(page).evaluate(el => el.style.transform)).toContain('scale3d(1.5')
  await page.getByRole('button', { name: '右转', exact: true }).click()
  await expect.poll(() => preview(page).evaluate(el => el.style.transform)).toContain('rotate(90deg)')
  await page.getByRole('button', { name: '横向翻转', exact: true }).click()
  await expect.poll(() => preview(page).evaluate(el => el.style.transform)).toContain('scale3d(-1.5')
  await page.getByRole('button', { name: '恢复', exact: true }).click()
  await expect.poll(() => preview(page).evaluate(el => el.style.transform)).toContain('rotate(0deg)')
  await page.keyboard.press('Escape'); await expect(preview(page)).not.toBeVisible(); await expect(opener).toBeFocused()
  await opener.click(); await page.getByRole('button', { name: '关闭', exact: true }).click(); await expect(preview(page)).not.toBeVisible()
})

test('decode failure recovers on a new URL and honors height/dark token', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('knowall-theme', 'dark'))
  await page.goto('/dashboard'); const body = await imageFixture(page)
  await page.route('**/image-broken', r => r.fulfill({ body: 'not an image', contentType: 'image/png' }))
  await page.route('**/image-good', r => r.fulfill({ body, contentType: 'image/png' }))
  await page.evaluate(async () => (await import('/.tmp/imageHarness.tsx')).show('/image-broken'))
  await expect(page.locator('#image-test-mount')).toContainText(/图片加载失败|图片未能显示/)
  await page.evaluate(async () => (await import('/.tmp/imageHarness.tsx')).show('/image-good'))
  const img = page.locator('#image-test-mount img').first()
  await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(1200)
  await expect(page.locator('#image-test-mount')).not.toContainText('图片未能显示')
  await expect(page.locator('#image-test-mount .image-preview-viewport')).toHaveCSS('max-height', '321px')
  await expect(page.locator('#image-test-mount .image-preview-viewport')).toHaveCSS('background-color', 'rgb(14, 29, 42)')
  await expect(img).toHaveCSS('max-height', '321px')
})

test('file switch closes old preview and releases its authorized Blob', async ({ page }) => {
  await page.goto('/dashboard'); const body = await imageFixture(page)
  await file(page, 'png', body)
  await page.route('**/api/v1/documents/image-next', r => r.fulfill({ json: { id: 'image-next', filename: 'next.png', file_type: 'png', status: 'ready', total_chunks: 1 } }))
  await page.route('**/api/v1/documents/image-next/chunks', r => r.fulfill({ json: { chunks: [] } }))
  await page.route('**/api/v1/documents/image-next/raw', r => r.fulfill({ body, contentType: 'image/png' }))
  await page.addInitScript(() => { const state = window as unknown as { imageRevoked: string[] }; state.imageRevoked = []; const original = URL.revokeObjectURL; URL.revokeObjectURL = url => { state.imageRevoked.push(url); original.call(URL, url) } })
  await page.goto('/materials/image-fixture'); await page.getByRole('button', { name: '放大查看', exact: true }).click()
  const old = await preview(page).getAttribute('src')
  // Client navigation preserves the app realm so Blob cleanup is observable.
  await page.evaluate(() => { history.pushState({}, '', '/materials/image-next'); window.dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.getByRole('img', { name: 'next.png', exact: true }).first()).toBeVisible()
  await expect(preview(page)).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => (window as unknown as { imageRevoked: string[] }).imageRevoked)).toContain(old)
})

test('slide current-page authorization, native preview, page bounds and no eager raw prefetch', async ({ page }) => {
  await page.goto('/dashboard'); const body = await imageFixture(page)
  const reads = await slides(page, 'slide-fixture', body)
  await page.evaluate(async () => (await import('/.tmp/imageHarness.tsx')).show('slide-fixture', true))
  await expect(page.getByRole('img', { name: '幻灯片 1', exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '上一页', exact: true })).toBeDisabled()
  expect(reads.some(r => r.url.endsWith('/2'))).toBe(false)
  await page.getByRole('button', { name: '放大查看', exact: true }).click(); await expect(preview(page)).toBeVisible(); await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.getByRole('img', { name: '幻灯片 2', exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '上一页', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeDisabled()
  await expect(page.locator('#image-test-mount .image-preview-viewport')).toHaveCSS('max-height', '321px')
  expect(reads.every(r => r.auth === `Bearer ${E2E_TOKEN}`)).toBe(true)
})

test('slide image 401 retries; empty and unsupported slides remain readable', async ({ page }) => {
  await page.goto('/dashboard'); const body = await imageFixture(page)
  await slides(page, 'slide-retry', body, 1)
  let attempts = 0
  await page.route('**/api/v1/documents/slide-retry/slides/1', r => { expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`); return ++attempts === 1 ? r.fulfill({ status: 401 }) : r.fulfill({ body, contentType: 'image/png' }) })
  await page.evaluate(async () => (await import('/.tmp/imageHarness.tsx')).show('slide-retry', true))
  await page.getByRole('button', { name: '重试当前页', exact: true }).click()
  await expect(page.getByRole('img', { name: '幻灯片 1', exact: true }).first()).toBeVisible(); expect(attempts).toBe(2)
  await slides(page, 'slide-empty', body, 0)
  await page.evaluate(async () => (await import('/.tmp/imageHarness.tsx')).show('slide-empty', true)); await expect(page.locator('#image-test-mount')).toContainText('无幻灯片')
  await page.route('**/api/v1/documents/slide-old/slides', r => r.fulfill({ status: 400 }))
  await page.evaluate(async () => (await import('/.tmp/imageHarness.tsx')).show('slide-old', true)); await expect(page.locator('#image-test-mount')).toContainText('仅支持 PPTX 格式')
})

test('unsafe external slide URL is rejected without leaking Bearer', async ({ page }) => {
  await page.goto('/dashboard'); let leaks = 0
  await page.route('https://untrusted.example/**', r => { leaks++; return r.abort() })
  await page.route('**/api/v1/documents/slide-unsafe/slides', r => r.fulfill({ json: { slides: [{ index: 0, image_url: 'https://untrusted.example/secret.png' }] } }))
  await page.evaluate(async () => (await import('/.tmp/imageHarness.tsx')).show('slide-unsafe', true))
  await expect(page.locator('#image-test-mount')).toContainText('幻灯片图片未能加载')
  expect(leaks).toBe(0)
})

test('slide 204 is recoverable and changing credentials closes the old authorized viewer', async ({ page }) => {
  await page.goto('/dashboard'); const body = await imageFixture(page)
  await slides(page, 'slide-credentials', body, 1)
  const reads: string[] = []
  await page.route('**/api/v1/documents/slide-credentials/slides/1', r => {
    reads.push(r.request().headers().authorization)
    return reads.length === 1 ? r.fulfill({ status: 204 }) : r.fulfill({ body, contentType: 'image/png' })
  })
  await page.evaluate(async () => (await import('/.tmp/imageHarness.tsx')).show('slide-credentials', true))
  await page.getByRole('button', { name: '重试当前页', exact: true }).click()
  await page.getByRole('button', { name: '放大查看', exact: true }).click(); await expect(preview(page)).toBeVisible()
  const old = await preview(page).getAttribute('src')
  await page.evaluate(async () => {
    (await import('/src/authToken.ts')).setAuthToken('image-next-user')
    ;(await import('/.tmp/imageHarness.tsx')).show('slide-credentials', true)
  })
  await expect(preview(page)).toHaveCount(0)
  await expect(page.getByRole('img', { name: '幻灯片 1', exact: true }).first()).not.toHaveAttribute('src', old!)
  expect(reads).toEqual([`Bearer ${E2E_TOKEN}`, `Bearer ${E2E_TOKEN}`, 'Bearer image-next-user'])
})

test('switching presentations destroys the active viewer and resets the page', async ({ page }) => {
  await page.goto('/dashboard'); const body = await imageFixture(page)
  await slides(page, 'slides-before', body); await slides(page, 'slides-after', body, 1)
  await page.evaluate(async () => (await import('/.tmp/imageHarness.tsx')).show('slides-before', true))
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await page.getByRole('button', { name: '放大查看', exact: true }).click(); await expect(preview(page)).toBeVisible()
  await page.evaluate(async () => (await import('/.tmp/imageHarness.tsx')).show('slides-after', true))
  await expect(preview(page)).toHaveCount(0)
  await expect(page.getByRole('img', { name: '幻灯片 1', exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeDisabled()
})
