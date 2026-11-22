export const padding = Array.from({ length: 15 }, (_, i) => `正文原文 ${i}：保留来源和内容，不重新生成。`).join('\n\n')
export const summaryMarkdown = [
  '# 🔴【核心】 中文 **重点** `code`', padding,
  'Setext 标题\n============', padding,
  '## 重复标题 😀', padding,
  '```md\n# 代码中的伪标题\n```',
  '## 重复标题 😀', padding,
  '### [三级链接](https://example.test) *强调*', padding,
  '#### 四级', padding, '##### 五级', padding, '###### 六级', padding,
].join('\n\n')
export const headingLabels = ['🔴【核心】 中文 重点 code', 'Setext 标题', '重复标题 😀', '重复标题 😀', '三级链接 强调', '四级', '五级', '六级']
export function summaryData(id = 'nav', content_md = summaryMarkdown) {
  return { id, content_md, document_ids: ['source-doc'], node_count: 8, level_stats: { core: 3 } }
}
