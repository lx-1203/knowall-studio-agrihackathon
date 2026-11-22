import api from './api'

export interface KnowledgeItem {
  id: string
  name: string
  topic: string
  aliases: string[]
  definition: string
  formulas: string[]
  conditions: string[]
  key_points: string[]
  pitfalls: string[]
  prerequisites: string[]
  related: string[]
  supplements: string[]
}
export interface CollectionSummary {
  id: string
  title: string
  subject: string
  kind: 'builtin' | 'personal'
  point_count: number
  review_status: 'compiled' | 'needs_review' | 'reviewed'
}
export interface KnowledgeCollection extends CollectionSummary { points: KnowledgeItem[] }
export interface CollectionPreview { batch_count: number; characters: number; chunk_count: number }

export const listKnowledgeCollections = (signal?: AbortSignal): Promise<CollectionSummary[]> =>
  api.get('/knowledge-collections', { signal }).then(r => r.data)
export const getKnowledgeCollection = (id: string, signal?: AbortSignal): Promise<KnowledgeCollection> =>
  api.get(`/knowledge-collections/${encodeURIComponent(id)}`, { signal }).then(r => r.data)
export const previewKnowledgeCollection = (document_ids: string[], signal?: AbortSignal): Promise<CollectionPreview> =>
  api.post('/knowledge-collections/preview', { document_ids }, { signal }).then(r => r.data)
export const generateKnowledgeCollection = (params: { document_ids: string[]; subject: string; title: string; model: string }): Promise<KnowledgeCollection> =>
  api.post('/knowledge-collections/generate', params, { timeout: 3600000 }).then(r => r.data)
export const updateKnowledgeCollection = (collection: KnowledgeCollection, reviewed = false): Promise<KnowledgeCollection> =>
  api.put(`/knowledge-collections/${encodeURIComponent(collection.id)}`, {
    title: collection.title, subject: collection.subject, points: collection.points, reviewed,
  }).then(r => r.data)

export function collectionMarkdown(collection: KnowledgeCollection): string {
  const lines = [`# ${collection.title}`, '', `学科：${collection.subject}`, '',
    `内容状态：${collection.review_status === 'needs_review' ? '待校对' : collection.review_status === 'reviewed' ? '已校对' : '基础整理'}`, '']
  for (const point of collection.points) {
    lines.push(`## ${point.name}`, '', `知识主题：${point.topic}`, '', point.definition, '')
    const sections: [string, string[]][] = [
      ['别名', point.aliases], ['公式', point.formulas], ['适用条件', point.conditions],
      ['重点', point.key_points], ['易错点', point.pitfalls], ['前置知识', point.prerequisites],
      ['关联知识', point.related], ['待核对的补充说明', point.supplements],
    ]
    for (const [label, values] of sections) {
      if (values?.length) lines.push(`### ${label}`, '', ...values.map(value => `- ${value}`), '')
    }
  }
  return lines.join('\n')
}
