import test from 'node:test'
import assert from 'node:assert/strict'
import { learningPackPath, learningSessionRequest, learningAnswerRequest, materialContext, normalizeLearningPack, findSourceIndex } from '../src/materialLearning.ts'

test('document learning pack requests encode the uploaded document ID', () => {
  assert.equal(learningPackPath('bio/a?b'), '/learning/documents/bio%2Fa%3Fb/pack')
})
test('an empty pack stays empty rather than exposing global material', () => {
  assert.equal(normalizeLearningPack(null), null)
  assert.equal(normalizeLearningPack([]), null)
})
test('pack content retains source chunk IDs and handles missing arrays', () => {
  const pack = normalizeLearningPack({ id: 'p', sections: [{ id: 's', title: '遗传', chunk_ids: ['c2'] }] })
  assert.deepEqual(pack.sections[0].flashcards, [])
  assert.deepEqual(pack.sections[0].chunk_ids, ['c2'])
})
test('sources locate the matching section without silently jumping to an unrelated chapter', () => {
  const chunks = [{ id: 'c1' }, { id: 'c2' }]
  assert.equal(findSourceIndex(chunks, ['c2']), 1)
  assert.equal(findSourceIndex(chunks, ['deleted']), -1)
})
test('outdated learning content cannot start a new practice session', () => {
  assert.throws(() => learningSessionRequest({ id: 'p', stale: true, sections: [] }), /更新/)
})
test('practice uses the current pack and the authenticated backend session endpoint', () => {
  assert.deepEqual(learningSessionRequest({ id: 'p/a', stale: false, sections: [] }), {
    path: '/learning/packs/p%2Fa/session', body: { mode: 'practice', limit: 20 },
  })
})
test('answer submissions preserve the question, answer and elapsed time contract', () => {
  assert.deepEqual(learningAnswerRequest('p/a', 'q1', 'A,C', 2400), {
    path: '/learning/packs/p%2Fa/answers', body: { question_id: 'q1', answer: 'A,C', time_spent_ms: 2400 },
  })
})
test('asking uses only the selected source and truncates the context explicitly', () => {
  assert.equal(materialContext({ text: '本章讲 DNA' }), '本章讲 DNA')
  assert.equal(materialContext({ text: 'x'.repeat(21000) }).length, 20000)
  assert.equal(materialContext(undefined), '')
})
