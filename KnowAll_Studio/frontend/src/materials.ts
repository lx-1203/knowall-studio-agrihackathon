export type MaterialKind = 'knowledge' | 'question_bank' | 'mixed'
export interface Material {
  id: string
  filename: string
  file_type: string
  status: string
  subject?: string
  material_kind?: MaterialKind
  page_count?: number
  created_at?: string
}
export interface SourceChunk { id: string; index: number; text: string; page_range?: string }
export const materialKinds = [
  { value: 'knowledge', label: '知识资料' },
  { value: 'question_bank', label: '题库' },
  { value: 'mixed', label: '混合资料' },
] as const
export const kindLabel = (kind?: MaterialKind) => materialKinds.find(item => item.value === kind)?.label || '知识资料'
export const materialPath = (id: string) => `/materials/${encodeURIComponent(id)}`
export function chapterTitle(chunk: SourceChunk, index: number) {
  return chunk.text.match(/^#{1,6}\s+(.+)$/m)?.[1] || `阅读片段 ${index + 1}`
}

// This resource keeps its existing login/consent and progress implementation.
// It is not a generated study pack or an uploaded document.
export const builtinBiology: Material = {
  id: 'builtin-molbio', filename: '分子生物学 · 章节习题', file_type: '内置题库',
  status: 'ready', subject: '生物', material_kind: 'question_bank',
}
