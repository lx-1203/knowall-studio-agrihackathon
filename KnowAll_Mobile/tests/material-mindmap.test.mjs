import test from 'node:test'
import assert from 'node:assert/strict'
import { mindmapBranches, filterMindmap } from '../src/materialMindmap.ts'
const chunks = [{ id: 'c1', text: '# 细胞\n细胞结构' }, { id: 'c2', text: '第二章 遗传\nDNA 保存信息' }]
test('raw source structure works before generation', () => {
  const result = mindmapBranches([], chunks)
  assert.equal(result[0].title, '细胞')
  assert.equal(result[1].title, '第二章 遗传')
  assert.deepEqual(result[1].source, ['c2'])
})
test('searching an answer retains its parent chapter without unrelated cards', () => {
  const nodes = mindmapBranches([{ id: 's', title: '遗传', notes: '', chunk_ids: ['c2'], flashcards: [{ id: 'a', front: '载体？', back: 'DNA' }, { id: 'b', front: '其他', back: '蛋白质' }] }], chunks)
  const found = filterMindmap(nodes, ' dna ')
  assert.equal(found.length, 1)
  assert.equal(found[0].title, '遗传')
  assert.deepEqual(found[0].cards.map(card => card.id), ['a'])
  assert.deepEqual(filterMindmap(nodes, '不存在'), [])
})
test('missing sources are not replaced with unrelated chunks; duplicate section IDs stay distinct', () => {
  const result = mindmapBranches([{ id: 'same', title: 'a', notes: '', chunk_ids: ['deleted'], flashcards: [] }, { id: 'same', title: 'b', notes: '', chunk_ids: ['c1'], flashcards: [] }], chunks)
  assert.deepEqual(result[0].source, [])
  assert.notEqual(result[0].key, result[1].key)
})
