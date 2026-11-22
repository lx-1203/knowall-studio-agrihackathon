const { startCloud, request, launchClient } = require('./smoke-flashcard-export')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const root = path.resolve(__dirname, '..')
async function smoke() {
  const output = process.env.KNOWALL_EXPORT_SMOKE_OUTPUT
  assert.ok(output && process.env.KNOWALL_DESKTOP_EXECUTABLE)
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-manual-card-'))
  const cloud = startCloud()
  let client, page, report
  try {
    const ready = await cloud.event('READY')
    const opened = await launchClient(process.env.KNOWALL_DESKTOP_EXECUTABLE, data, ready.origin, ready.accounts[0]); client = opened.client; page = opened.page
    await fs.mkdir(output, { recursive: true })
    await page.goto(new URL('/flashcards', page.url()).href)
    const row = page.getByRole('row').filter({ hasText: 'integration-biology.md' })
    await row.getByRole('button', { name: '添加卡片', exact: true }).click()
    const front = '手动补充：线粒体如何为细胞供能？', back = '**手动补充**\n\n- 细胞呼吸\n- 能量供应'
    await page.getByLabel('问题 / 正面', { exact: true }).fill(front)
    await page.getByLabel('答案 / 解释', { exact: true }).fill(back)
    await page.screenshot({ path: path.join(output, 'manual-form.png') })
    const sent = page.waitForResponse(r => r.url().endsWith('/flashcards/cards') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '保存卡片', exact: true }).click()
    const response = await sent; assert.equal(response.status(), 201)
    assert.deepEqual(response.request().postDataJSON(), { deck_id: ready.deck_id, card_type: 'qa', front, back, hints: '', tags: [] })
    const created = await response.json()
    await page.getByRole('dialog').waitFor({ state: 'detached' })
    await page.getByTestId('flashcard-session-progress').filter({ hasText: '待复习 2 张' }).waitFor()
    assert.equal((await request(page, '/api/v1/flashcards/due?deck_id=' + ready.deck_id)).data.cards.length, 2)
    for (let i = 0; i < 2; i++) {
      await page.locator('.flashcard-flip').click()
      const graded = page.waitForResponse(r => r.url().endsWith('/flashcards/review'))
      await page.getByRole('button', { name: '正常回忆 (3)', exact: true }).click()
      assert.equal((await (await graded).json()).review_count, 1)
      if (i === 0) await page.getByTestId('flashcard-session-progress').filter({ hasText: '本轮已复习 1 张，待复习 1 张' }).waitFor()
    }
    await page.getByText('已复习 2 张卡片', { exact: true }).waitFor()
    await page.screenshot({ path: path.join(output, 'manual-session-completed.png') })
    cloud.command('recycle'); await cloud.event('RECYCLED')
    const due = await request(page, '/api/v1/flashcards/due?deck_id=' + ready.deck_id)
    assert.equal(due.status, 200); assert.deepEqual(due.data.cards, []); assert.equal(due.data.due_count, 0)
    const exported = await page.request.get(new URL('/api/v1/flashcards/export/anki/' + ready.deck_id, page.url()).href, { headers: { Authorization: 'Bearer ' + await page.evaluate(() => localStorage.getItem('knowall_token')) } })
    assert.equal(exported.status(), 200)
    const apkg = path.join(data, 'manual.apkg'); await fs.writeFile(apkg, await exported.body())
    const check = spawnSync(process.env.KNOWALL_TEST_PYTHON, ['-c', `
import sys,genanki
from anki.collection import Collection,ImportAnkiPackageRequest
c=Collection(sys.argv[1])
try:
 for _ in range(2):c.import_anki_package(ImportAnkiPackageRequest(package_path=sys.argv[2]))
 assert c.note_count()==c.card_count()==2
 n=[c.get_note(i) for i in c.find_notes('') if c.get_note(i).guid==genanki.guid_for('knowall-note-v1',sys.argv[3],sys.argv[4])][0]
 assert '手动补充' in n.fields[0] and '<li>细胞呼吸</li>' in n.fields[1]
finally:c.close()
print('PASS actualAnki2imports2cards manualMarkdown')
`, path.join(data, 'consumer.anki2'), apkg, ready.deck_id, created.id], { encoding: 'utf8', windowsHide: true, env: { ...process.env, PYTHONUTF8: '1' } })
    assert.equal(check.status, 0, check.stderr)
    const denial = await page.evaluate(async ({ account, payload }) => {
      const login = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(account) }); const auth = await login.json()
      const result = await fetch('/api/v1/flashcards/cards', { method: 'POST', headers: { 'content-type': 'application/json', Authorization: 'Bearer ' + auth.access_token }, body: JSON.stringify(payload) }); return { login: login.status, status: result.status }
    }, { account: ready.accounts[1], payload: { deck_id: ready.deck_id, card_type: 'qa', front, back } })
    assert.deepEqual(denial, { login: 200, status: 404 })
    await fs.copyFile(apkg, path.join(output, 'manual.apkg'))
    report = { passed: true, checked_at: new Date().toISOString(), actual_executable: process.env.KNOWALL_DESKTOP_EXECUTABLE, actual_backend: process.env.KNOWALL_TEST_BACKEND, actual_manual_ui_post: true, actual_two_card_completed: true, actual_restarted_due_empty: true, actual_anki_two_imports_two_cards: true, other_account: denial, deterministic_seed_only: true, no_paid_call: true }
  } catch (error) { if (page) await page.screenshot({ path: path.join(output, 'failed.png') }).catch(() => {}); throw error }
  finally {
    try { if (client) await client.close() } finally { await cloud.close(); assert.equal(path.dirname(data), os.tmpdir()); assert.ok(path.basename(data).startsWith('knowall-manual-card-')); await fs.rm(data, { recursive: true, force: true }) }
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
  console.log('PASS newWindows actual extracted-cloud manualForm/create/due/2grades/restart/Anki2imports/private404/cleanup')
}
smoke().catch(error => { console.error(error); process.exitCode = 1 })
