// Actual packaged renderer/gateway with deterministic local image/account fixtures.
const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const http = require('node:http')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')

async function main() {
  const root = path.resolve(__dirname, '..'), output = path.resolve(root, '../docs/open-source-integration')
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round07/win-unpacked/KnowAll Studio.exe')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-image-'))
  const images = {}
  for (const type of ['png', 'jpeg', 'webp']) images[type] = await fs.readFile(path.join(output, 'round-07-fixtures', `diagram.${type}`))
  const requests = [], assets = [], token = 'image-test-token'
  let client, report
  const server = http.createServer((req, res) => {
    requests.push({ url: req.url, authorization: req.headers.authorization })
    res.setHeader('Content-Type', 'application/json')
    const user = { id: 'image-user', username: 'image', email: 'image@example.test', is_active: true }
    if (req.url === '/health') return res.end('{"status":"ok"}')
    if (req.url === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: token, user }))
    if (req.url === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    const match = /^\/api\/v1\/documents\/image-(png|jpeg|webp|pptx)(\/chunks|\/raw|\/slides(?:\/[12])?)?$/.exec(req.url)
    if (match) {
      if (req.headers.authorization !== `Bearer ${token}`) { res.statusCode = 401; return res.end('{}') }
      const type = match[1], suffix = match[2]
      if (suffix === '/chunks') return res.end(JSON.stringify({ chunks: [{ id: 'section', index: 0, text: '学习图表原文', page_range: 'p1' }] }))
      if (suffix === '/slides') return res.end(JSON.stringify({ slides: [1, 2].map(i => ({ index: i - 1, image_url: `/api/v1/documents/image-pptx/slides/${i}` })) }))
      if (suffix === '/raw' || /^\/slides\/[12]$/.test(suffix || '')) {
        const format = type === 'pptx' ? 'png' : type
        res.setHeader('Content-Type', `image/${format}`); return res.end(images[format])
      }
      return res.end(JSON.stringify({ id: `image-${type}`, filename: `学习图表.${type}`, file_type: type, status: 'ready', total_chunks: 1 }))
    }
    res.end('[]')
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const env = { ...process.env, KNOWALL_DESKTOP_DATA_DIR: data }
    delete env.KNOWALL_SERVER_URL; delete env.ELECTRON_RUN_AS_NODE
    client = await _electron.launch({ executablePath, env, timeout: 30000 })
    const settings = await client.firstWindow()
    await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`)
    const opened = client.waitForEvent('window'); await settings.locator('#submit').click(); const page = await opened
    await page.getByLabel('账号', { exact: true }).fill('image')
    await page.getByLabel('密码', { exact: true }).fill('safe-test-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.locator('.product-shell').waitFor()
    page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/assets/')) assets.push(r.url()) })
    const preview = page.locator('.ant-image-preview-img')
    for (const type of ['png', 'jpeg', 'webp']) {
      await page.goto(new URL(`/materials/image-${type}`, page.url()).href)
      const img = page.getByRole('img', { name: `学习图表.${type}`, exact: true }).first()
      await expect.poll(() => img.evaluate(el => el.naturalWidth)).toBe(1200)
      const opener = page.getByRole('button', { name: '放大查看', exact: true })
      await opener.focus(); await page.keyboard.press('Enter'); await expect(preview).toBeVisible()
      await expect(preview).toHaveAttribute('src', /^blob:/)
      await expect.poll(() => preview.evaluate(el => el.naturalWidth)).toBe(1200)
      await expect(page.getByRole('button', { name: '缩小', exact: true })).toBeDisabled()
      await page.getByRole('button', { name: '放大', exact: true }).focus(); await page.keyboard.press('Enter')
      await expect.poll(() => preview.evaluate(el => el.style.transform)).toContain('scale3d(1.5')
      await page.getByRole('button', { name: '右转', exact: true }).click()
      await expect.poll(() => preview.evaluate(el => el.style.transform)).toContain('rotate(90deg)')
      await page.getByRole('button', { name: '横向翻转', exact: true }).click()
      await expect.poll(() => preview.evaluate(el => el.style.transform)).toContain('scale3d(-1.5')
      await page.getByRole('button', { name: '恢复', exact: true }).click()
      await expect.poll(() => preview.evaluate(el => el.style.transform)).toContain('rotate(0deg)')
      await expect(preview).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)')
      if (type === 'png') await page.screenshot({ path: path.join(output, 'round-07-packaged-image.png') })
      await page.keyboard.press('Escape'); await expect(preview).not.toBeVisible(); await expect(opener).toBeFocused()
    }
    await page.evaluate(() => localStorage.setItem('knowall-theme', 'dark')); await page.reload()
    await expect(page.locator('.image-preview-viewport')).toHaveCSS('background-color', 'rgb(14, 29, 42)')
    await page.goto(new URL('/materials/image-pptx', page.url()).href)
    await expect(page.getByRole('img', { name: '幻灯片 1', exact: true }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: '上一页', exact: true })).toBeDisabled()
    assert.equal(requests.some(r => r.url === '/api/v1/documents/image-pptx/slides/2'), false)
    await page.getByRole('button', { name: '放大查看', exact: true }).click(); await expect(preview).toBeVisible()
    await page.getByRole('button', { name: '关闭', exact: true }).click(); await expect(preview).not.toBeVisible()
    await page.getByRole('button', { name: '下一页', exact: true }).click()
    await expect(page.getByRole('img', { name: '幻灯片 2', exact: true }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: '上一页', exact: true })).toBeEnabled()
    await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeDisabled()
    await page.screenshot({ path: path.join(output, 'round-07-packaged-slides.png') })
    for (const type of ['png', 'jpeg', 'webp']) assert.ok(requests.some(r => r.url === `/api/v1/documents/image-${type}/raw`))
    assert.ok(requests.some(r => r.url === '/api/v1/documents/image-pptx/slides/2'))
    assert.ok(requests.filter(r => r.url.startsWith('/api/v1/documents/')).every(r => r.authorization === `Bearer ${token}`))
    const resources = path.join(path.dirname(executablePath), 'resources')
    const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
    const notices = JSON.parse(await fs.readFile(path.join(output, 'round-07-runtime-notices.json'), 'utf8'))
    let checked = 0
    for (const item of notices) for (const original of item.original_notices) {
      const bytes = await fs.readFile(path.join(resources, 'frontend/third-party', item.name.replace('@', '').replace('/', '-'), original.file))
      assert.equal(sha(bytes), original.sha256); checked++
    }
    const readerChunks = {}
    for (const name of ['ImagePreview', 'PptxPreview']) {
      const chunk = assets.find(url => new RegExp(`/${name}-[^/]+\\.js`).test(url)); assert.ok(chunk, name)
      readerChunks[name] = sha(await fs.readFile(path.join(resources, 'frontend/assets', path.basename(new URL(chunk).pathname))))
    }
    report = { at: new Date().toISOString(), executable: executablePath, app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))), frontend_index_sha256: sha(await fs.readFile(path.join(resources, 'frontend/index.html'))), reader_chunks_sha256: readerChunks, observations: { png_jpeg_webp: true, keyboard_native_zoom_rotate_flip_reset: true, escape_focus: true, dark: true, slide_paging_no_prefetch: true, bearer_blob: true }, requests, assets, original_notice_files_checked: checked, passed: true, simulation: 'Actual packaged renderer/gateway with deterministic local account/image HTTP fixture; no formal production service.' }
  } finally {
    if (client) await client.close()
    await new Promise(resolve => server.close(resolve))
    assert.equal(path.dirname(data), os.tmpdir()); await fs.rm(data, { recursive: true, force: true })
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'round-07-packaged-image.json'), JSON.stringify(report, null, 2))
  console.log('PASS actual packaged image/PPT: original decode, native keyboard transforms/Escape/focus, theme/paging/no-prefetch, Bearer/Blob, notices, cleanup')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
