import { test } from 'node:test'
import assert from 'node:assert/strict'
import { generationSettings, generationRequest, previewRequest, normalizeGenerationPreview, generationReceipt } from '../src/materialGeneration.ts'

const draft = { questions: '10', cards: '5', model: ' deepseek-chat ', types: ['single_choice'] }
const section = { id: 's', title: '章节', notes: '有效讲解', chunk_ids: ['c'], questions: [], flashcards: [], errors: [] }
const result = { id: 'p', document_id: 'bio', stale: false, sections: [section], quality: { blocked: 0 } }
test('generation numbers are bounded integers, never coerced or silently truncated', () => {
  for (const questions of ['-1', '101', '1.5', '1e1', '', 'x']) assert.ok(generationSettings({ ...draft, questions }).error)
  assert.ok(generationSettings({ ...draft, questions: '0', cards: '0' }).error)
  assert.equal(generationSettings({ ...draft, questions: '0', cards: '100' }).value.card_count, 100)
})
test('model and supported question types are explicit and canonical for cache reuse', () => {
  assert.ok(generationSettings({ ...draft, model: ' ' }).error)
  assert.ok(generationSettings({ ...draft, model: 'x'.repeat(101) }).error)
  assert.ok(generationSettings({ ...draft, types: [] }).error)
  assert.ok(generationSettings({ ...draft, types: ['forged'] }).error)
  assert.deepEqual(generationSettings({ ...draft, types: ['matching', 'single_choice', 'matching'] }).value.question_types, ['single_choice', 'matching'])
})
test('preview and generation remain bound to the uploaded document and existing backend parameters', () => {
  const settings = generationSettings(draft).value
  assert.deepEqual(previewRequest('bio/a', settings), { path: '/learning/documents/bio%2Fa/preview', params: { question_count: 10, card_count: 5 } })
  assert.deepEqual(generationRequest('bio/a', settings), { path: '/learning/generate', body: { document_id: 'bio/a', question_count: 10, card_count: 5, model: 'deepseek-chat', question_types: ['single_choice'], concurrency: 2, max_section_chars: 6000 } })
})
test('preview requires real sections, source version and budgets matching requested totals', () => {
  const settings = generationSettings(draft).value
  const data = { source_hash: 'source', sections: [{ id: 's', title: '细胞', question_budget: 10, card_budget: 5 }] }
  assert.equal(normalizeGenerationPreview(data, settings).sections.length, 1)
  for (const invalid of [null, { ...data, source_hash: '' }, { ...data, sections: [] }, { ...data, sections: [{ ...data.sections[0], card_budget: 4 }] }, { ...data, sections: [data.sections[0], data.sections[0]] }]) assert.throws(() => normalizeGenerationPreview(invalid, settings))
})
test('generation never accepts a stale or foreign document receipt', () => {
  for (const invalid of [null, { ...result, document_id: 'history' }, { ...result, stale: true }, { ...result, sections: [] }]) assert.throws(() => generationReceipt(invalid, 'bio'))
})
test('partial results remain usable even when every section has shortages', () => {
  const receipt = generationReceipt({ ...result, sections: [{ ...section, errors: ['数量不足'] }], coverage: { failed_sections: 1, total_sections: 1 } }, 'bio')
  assert.equal(receipt.status, 'partial')
  assert.match(receipt.message, /缺漏/)
  const failed = generationReceipt({ ...result, sections: [{ ...section, notes: '', errors: ['失败'] }] }, 'bio')
  assert.equal(failed.status, 'failed')
})
test('cache reuse and blocked questions are reported without claiming complete success', () => {
  assert.match(generationReceipt({ ...result, from_cache: true }, 'bio').message, /复用/)
  assert.equal(generationReceipt({ ...result, quality: { blocked: 2 } }, 'bio').status, 'partial')
  assert.match(generationReceipt({ ...result, quality: { blocked: 2 } }, 'bio').message, /2 道题/)
})
test('recovering a response must confirm the submitted configuration, not another new pack', () => {
  const expected = generationSettings(draft).value
  const config = generationRequest('bio', expected).body
  assert.equal(generationReceipt({ ...result, config }, 'bio', expected).status, 'complete')
  for (const changes of [{ model: 'other-model' }, { question_count: 9 }, { card_count: 4 }, { question_types: ['matching'] }]) {
    assert.throws(() => generationReceipt({ ...result, config: { ...config, ...changes } }, 'bio', expected))
  }
})
