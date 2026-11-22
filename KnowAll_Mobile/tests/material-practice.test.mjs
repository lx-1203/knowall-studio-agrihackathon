import { test } from 'node:test'
import assert from 'node:assert/strict'
import { answerReady, initialAnswer, moveOrder, selectMapping, toggleMultiple, readMapping, readOrder, readBlanks, setBlank, formatCorrectAnswer, inputIssue } from '../src/materialPractice.ts'
import { learningSessionRequest, learningCardReviewRequest } from '../src/materialLearning.ts'

const options = [{ label: 'A', text: '细胞核', choices: ['储存遗传信息', '提供能量'] }, { label: 'B', text: '线粒体', choices: ['储存遗传信息', '提供能量'] }]
const mapping = { id: 'm', question_text: '匹配结构与功能', question_type: 'matching', options }
const ordering = { ...mapping, question_type: 'ordering' }
test('mapping/classification retain picks and require complete candidate values', () => {
  const first = selectMapping(mapping, '', 'A', '储存遗传信息')
  assert.deepEqual(readMapping(first), { A: '储存遗传信息' })
  assert.equal(answerReady(mapping, first), false)
  const full = selectMapping(mapping, first, 'B', '提供能量')
  assert.equal(answerReady(mapping, full), true)
  assert.equal(answerReady({ ...mapping, question_type: 'classification' }, full), true)
  assert.equal(selectMapping(mapping, full, 'B', '伪造候选'), full)
  assert.equal(selectMapping(mapping, full, 'C', '提供能量'), full)
  assert.equal(answerReady(mapping, '{"A":"储存遗传信息","B":"提供能量","forged":"x"}'), false)
})
test('ordering submits a complete permutation with guarded moves and no default reset', () => {
  const initial = initialAnswer(ordering)
  assert.equal(initial, '["A","B"]')
  assert.equal(answerReady(ordering, initial), true)
  assert.equal(moveOrder(ordering, initial, 0, -1), initial)
  assert.equal(moveOrder(ordering, initial, 1, 1), initial)
  assert.equal(moveOrder(ordering, initial, 1, -1), '["B","A"]')
  assert.deepEqual(readOrder(ordering, '["B","A"]'), ['B', 'A'])
  assert.equal(answerReady(ordering, '["A","A"]'), false)
})
test('multiple choice toggles once and serializes in displayed option order', () => {
  const q = { ...mapping, question_type: 'multi_choice' }
  assert.equal(toggleMultiple(q, 'B', 'A'), 'A,B')
  assert.equal(toggleMultiple(q, 'A,B', 'A'), 'B')
  assert.equal(toggleMultiple(q, 'A,B', 'C'), 'A,B')
  assert.equal(answerReady(q, 'A,A'), false)
  assert.equal(answerReady(q, 'A,B'), true)
})
test('single choice and judgment send actual backend tokens', () => {
  assert.equal(answerReady({ ...mapping, question_type: 'single_choice' }, 'A'), true)
  assert.equal(answerReady({ ...mapping, question_type: 'single_choice' }, 'true'), false)
  assert.equal(answerReady({ ...mapping, question_type: 'true_false', options: undefined }, 'false'), true)
  assert.equal(answerReady({ ...mapping, question_type: 'true_false' }, 'A'), false)
})
test('multi-blank answers preserve text and do not accidentally split commas', () => {
  const q = { ...mapping, question_type: 'fill_blank', blank_count: 2 }
  const first = setBlank('', 2, 0, 'DNA, RNA')
  assert.deepEqual(readBlanks(first, 2), ['DNA, RNA', ''])
  assert.equal(answerReady(q, first), false)
  const full = setBlank(first, 2, 1, '细胞核')
  assert.equal(answerReady(q, full), true)
  assert.equal(setBlank(full, 2, 9, '错误位置'), full)
})
test('broken structured options are blocked instead of guessing answers', () => {
  for (const q of [{ ...mapping, options: [] }, { ...mapping, options: [null, options[1]] }, { ...mapping, options: [options[0], options[0]] }, { ...mapping, options: [{ ...options[0], choices: ['仅一项'] }, options[1]] }]) {
    assert.ok(inputIssue(q))
    assert.equal(answerReady(q, '{}'), false)
  }
  assert.deepEqual(readMapping('null'), {})
  assert.equal(answerReady({ ...mapping, question_type: 'short_answer' }, 'x'.repeat(20001)), false)
})
test('correct structured answers render labels and source text for learners', () => {
  assert.equal(formatCorrectAnswer(mapping, '{"A":"储存遗传信息","B":"提供能量"}'), 'A. 细胞核 → 储存遗传信息\nB. 线粒体 → 提供能量')
  assert.equal(formatCorrectAnswer(ordering, '["B","A"]'), 'B. 线粒体 → A. 细胞核')
  assert.equal(formatCorrectAnswer({ ...mapping, question_type: 'fill_blank', blank_count: 2 }, '["DNA","细胞核"]'), '第1空：DNA\n第2空：细胞核')
})
test('smart sessions and card ratings use existing pack endpoints and reject stale or invalid ratings', () => {
  const pack = { id: 'p/a', stale: false, sections: [] }
  assert.deepEqual(learningSessionRequest(pack, 'smart'), { path: '/learning/packs/p%2Fa/session', body: { mode: 'smart', limit: 20 } })
  assert.deepEqual(learningCardReviewRequest(pack, 'card', 4), { path: '/learning/packs/p%2Fa/review', body: { card_id: 'card', rating: 4 } })
  assert.throws(() => learningCardReviewRequest({ ...pack, stale: true }, 'card', 4), /更新/)
  for (const rating of [0, 5, 1.5, NaN]) assert.throws(() => learningCardReviewRequest(pack, 'card', rating), /评分/)
})
