import test from 'node:test'
import assert from 'node:assert/strict'
import { searchPlan, normalizeSearchScope, normalizeSearchKind } from '../src/unifiedSearch.ts'

test('empty and whitespace-only queries never request any catalog', () => {
  assert.deepEqual(searchPlan('', 'mine', 'all'), [])
  assert.deepEqual(searchPlan('   ', 'shared', 'all'), [])
})
test('one personal query covers documents, saved knowledge, cards and terms', () => {
  const plan = searchPlan(' 导数 ', 'mine', 'all')
  assert.deepEqual(plan.map(item => item.key), ['docs', 'points', 'cards', 'terms'])
  assert.equal(plan[0].body.query, '导数')
  assert.ok(plan.every(item => item.key !== 'shared'))
})
test('shared search never calls the personal or legacy unscoped sources', () => {
  const plan = searchPlan('导数', 'shared', 'points')
  assert.equal(plan.length, 1)
  assert.equal(plan[0].path, '/shared-library/search')
  assert.equal(plan[0].params.q, '导数')
})
test('each type filter queries only that type with the same keyword', () => {
  for (const key of ['docs', 'points', 'cards', 'terms']) {
    assert.deepEqual(searchPlan('导数', 'mine', key).map(item => item.key), [key])
  }
})
test('load-more requests stay within the backend 40-result bound', () => {
  const [shared] = searchPlan('导数', 'shared', 'all', 100)
  assert.equal(shared.params.limit, 40)
  assert.equal(searchPlan('导数', 'mine', 'cards', -5)[0].params.top_k, 10)
})
test('related-document search remains available as a secondary option', () => {
  const [docs] = searchPlan('导数', 'mine', 'docs', 10, 'related')
  assert.equal(docs.path, '/search/rag')
  assert.equal(docs.body.query, '导数')
})
test('legacy and malformed route filters resolve to explicit safe defaults', () => {
  assert.equal(normalizeSearchScope('shared'), 'shared')
  assert.equal(normalizeSearchScope('unknown'), 'mine')
  assert.equal(normalizeSearchKind('knowledge'), 'points')
  assert.equal(normalizeSearchKind('terms'), 'terms')
  assert.equal(normalizeSearchKind('unknown'), 'all')
})
