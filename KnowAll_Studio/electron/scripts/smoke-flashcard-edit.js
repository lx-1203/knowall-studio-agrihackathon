// Current packaged Windows + actual extracted cloud; no mocks or paid calls.
const { startCloud, request, launchClient } = require('./smoke-flashcard-export')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
async function smoke() {
  const output = process.env.KNOWALL_EXPORT_SMOKE_OUTPUT
  assert.ok(output && process.env.KNOWALL_DESKTOP_EXECUTABLE && process.env.KNOWALL_TEST_BACKEND)
  await fs.mkdir(output, { recursive: true })
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-card-edit-'))
  const cloud = startCloud(); let client, page, report
  try {
    const ready = await cloud.event('READY')
    const opened = await launchClient(process.env.KNOWALL_DESKTOP_EXECUTABLE, data, ready.origin, ready.accounts[1]); client = opened.client; page = opened.page
    assert.deepEqual((await request(page, '/api/v1/flashcards/decks')).data, [])
    const created = await request(page, '/api/v1/flashcards/decks', { name: '纠错生物牌组' }); assert.equal(created.status, 201)
    const deck = created.data, cards = []
    const fronts = ['细胞供能的结构是什么？', '{{c1::细胞核}} 为细胞供能', '比较细胞核与线粒体的作用']
    const corrected = ['纠错问答：细胞供能的结构是什么？', '{{c1::线粒体}} 为细胞供能', '纠错对比：细胞核与线粒体的作用']
    const answers = ['**线粒体**参与细胞呼吸。', '线粒体参与细胞呼吸。', '| 结构 | 功能 |\n| --- | --- |\n| 细胞核 | 遗传物质 |\n| 线粒体 | 供能 |']
    for (const [index, kind] of ['qa','cloze','compare'].entries()) {
      const response = await request(page, '/api/v1/flashcards/cards', { deck_id: deck.id, card_type: kind, front: fronts[index], back: '待纠错的答案', hints: '原提示' })
      assert.equal(response.status, 201); cards.push(response.data)
    }
    await page.goto(new URL('/flashcards?deck_id=' + deck.id, page.url()).href)
    const edits = []
    for (let index = 0; index < 3; index++) {
      const before = (await request(page, '/api/v1/flashcards/due?deck_id=' + deck.id)).data.cards
      const read = page.waitForResponse(r => r.url().endsWith('/flashcards/cards/' + cards[index].id) && r.request().method() === 'GET')
      await page.getByRole('button', { name: '编辑当前卡片', exact: true }).click()
      const original = await (await read).json(); assert.equal(original.id, cards[index].id)
      await page.getByLabel('答案 / 解释', { exact: true }).waitFor()
      await page.getByLabel('问题 / 正面', { exact: true }).fill(corrected[index])
      await page.getByLabel('答案 / 解释', { exact: true }).fill(answers[index])
      await page.getByLabel('提示（可选）', { exact: true }).fill('已纠错提示')
      if (!index) await page.screenshot({ path: path.join(output, 'edit-form.png'), animations: 'disabled' })
      const saved = page.waitForResponse(r => r.url().endsWith('/flashcards/cards/' + cards[index].id) && r.request().method() === 'PATCH')
      await page.getByRole('dialog').getByRole('button', { name: '保存修改', exact: true }).click()
      const response = await saved; assert.equal(response.status(), 200)
      const result = await response.json(); assert.equal(result.front, corrected[index]); assert.equal(result.card_type, cards[index].card_type)
      await page.getByRole('dialog').waitFor({ state: 'detached' })
      const after = (await request(page, '/api/v1/flashcards/due?deck_id=' + deck.id)).data.cards
      assert.deepEqual(after.find(c => c.id === cards[index].id).schedule, before.find(c => c.id === cards[index].id).schedule)
      if (index !== 1) await page.getByText(corrected[index], { exact: true }).waitFor()
      edits.push({ original, result })
      if (!index) {
        const stale = await request(page, '/api/v1/flashcards/cards/' + cards[index].id, { front: original.front, back: '不允许覆盖', hints: original.hints, original }, 'PATCH')
        assert.equal(stale.status, 409)
      }
      if (index < 2) await page.getByRole('button', { name: /下一张.*跳过/ }).click()
    }
    await page.screenshot({ path: path.join(output, 'three-types-edited.png'), animations: 'disabled' })
    for (let index = 0; index < 3; index++) {
      const good = page.getByRole('button', { name: '正常回忆 (3)', exact: true })
      if (!await good.isVisible()) await page.locator('.flashcard-flip').click()
      const graded = page.waitForResponse(r => r.url().endsWith('/flashcards/review'))
      await good.click(); assert.equal((await (await graded).json()).review_count, 1)
    }
    await page.getByText('已复习 3 张卡片', { exact: true }).waitFor()
    await page.getByPlaceholder('搜索卡片...').fill(corrected[0]); await page.getByPlaceholder('搜索卡片...').press('Enter')
    // The exact corrected QA front yields a single authorized search hit.
    await page.getByRole('button', { name: '编辑卡片', exact: true }).first().click()
    await page.getByLabel('提示（可选）', { exact: true }).fill('已复习卡也可纠错')
    const second = page.waitForResponse(r => r.url().endsWith('/flashcards/cards/' + cards[0].id) && r.request().method() === 'PATCH')
    await page.getByRole('dialog').getByRole('button', { name: '保存修改', exact: true }).click(); assert.equal((await second).status(), 200)
    await page.getByRole('dialog').waitFor({ state: 'detached' }); await page.getByText('已复习 3 张卡片', { exact: true }).waitFor()
    cloud.command('recycle'); await cloud.event('RECYCLED')
    const due = await request(page, '/api/v1/flashcards/due?deck_id=' + deck.id); assert.equal(due.status, 200); assert.deepEqual(due.data.cards, [])
    for (let index = 0; index < 3; index++) {
      const result = await request(page, '/api/v1/flashcards/cards/' + cards[index].id)
      assert.equal(result.status, 200); assert.equal(result.data.front, corrected[index]); assert.equal(result.data.back, answers[index])
    }
    const exported = await page.request.get(new URL('/api/v1/flashcards/export/anki/' + deck.id, page.url()).href, { headers: { Authorization: 'Bearer ' + await page.evaluate(() => localStorage.getItem('knowall_token')) } })
    assert.equal(exported.status(), 200); const apkg = path.join(data, 'edited.apkg'); await fs.writeFile(apkg, await exported.body())
    const check = spawnSync(process.env.KNOWALL_TEST_PYTHON, ['-c', `
import sys,genanki
from anki.collection import Collection,ImportAnkiPackageRequest
c=Collection(sys.argv[1])
try:
 for _ in range(2):c.import_anki_package(ImportAnkiPackageRequest(package_path=sys.argv[2]))
 assert c.note_count()==c.card_count()==3
 n=[c.get_note(i) for i in c.find_notes('') if c.get_note(i).guid==genanki.guid_for('knowall-note-v1',sys.argv[3],sys.argv[4])][0]
 assert n.fields[0].startswith('纠错问答') and '<strong>线粒体</strong>' in n.fields[1]
finally:c.close()
print('PASS actualAnki edited3notes3cards stableGuid')
`, path.join(data, 'consumer.anki2'), apkg, deck.id, cards[0].id], { encoding: 'utf8', windowsHide: true, env: { ...process.env, PYTHONUTF8: '1' } })
    assert.equal(check.status, 0, check.stderr)
    const denial = await page.evaluate(async ({ account, card }) => {
      const login = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(account) }); const auth = await login.json()
      const headers = { 'content-type': 'application/json', Authorization: 'Bearer ' + auth.access_token }, url = '/api/v1/flashcards/cards/' + card.id
      const read = await fetch(url, { headers }), write = await fetch(url, { method: 'PATCH', headers, body: JSON.stringify({ front: card.front, back: '不允许跨账号', hints: '', original: card }) })
      return { login: login.status, statuses: [read.status, write.status] }
    }, { account: ready.accounts[0], card: edits[0].result })
    assert.deepEqual(denial, { login: 200, statuses: [404,404] })
    await fs.copyFile(apkg, path.join(output, 'edited.apkg'))
    await page.screenshot({ path: path.join(output, 'reviewed-edit-completed.png'), animations: 'disabled' })
    report = { passed: true, at: new Date().toISOString(), actual_executable: process.env.KNOWALL_DESKTOP_EXECUTABLE, actual_backend: process.env.KNOWALL_TEST_BACKEND, actual_three_types_native_edit: true, actual_schedule_preserved: true, actual_stale_conflict_409: true, actual_non_due_search_edit: true, actual_three_real_ratings: true, actual_restarted_due_empty: true, actual_anki_three_cards_corrected_content: true, other_account: denial, edits, no_paid_call: true, no_http_mocks: true }
  } catch (error) { if (page) await page.screenshot({ path: path.join(output, 'failed.png'), animations: 'disabled' }).catch(() => {}); throw error }
  finally {
    try { if (client) await client.close() } finally { await cloud.close(); assert.equal(path.dirname(path.resolve(data)), path.resolve(os.tmpdir())); assert.ok(path.basename(data).startsWith('knowall-card-edit-')); await fs.rm(data, { recursive: true, force: true }) }
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
  console.log('PASS newWindows/extractedCloud nativeEdit3types/CAS409/schedule/search/review/restart/Anki/private404/cleanup')
}
smoke().catch(error => { console.error(error); process.exitCode = 1 })
