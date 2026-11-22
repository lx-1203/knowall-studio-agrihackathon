// Current packaged renderer and real native libraries; local deterministic account/question API.
const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), http = require('node:http')
const assert = require('node:assert/strict'), crypto = require('node:crypto')

async function main() {
  const root = path.resolve(__dirname, '..'), output = path.resolve(root, '../docs/open-source-integration')
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round09/win-unpacked/KnowAll Studio.exe')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-practice-'))
  const token = 'practice-fixture-token', requests = [], assets = []
  const lesson = '学习 $x^2$，价格 \\$5 和 \\$10。\n\n$$\n\\frac{1}{2}\n$$\n\n```python\ndef learn(value):\n    return value + 1\n```\n\n`$raw$`\n\n|知识|进度|\n|---|---|\n|数学|50%|\n\n$\\htmlClass{owned}{x}$\n\n$\\href{https://tracker.example.test}{remote}$\n\n![题目图片](https://tracker.example.test/question.png)'
  const analysis = '反馈 $y^2$\n\n```js\nconst score = 0.5;\n```'
  const question = { id: 'practice-q', question_type: 'fill_blank', difficulty: 'medium', difficulty_score: 0.5, cognitive_level: 'L2_understand', tags: [], question_text: lesson, options: [], answer: '1;2', analysis }
  let client, page, report
  const server = http.createServer((req, res) => {
    const uri = new URL(req.url, 'http://localhost').pathname
    res.setHeader('Content-Type', 'application/json')
    if (uri === '/health') return res.end('{"status":"ok"}')
    const user = { id: 'practice-user', username: 'practice', email: 'practice@example.test', is_active: true }
    if (uri === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: token, user }))
    if (uri === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    if (uri.startsWith('/api/v1/quiz/')) {
      if (req.headers.authorization !== `Bearer ${token}`) { res.statusCode = 401; return res.end('{}') }
      let body = ''; req.on('data', bytes => { body += bytes }); req.on('end', () => {
        const value = body ? JSON.parse(body) : undefined
        requests.push({ path: uri, authorization: req.headers.authorization, body: value })
        if (uri === '/api/v1/quiz/questions') return res.end(JSON.stringify([question]))
        if (uri === '/api/v1/quiz/exam/create') return res.end(JSON.stringify({ paper_id: 'practice-exam', title: '原生练习', question_count: 1, questions: [question] }))
        if (uri === '/api/v1/quiz/exam/submit') return res.end(JSON.stringify({ total: 1, correct: 0, score: 2.5, percentage: 50, details: [{ question_id: question.id, is_correct: false, correct_answer: '1;2', user_answer: '1', partial_score: 0.5, analysis }] }))
        if (uri === '/api/v1/quiz/interactive/start') return res.end(JSON.stringify({ session_id: 'practice-session', questions: [{ ...question, question_type: 'single_choice', options: [{ label: 'A', text: 'first' }, { label: 'B', text: 'second' }] }] }))
        if (uri === '/api/v1/quiz/interactive/submit') return res.end(JSON.stringify({ is_correct: false, correct_answer: 'B', analysis }))
        res.end('[]')
      }); return
    }
    res.end('[]')
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const env = { ...process.env, KNOWALL_DESKTOP_DATA_DIR: data }; delete env.KNOWALL_SERVER_URL; delete env.ELECTRON_RUN_AS_NODE
    client = await _electron.launch({ executablePath, env, timeout: 30000 })
    const settings = await client.firstWindow()
    await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`)
    const opened = client.waitForEvent('window'); await settings.locator('#submit').click(); page = await opened
    await page.getByLabel('账号', { exact: true }).fill('practice'); await page.getByLabel('密码', { exact: true }).fill('test-password-only')
    await page.getByRole('button', { name: '登录', exact: true }).click(); await page.locator('.product-shell').waitFor()
    page.on('request', request => assets.push(request.url()))
    await page.goto(new URL('/quiz', page.url()).href)
    const tabs = await page.getByRole('tab').allTextContents()
    await page.getByRole('tab', { name: /题库/ }).click()
    await page.getByRole('button', { name: /练习$/, exact: false }).first().click()
    const card = page.locator('#question-practice-q')
    await expect(card.locator('.katex')).toHaveCount(4)
    await expect(card).toContainText('价格 $5 和 $10')
    await expect(card.locator('p > code')).toHaveText('$raw$')
    await expect(card.locator('table td')).toHaveText(['数学', '50%'])
    await expect(card.locator('pre code')).toHaveText('def learn(value):\n    return value + 1\n')
    await expect(card.locator('pre code span').filter({ hasText: /^def$/ }).first()).toHaveCSS('color', 'rgb(166, 38, 164)')
    await expect(card.locator('.owned, .katex a, img')).toHaveCount(0)
    await card.getByPlaceholder('请输入答案...', { exact: true }).fill('1')
    await page.getByRole('button', { name: /^提交 \(/ }).click()
    await expect(card).toContainText('部分得分: 50%'); await expect(card.locator('annotation').last()).toHaveText('y^2')
    await expect(card.locator('pre code').last()).toHaveText('const score = 0.5;\n')
    assert.deepEqual(requests.find(r => r.path === '/api/v1/quiz/exam/submit').body, { paper_id: 'practice-exam', answers: { 'practice-q': '1' } })
    await page.getByText('答题回顾', { exact: true }).click()
    await expect(page.locator('#review-practice-q .katex')).toHaveCount(5)
    await page.screenshot({ path: path.join(output, 'round-09-packaged-practice.png') })
    await page.goto(new URL('/quiz/interactive/practice-summary', page.url()).href)
    await expect(page.locator('.katex')).toHaveCount(4)
    await page.getByRole('radio', { name: 'A. first', exact: true }).check()
    await expect(page.getByText('答题完成！', { exact: true })).toBeVisible()
    await expect(page.locator('annotation').last()).toHaveText('y^2')
    assert.equal(requests.find(r => r.path === '/api/v1/quiz/interactive/submit').body.user_answer, 'A')
    await page.evaluate(() => localStorage.setItem('knowall-theme', 'dark'))
    await page.goto(new URL('/quiz/interactive/practice-summary', page.url()).href)
    await expect(page.locator('pre code span').filter({ hasText: /^def$/ }).first()).toHaveCSS('color', 'rgb(86, 156, 214)')
    await page.screenshot({ path: path.join(output, 'round-09-packaged-practice-dark.png') })
    assert.ok(assets.every(url => !url.startsWith('https://tracker.example.test')))
    const resources = path.join(path.dirname(executablePath), 'resources')
    const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
    const notices = JSON.parse(await fs.readFile(path.join(output, 'round-09-runtime-notices.json'), 'utf8'))
    let checked = 0
    for (const item of notices) for (const original of item.original_notices) {
      assert.equal(sha(await fs.readFile(path.join(resources, 'frontend/third-party', item.name.replace('@', '').replace('/', '-'), original.file))), original.sha256); checked++
    }
    const chunk = assets.find(url => /\/MarkdownContent-[^/]+\.js/.test(url)); assert.ok(chunk, 'Shared native renderer chunk must execute')
    report = { at: new Date().toISOString(), executable: executablePath, app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))), frontend_index_sha256: sha(await fs.readFile(path.join(resources, 'frontend/index.html'))), native_content_chunk_sha256: sha(await fs.readFile(path.join(resources, 'frontend/assets', path.basename(new URL(chunk).pathname)))), original_notice_files_checked: checked, requests, assets, tabs, passed: true,
      observations: { real_question_native_math_code: true, escaped_code_currency: true, safe_untrusted_commands_images: true, original_payload_and_partial_score: true, native_review: true, interactive_submission_and_recap: true, native_theme: true },
      simulation: 'Actual packaged renderer/gateway/native libraries with temporary deterministic local account/question HTTP fixture; cloud persistence separately verified by real worker smoke.' }
  } catch (error) {
    await fs.writeFile(path.join(output, `round-09-packaged-practice-diagnostics-${process.env.OSS_EVIDENCE_STAGE || 'current'}.json`), JSON.stringify({ url: page?.url(), snapshot: page ? await page.locator('body').ariaSnapshot() : '', requests, assets }, null, 2))
    throw error
  } finally {
    if (client) await client.close(); await new Promise(resolve => server.close(resolve))
    assert.equal(path.dirname(data), os.tmpdir()); await fs.rm(data, { recursive: true, force: true })
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'round-09-packaged-practice.json'), JSON.stringify(report, null, 2))
  console.log('PASS actual packaged practice: native question/math/code/GFM, safe untrusted content, original scoring payload, review/interactive/theme/notices/cleanup')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
