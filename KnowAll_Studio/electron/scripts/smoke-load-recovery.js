const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), http = require('node:http'), assert = require('node:assert/strict'), crypto = require('node:crypto')

async function main() {
  const root = path.resolve(__dirname, '..'), out = path.resolve(root, '../docs/open-source-integration')
  const exe = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round13/win-unpacked/KnowAll Studio.exe')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-load-recovery-'))
  const token = 'load-recovery-temporary-token', requests = [], counts = {}, ready = {}, aborted = [], timers = new Set()
  const coverage = { total_knowledge_points: 8, covered_by_questions: 4, covered_by_flashcards: 2, full_coverage: 2, full_coverage_rate: .25, coverage_rate_questions: .5, coverage_rate_flashcards: .25, uncovered_points: [], weak_points: [] }
  const quiz = { session_id: 'temporary-session', questions: [{ id: 'q', question_text: '当前包恢复后的题目', question_type: 'single_choice', options: [{ label: 'A', text: '选项一' }, { label: 'B', text: '选项二' }], knowledge_points: [] }] }
  let client, page, report
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost'), uri = url.pathname
    res.setHeader('Content-Type', 'application/json')
    const user = { id: 'recovery-user', username: 'recovery', email: 'recovery@example.test', is_active: true }
    if (uri === '/health') return res.end('{"status":"ok"}')
    if (uri === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: token, user }))
    if (uri === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    let text = ''; req.on('data', b => { text += b }); req.on('end', () => {
      const body = text ? JSON.parse(text) : undefined
      requests.push({ path: uri, query: url.search, method: req.method, authorization: req.headers.authorization, body })
      counts[uri] = (counts[uri] || 0) + 1
      const respond = (value, status = 200) => { res.statusCode = status; res.end(JSON.stringify(value)) }
      const later = value => { const timer = setTimeout(() => { timers.delete(timer); respond(value) }, 10000); timers.add(timer) }
      if (/^\/api\/v1\/(knowledge|agents|quiz|memory)\//.test(uri)) {
        if (req.headers.authorization !== `Bearer ${token}`) return respond({}, 401)
        if (uri === '/api/v1/quiz/interactive/start') return ready.quiz ? respond(quiz) : respond({ detail: 'PRIVATE_TRACE_DO_NOT_SHOW' }, 503)
        if (uri === '/api/v1/agents/orchestrate') return respond({ results: { question_bank: { status: 'success' }, mindmap: { status: 'skipped' }, study_plan: { status: 'skipped' }, language: { status: 'skipped' } } })
        if (uri === '/api/v1/memory/stats') return respond(null)
        if (uri.endsWith('/mindmap')) return ready.map ? respond(null) : respond({}, 503)
        if (uri.startsWith('/api/v1/knowledge/summary/')) {
          const id = uri.split('/').at(-1), summary = { id, content_md: `# 当前包原文 ${id}\n\n可用正文保持显示。`, document_ids: ['source-doc'], node_count: 8 }
          if (id === 'slow') return later(summary)
          if (id === 'missing') return respond({}, 404)
          return ready.summary || id !== 'recover' ? respond(summary) : respond({ detail: 'PRIVATE_TRACE_DO_NOT_SHOW' }, 503)
        }
        if (uri.startsWith('/api/v1/knowledge/coverage/')) return ready.coverage ? respond(coverage) : respond({}, 503)
      }
      respond([])
    })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const env = { ...process.env, KNOWALL_DESKTOP_DATA_DIR: data }; delete env.KNOWALL_SERVER_URL; delete env.ELECTRON_RUN_AS_NODE
    client = await _electron.launch({ executablePath: exe, env, timeout: 30000 })
    const settings = await client.firstWindow()
    await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`)
    const opened = client.waitForEvent('window'); await settings.locator('#submit').click(); page = await opened
    await page.getByLabel('账号', { exact: true }).fill('recovery'); await page.getByLabel('密码', { exact: true }).fill('temporary-fixture-only')
    await page.getByRole('button', { name: '登录', exact: true }).click(); await page.locator('.product-shell').waitFor()
    await page.setViewportSize({ width: 1600, height: 950 }); page.on('requestfailed', r => aborted.push({ url: r.url(), failure: r.failure() }))
    const origin = new URL(page.url()).origin, go = route => page.goto(`${origin}${route}`)
    await go('/summary/recover'); await expect(page.getByText('知识总纲加载失败', { exact: true })).toBeVisible({ timeout: 15000 })
    assert.equal(counts['/api/v1/knowledge/summary/recover'], 3)
    await expect(page.getByText(/PRIVATE_TRACE/)).toBeHidden()
    await page.screenshot({ path: path.join(out, 'round-13-packaged-main-error.png'), animations: 'disabled' })
    ready.summary = true; await page.getByRole('button', { name: '重新加载', exact: true }).click()
    await expect(page.locator('.markdown-body')).toContainText('当前包原文 recover'); assert.equal(counts['/api/v1/knowledge/summary/recover'], 4)
    await page.getByRole('tab', { name: /覆盖率$/ }).click(); await expect(page.getByText('覆盖率加载失败', { exact: true })).toBeVisible({ timeout: 15000 })
    const beforeSummary = counts['/api/v1/knowledge/summary/recover']; ready.coverage = true
    await page.getByRole('button', { name: '重新加载覆盖率', exact: true }).click(); await expect(page.getByText('25%', { exact: true })).toBeVisible()
    assert.equal(counts['/api/v1/knowledge/summary/recover'], beforeSummary)
    await page.getByRole('button', { name: /一键生成全部配套$/ }).first().click()
    await page.getByRole('tab', { name: /题库 \(\?\)$/ }).click(); await expect(page.getByText('题库预览加载失败', { exact: true })).toBeVisible()
    await expect(page.getByText('尚未生成题目', { exact: true })).toBeHidden(); assert.equal(counts['/api/v1/quiz/interactive/start'], 1)
    ready.quiz = true; await page.getByRole('button', { name: '重新加载题库预览', exact: true }).evaluate(el => { el.click(); el.click() })
    await expect(page.getByText('当前包恢复后的题目', { exact: true })).toBeVisible()
    assert.equal(counts['/api/v1/quiz/interactive/start'], 2); assert.equal(counts['/api/v1/agents/orchestrate'], 1)
    assert.deepEqual(requests.find(r => r.path === '/api/v1/agents/orchestrate').body, { summary_id: 'recover', document_id: 'source-doc', config: { question_count: 30 } })
    await page.screenshot({ path: path.join(out, 'round-13-packaged-preview-recovered.png'), animations: 'disabled' })
    ready.coverage = false; await go('/coverage/recover'); await expect(page.getByText('覆盖率报告加载失败', { exact: true })).toBeVisible({ timeout: 15000 })
    ready.coverage = true; await page.getByRole('button', { name: '重新加载', exact: true }).click(); await expect(page.getByRole('heading', { name: '覆盖率报告', exact: true })).toBeVisible()
    ready.quiz = false; await go('/quiz/interactive/recover'); await expect(page.getByText('题目加载失败', { exact: true })).toBeVisible()
    assert.equal(counts['/api/v1/quiz/interactive/start'], 3)
    ready.quiz = true; await page.getByRole('button', { name: '重新加载', exact: true }).click(); await expect(page.getByText('当前包恢复后的题目', { exact: true })).toBeVisible()
    assert.equal(counts['/api/v1/quiz/interactive/start'], 4)
    await go('/mindmap/recover'); await expect(page.getByText('思维导图加载失败', { exact: true })).toBeVisible({ timeout: 15000 })
    ready.map = true; await page.getByRole('button', { name: '重新加载', exact: true }).click(); await expect(page.getByText('暂无思维导图', { exact: true })).toBeVisible()
    await go('/summary/missing'); await expect(page.getByText('该内容不存在或已被移除。', { exact: true })).toBeVisible()
    await go('/summary/slow'); await expect.poll(() => counts['/api/v1/knowledge/summary/slow']).toBe(1)
    await page.evaluate(() => { history.pushState(null, '', '/summary/other'); dispatchEvent(new PopStateEvent('popstate')) })
    await expect(page.locator('.markdown-body')).toContainText('当前包原文 other'); await expect.poll(() => aborted.some(r => r.url.endsWith('/summary/slow'))).toBe(true)
    const sessions = requests.filter(r => r.path === '/api/v1/quiz/interactive/start')
    assert.deepEqual(sessions.map(r => new URLSearchParams(r.query).get('count')), ['5', '5', '20', '20'])
    assert.ok(sessions.every(r => new URLSearchParams(r.query).get('summary_id') === 'recover'))
    assert.ok(requests.filter(r => /^\/api\/v1\/(knowledge|agents|quiz|memory)\//.test(r.path)).every(r => r.authorization === `Bearer ${token}`))
    const sha = b => crypto.createHash('sha256').update(b).digest('hex'), resources = path.join(path.dirname(exe), 'resources')
    const notices = JSON.parse(await fs.readFile(path.join(out, 'round-13-runtime-notices.json'), 'utf8')); let checked = 0
    for (const item of notices) for (const n of item.original_notices) { assert.equal(sha(await fs.readFile(path.join(resources, 'frontend/third-party', item.name.replace('@', '').replace('/', '-'), n.file))), n.sha256); checked++ }
    report = { at: new Date().toISOString(), executable: exe, app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))), frontend_index_sha256: sha(await fs.readFile(path.join(resources, 'frontend/index.html'))), original_notice_files_checked: checked, passed: true, requests, counts, aborted, observations: { main_failure_recovered: true, auxiliary_recovery_does_not_reload_source: true, no_unrequested_generation: true, explicit_preview_double_click_one_session: true, legacy_session_no_automatic_retry: true, all_four_pages_recovered: true, legitimate_null_mindmap: true, missing_404: true, source_count_auth_preserved: true, route_abort: true }, simulation: 'Current real packaged Electron renderer/gateway, local HTTP fault fixture, temporary account/content. No paid AI or production service; actual cloud workers verified separately.' }
  } catch (error) {
    await fs.writeFile(path.join(out, 'round-13-packaged-load-recovery-diagnostics.json'), JSON.stringify({ url: page?.url(), snapshot: page ? await page.locator('body').ariaSnapshot() : '', requests, counts, aborted }, null, 2)); throw error
  } finally {
    for (const timer of timers) clearTimeout(timer)
    if (client) await client.close()
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve))
    assert.equal(path.dirname(data), os.tmpdir()); await fs.rm(data, { recursive: true, force: true })
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(out, 'round-13-packaged-load-recovery.json'), JSON.stringify(report, null, 2))
  console.log('PASS current packaged native load failures/manual recovery/session once/null/404/abort/auth/original notices/cleanup')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
