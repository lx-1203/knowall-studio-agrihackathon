export const generationTypes = [['single_choice', '单选'], ['multi_choice', '多选'], ['true_false', '判断'], ['fill_blank', '填空'], ['short_answer', '简答'], ['term_definition', '名词解释'], ['matching', '匹配'], ['ordering', '排序'], ['classification', '分类']] as const
export interface GenerationDraft { questions: string; cards: string; model: string; types: string[] }
export interface GenerationSettings { question_count: number; card_count: number; model: string; question_types: string[] }
export interface GenerationPreview { source_hash: string; sections: { id: string; title: string; question_budget: number; card_budget: number }[] }
const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const nonempty = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
const count = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 100

export function generationSettings(draft: GenerationDraft): { value: GenerationSettings | null; error: string } {
  if (![draft.questions, draft.cards].every(value => /^\d{1,3}$/.test(value.trim()) && Number(value) <= 100)) return { value: null, error: '习题和知识卡数量需为0至100的整数。' }
  const question_count = Number(draft.questions), card_count = Number(draft.cards)
  if (question_count + card_count === 0) return { value: null, error: '请至少生成一道习题或一张知识卡。' }
  if (!draft.model.trim() || draft.model.trim().length > 100) return { value: null, error: '请填写已配置的模型名称，最多100字。' }
  if (!draft.types.length || draft.types.some(type => !generationTypes.some(([value]) => value === type))) return { value: null, error: '请至少选择一种支持的题型。' }
  return { value: { question_count, card_count, model: draft.model.trim(), question_types: generationTypes.map(([value]) => value).filter(type => draft.types.includes(type)) }, error: '' }
}
export const previewRequest = (documentId: string, settings: GenerationSettings) => ({ path: `/learning/documents/${encodeURIComponent(documentId)}/preview`, params: { question_count: settings.question_count, card_count: settings.card_count } })
export const generationRequest = (documentId: string, settings: GenerationSettings) => ({ path: '/learning/generate', body: { document_id: documentId, ...settings, concurrency: 2, max_section_chars: 6000 } })
export function normalizeGenerationPreview(value: unknown, settings: GenerationSettings): GenerationPreview {
  const data = record(value)
  if (!nonempty(data.source_hash) || !Array.isArray(data.sections) || !data.sections.length || data.sections.length > 200) throw new Error('Invalid preview')
  const ids = new Set<string>()
  const sections = data.sections.map(value => {
    const section = record(value)
    if (!nonempty(section.id) || ids.has(section.id) || !nonempty(section.title) || !count(section.question_budget) || !count(section.card_budget)) throw new Error('Invalid budget')
    ids.add(section.id)
    return { id: section.id, title: section.title, question_budget: section.question_budget, card_budget: section.card_budget }
  })
  if (sections.reduce((sum, section) => sum + section.question_budget, 0) !== settings.question_count || sections.reduce((sum, section) => sum + section.card_budget, 0) !== settings.card_count) throw new Error('Budget does not match configuration')
  return { source_hash: data.source_hash, sections }
}
export function generationReceipt(value: unknown, documentId: string, expected?: GenerationSettings): { status: 'complete' | 'partial' | 'failed'; message: string; id: string } {
  const data = record(value)
  if (!nonempty(data.id) || data.document_id !== documentId || data.stale !== false || !Array.isArray(data.sections) || !data.sections.length) throw new Error('Invalid generation receipt')
  if (expected) {
    const config = record(data.config)
    const types = Array.isArray(config.question_types) ? config.question_types : []
    if (config.question_count !== expected.question_count || config.card_count !== expected.card_count || config.model !== expected.model ||
      types.length !== expected.question_types.length || !expected.question_types.every(type => types.includes(type)) ||
      config.concurrency !== 2 || config.max_section_chars !== 6000) throw new Error('Generation configuration does not match submission')
  }
  const sections = data.sections.map(record)
  const usable = (section: Record<string, unknown>) => nonempty(section.notes) ||
    (Array.isArray(section.flashcards) && section.flashcards.some(value => { const card = record(value); return nonempty(card.id) && nonempty(card.front) && nonempty(card.back) })) ||
    (Array.isArray(section.questions) && section.questions.some(value => { const q = record(value); return nonempty(q.id) && nonempty(q.question_text) && record(q.quality).eligible === true }))
  if (!sections.some(usable)) return { id: data.id, status: 'failed', message: '各章节均未生成成功，请检查模型设置后重新整理。原资料仍可阅读。' }
  const failed = sections.filter(section => !usable(section) || (Array.isArray(section.errors) && section.errors.length > 0)).length
  const blocked = record(data.quality).blocked
  const blockedCount = typeof blocked === 'number' && Number.isInteger(blocked) && blocked > 0 ? blocked : 0
  if (failed || blockedCount) return { id: data.id, status: 'partial', message: `${failed} 个章节存在缺漏，已保留可用内容。${blockedCount ? `${blockedCount} 道题需修复，已从练习中隔离。` : ''}` }
  return { id: data.id, status: 'complete', message: data.from_cache === true ? '已复用这份资料的学习内容。' : '学习内容已整理，可开始练习或查看知识卡。' }
}
