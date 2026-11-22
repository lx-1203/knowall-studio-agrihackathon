import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chapterLabel, materialLabel } from '../src/materialReaderNavigation.ts'

test('reading labels retain meaningful titles while removing only supported final extensions', () => {
  assert.equal(materialLabel('细胞生物学 · 课程讲义.md'), '细胞生物学 · 课程讲义')
  assert.equal(materialLabel('lecture.v2.PDF'), 'lecture.v2')
  assert.equal(materialLabel('chapter.unknown'), 'chapter.unknown')
  assert.equal(materialLabel(''), '未命名资料')
})
test('directory uses a heading even after front matter or a leading introduction', () => {
  assert.equal(chapterLabel('前言\n\n## 第二章 遗传\nDNA 保存信息', 1), '第二章 遗传')
  assert.equal(chapterLabel('---\ntitle: metadata\n---\n# 细胞', 0), '细胞')
})
test('code examples and long prose are not mistaken for chapter headings', () => {
  assert.equal(chapterLabel('```python\n# not a chapter\n```\n## 真实章节', 0), '真实章节')
  assert.equal(chapterLabel('细胞是生命的基本单位。', 2), '第 3 段')
  assert.equal(chapterLabel('', 0), '第 1 段')
})
test('plain textbook chapter lines work and markdown inline syntax is not exposed in the title', () => {
  assert.equal(chapterLabel('第二章 遗传\nDNA 保存信息', 1), '第二章 遗传')
  assert.equal(chapterLabel('## **细胞** 与 `DNA`', 0), '细胞 与 DNA')
})
