export interface MaterialCard { id: string; front: string; back: string }
export interface MaterialSection { id: string; title: string; notes: string; chunk_ids: string[]; flashcards: MaterialCard[] }
export interface MaterialPack { id: string; stale: boolean; sections: MaterialSection[] }
export interface MaterialQuestion {
  id: string; question_text: string; question_type: string; options?: MaterialOption[]; blank_count?: number
}
export interface MaterialOption { label: string; text: string; choices?: string[] }
export interface MaterialPracticeItem {
  kind: 'question' | 'flashcard' | 'card'; title?: string; notes?: string; question?: MaterialQuestion; card?: MaterialCard
  source?: { chunk_ids?: string[]; page_ranges?: string[] }
}
export interface MaterialAnswerResult {
  is_correct: boolean; correct_answer: unknown; analysis?: string; score_ratio?: number
  source?: { chunk_ids?: string[]; page_ranges?: string[] }
}

const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const string = (value: unknown) => typeof value === 'string' ? value : ''
const strings = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []

export function normalizeLearningPack(value: unknown): MaterialPack | null {
  const pack = record(value)
  if (!string(pack.id)) return null
  const sections = Array.isArray(pack.sections) ? pack.sections : []
  return { id: string(pack.id), stale: pack.stale === true, sections: sections.map(value => {
    const section = record(value)
    const cards = Array.isArray(section.flashcards) ? section.flashcards : []
    return { id: string(section.id), title: string(section.title), notes: string(section.notes), chunk_ids: strings(section.chunk_ids),
      flashcards: cards.map(value => { const card = record(value); return { id: string(card.id), front: string(card.front), back: string(card.back) } }).filter(card => card.id && card.front) }
  }) }
}
export const learningPackPath = (documentId: string) => `/learning/documents/${encodeURIComponent(documentId)}/pack`
export function learningSessionRequest(pack: MaterialPack, mode: 'practice' | 'smart' = 'practice') {
  if (pack.stale) throw new Error('资料已更新，请重新整理后练习。')
  return { path: `/learning/packs/${encodeURIComponent(pack.id)}/session`, body: { mode, limit: 20 } }
}
export function learningCardReviewRequest(pack: MaterialPack, cardId: string, rating: number) {
  if (pack.stale) throw new Error('资料已更新，请重新整理后复习。')
  if (!Number.isInteger(rating) || rating < 1 || rating > 4) throw new Error('评分必须为1至4')
  if (!cardId) throw new Error('未找到这张知识卡')
  return { path: `/learning/packs/${encodeURIComponent(pack.id)}/review`, body: { card_id: cardId, rating } }
}
export function learningAnswerRequest(packId: string, questionId: string, answer: string, elapsed: number) {
  return { path: `/learning/packs/${encodeURIComponent(packId)}/answers`, body: { question_id: questionId, answer, time_spent_ms: Math.min(86400000, Math.max(0, Math.round(elapsed))) } }
}
export const materialContext = (chunk?: { text: string }) => chunk?.text.slice(0, 20000) || ''
export function findSourceIndex(chunks: { id: string }[], ids: string[]) {
  return chunks.findIndex(chunk => ids.includes(chunk.id))
}
