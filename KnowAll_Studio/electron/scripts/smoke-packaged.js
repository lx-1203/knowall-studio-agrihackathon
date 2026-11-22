// Exercise the built executable, not the development Electron installation.
// The loopback fixture proves desktop routing/lifecycle only; cloud account
// verification is a separate deployment integration check.
const { _electron } = require('../../frontend/node_modules/playwright')
const fs = require('node:fs/promises')
const path = require('node:path')
const http = require('node:http')
const os = require('node:os')
const assert = require('node:assert/strict')
async function smoke() {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-desktop-smoke-'))
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(__dirname, '..', 'release', 'win-unpacked', 'KnowAll Studio.exe')
  const observed = []
  const upstream = http.createServer((req, res) => {
    observed.push({ url: req.url, authorization: req.headers.authorization })
    res.setHeader('content-type', 'application/json')
    if (req.url === '/health') res.end('{"status":"ok"}')
    else if (req.url === '/api/v1/auth/login' && req.method === 'POST') res.end(JSON.stringify({ access_token: 'desktop-smoke-token', token_type: 'bearer', user: { id: 'smoke-user', username: 'Smoke Student', email: 'student@example.test', is_active: true, is_admin: false } }))
    else if (req.url === '/api/v1/auth/me') res.end(JSON.stringify({ id: 'smoke-user', username: 'Smoke Student', email: 'student@example.test', is_active: true, is_admin: false }))
    else if (req.url === '/api/v1/desktop-smoke') res.end(JSON.stringify({ authorization: req.headers.authorization }))
    else if (req.url === '/api/v1/quiz/progress') res.end(JSON.stringify({ data: { agreed: true, answers: {}, idx: 0 } }))
    else if (req.url === '/glossary_terms.kgb') res.end(JSON.stringify({ entries: [] }))
    else { res.writeHead(404); res.end('{"detail":"smoke fixture"}') }
  })
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve))
  const serverURL = `http://127.0.0.1:${upstream.address().port}`
  let client
  try {
    const env = { ...process.env, KNOWALL_DESKTOP_DATA_DIR: data }
    delete env.KNOWALL_SERVER_URL
    delete env.ELECTRON_RUN_AS_NODE
    client = await _electron.launch({ executablePath, env, timeout: 30000 })
    const settings = await client.firstWindow()
    await settings.locator('#server').fill(serverURL)
    const next = client.waitForEvent('window')
    await settings.locator('#submit').click()
    let page = await next
    await page.waitForURL('http://127.0.0.1:*/', { timeout: 30000 })
    await page.getByLabel('账号', { exact: true }).waitFor({ timeout: 30000 })
    assert.match(await page.title(), /KnowAll|智识/)
    await page.screenshot({ path: path.join(__dirname, '..', 'release', 'login-smoke.png') })
    await page.getByLabel('账号', { exact: true }).fill('smoke')
    await page.getByLabel('密码', { exact: true }).fill('smoke-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.locator('.product-shell').waitFor({ timeout: 30000 })
    assert.equal(await page.evaluate(() => localStorage.getItem('knowall_token')), 'desktop-smoke-token')
    const firstOrigin = new URL(page.url()).origin
    await client.close()
    client = await _electron.launch({ executablePath, env, timeout: 30000 })
    page = await client.firstWindow()
    await page.locator('.product-shell').waitFor({ timeout: 30000 })
    assert.equal(new URL(page.url()).origin, firstOrigin, 'normal restart keeps the configured gateway origin')
    assert.equal(await page.evaluate(() => localStorage.getItem('knowall_token')), 'desktop-smoke-token', 'normal restart preserves login')
    // Credentials and JSON are transported through the installed gateway.
    const forwarded = await page.evaluate(async () => {
      const login = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'smoke', password: 'smoke' }) })
      const auth = await login.json()
      localStorage.setItem('knowall_token', auth.access_token)
      const result = await fetch('/api/v1/desktop-smoke', { headers: { Authorization: 'Bearer ' + auth.access_token } })
      return result.json()
    })
    assert.equal(forwarded.authorization, 'Bearer desktop-smoke-token')
    await page.goto(firstOrigin + '/molbio-quiz.html')
    await page.waitForFunction(() => document.getElementById('footUser')?.textContent.includes('Smoke Student') && !document.getElementById('gate')?.classList.contains('show') && document.getElementById('syncBadge')?.textContent.includes('已同步'))
    assert.ok(observed.some(request => request.url === '/api/v1/auth/me' && request.authorization === 'Bearer desktop-smoke-token'), 'quiz auth gate uses the configured cloud service')
    assert.ok(observed.some(request => request.url === '/api/v1/quiz/progress' && request.authorization === 'Bearer desktop-smoke-token'), 'quiz progress uses authenticated same-origin cloud requests')
    assert.ok(observed.some(request => request.url === '/glossary_terms.kgb' && request.authorization === 'Bearer desktop-smoke-token'), 'glossary snapshot carries authentication')
    const oldOrigin = new URL(page.url()).origin
    const settingsAgain = client.waitForEvent('window')
    await client.evaluate(({ Menu }) => Menu.getApplicationMenu().items.find(item => item.label === '服务').submenu.items[0].click())
    const replacement = await settingsAgain
    await replacement.locator('#server').waitFor()
    assert.equal(await replacement.evaluate(() => localStorage.getItem('knowall_token')), null)
    // Revisit the SAME previous origin with a hidden window to prove its auth
    // storage was actually erased; a new random origin alone is insufficient.
    const probe = http.createServer((req, res) => { res.setHeader('content-type', 'text/html'); res.end('<!doctype html><title>Storage probe</title>') })
    await new Promise(resolve => probe.listen(Number(new URL(oldOrigin).port), '127.0.0.1', resolve))
    let probeID
    try {
      const opened = client.waitForEvent('window')
      probeID = await client.evaluate(({ BrowserWindow }, origin) => {
        const hidden = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } })
        void hidden.loadURL(origin)
        return hidden.id
      }, oldOrigin)
      const probePage = await opened
      await probePage.waitForURL(oldOrigin + '/')
      assert.equal(await probePage.evaluate(() => localStorage.getItem('knowall_token')), null, 'old service bearer token was erased')
      assert.equal(await probePage.evaluate(() => localStorage.getItem('knowall_user')), null, 'old service account was erased')
    } finally {
      if (probeID) await client.evaluate(({ BrowserWindow }, id) => BrowserWindow.fromId(id)?.destroy(), probeID)
      await new Promise(resolve => probe.close(resolve))
    }
    const storage = await client.evaluate(async ({ session }) => session.defaultSession.getStoragePath())
    assert.ok(storage, 'Chromium session remains active after switching service')
    const connection = JSON.parse(await fs.readFile(path.join(data, 'knowall', 'connection.json'), 'utf8'))
    assert.equal(connection.serverURL, serverURL)
    await replacement.screenshot({ path: path.join(__dirname, '..', 'release', 'connection-smoke.png') })
    console.log('PASS packaged executable: first-run connection, login form, restart preserves login, bearer API forwarding, quiz account/progress/glossary cloud routes, server switch erases old credentials, isolated userData')
  } finally {
    if (client) await client.close()
    await new Promise(resolve => upstream.close(resolve))
    await fs.rm(data, { recursive: true, force: true })
  }
}
smoke().catch(error => { console.error(error); process.exitCode = 1 })
