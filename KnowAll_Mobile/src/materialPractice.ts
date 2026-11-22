import type { MaterialQuestion } from './materialLearning'

const parse = (value: string): unknown => { try { return JSON.parse(value) } catch { return null } }
const isMapping = (value: unknown): value is Record<string, string> => !!value && typeof value === 'object' && !Array.isArray(value)
export const readMapping = (value: string): Record<string, string> => {
  const parsed = parse(value)
  return isMapping(parsed) ? parsed : {}
}
const isMultiple = (q: MaterialQuestion) => ['multi_choice', 'multiple_choice'].includes(q.question_type)
const isMapped = (q: MaterialQuestion) => ['matching', 'classification'].includes(q.question_type)

export function inputIssue(q: MaterialQuestion): string {
  if (q.question_type === 'fill_blank' && q.blank_count !== undefined &&
    (!Number.isInteger(q.blank_count) || q.blank_count < 1 || q.blank_count > 100)) return '填空数量有误，请修复题目后重试。'
  if (!isMapped(q) && q.question_type !== 'ordering' && q.question_type !== 'single_choice' && !isMultiple(q)) return ''
  const options = q.options
  if (!Array.isArray(options) || options.length < 2 || options.length > 100) return '题目选项不完整，请刷新或修复题目。'
  const labels = new Set<string>()
  for (const option of options) {
    if (!option || typeof option.label !== 'string' || !option.label.trim() || option.label !== option.label.trim().toUpperCase() ||
      typeof option.text !== 'string' || !option.text.trim() || labels.has(option.label)) return '题目选项有误，请刷新或修复题目。'
    labels.add(option.label)
    if (isMapped(q) && (!Array.isArray(option.choices) || option.choices.length < 2 || option.choices.length > 100 ||
      option.choices.some(choice => typeof choice !== 'string' || !choice.trim()) || new Set(option.choices).size !== option.choices.length)) return '匹配或分类候选缺失，请刷新或修复题目。'
  }
  return ''
}

export function initialAnswer(q: MaterialQuestion): string {
  return q.question_type === 'ordering' && !inputIssue(q) ? JSON.stringify(q.options!.map(option => option.label)) : ''
}
export function readOrder(q: MaterialQuestion, value: string): string[] {
  const parsed = parse(value)
  const labels = (q.options || []).map(option => option.label)
  return Array.isArray(parsed) && parsed.length === labels.length && new Set(parsed).size === labels.length && parsed.every(label => labels.includes(label)) ? parsed : labels
}
export function moveOrder(q: MaterialQuestion, value: string, index: number, delta: number): string {
  if (inputIssue(q)) return value
  const order = readOrder(q, value)
  const target = index + delta
  if (!Number.isInteger(index) || ![-1, 1].includes(delta) || index < 0 || index >= order.length || target < 0 || target >= order.length) return value
  const next = [...order]
  ;[next[index], next[target]] = [next[target], next[index]]
  return JSON.stringify(next)
}
export function selectMapping(q: MaterialQuestion, value: string, label: string, choice: string): string {
  if (inputIssue(q) || !q.options?.find(option => option.label === label)?.choices?.includes(choice)) return value
  return JSON.stringify({ ...readMapping(value), [label]: choice })
}
export function toggleMultiple(q: MaterialQuestion, value: string, label: string): string {
  if (inputIssue(q) || !q.options?.some(option => option.label === label)) return value
  const chosen = new Set(value ? value.split(',') : [])
  chosen.has(label) ? chosen.delete(label) : chosen.add(label)
  return q.options.filter(option => chosen.has(option.label)).map(option => option.label).join(',')
}
export function readBlanks(value: string, count: number): string[] {
  const parsed = parse(value)
  return Array.from({ length: Math.min(100, Math.max(0, count)) }, (_, index) => Array.isArray(parsed) && typeof parsed[index] === 'string' ? parsed[index] : '')
}
export function setBlank(value: string, count: number, index: number, text: string): string {
  if (!Number.isInteger(count) || count < 1 || count > 100 || !Number.isInteger(index) || index < 0 || index >= count) return value
  const answers = readBlanks(value, count)
  answers[index] = text
  return JSON.stringify(answers)
}
export function answerReady(q: MaterialQuestion, value: string): boolean {
  if (!value.trim() || value.length > 20000 || inputIssue(q)) return false
  const options = q.options || []
  const labels = options.map(option => option.label)
  if (isMapped(q)) {
    const mapped = readMapping(value)
    return Object.keys(mapped).length === labels.length && options.every(option => option.choices!.includes(mapped[option.label]))
  }
  if (q.question_type === 'ordering') {
    const order = parse(value)
    return Array.isArray(order) && order.length === labels.length && new Set(order).size === labels.length && order.every(label => labels.includes(label))
  }
  if (isMultiple(q)) { const picks = value.split(','); return new Set(picks).size === picks.length && picks.every(label => labels.includes(label)) }
  if (q.question_type === 'single_choice') return labels.includes(value)
  if (q.question_type === 'true_false') return value === 'true' || value === 'false'
  if (q.question_type === 'fill_blank' && q.blank_count) return readBlanks(value, q.blank_count).every(text => !!text.trim())
  return true
}
export function formatCorrectAnswer(q: MaterialQuestion, value: unknown): string {
  const decoded = typeof value === 'string' ? parse(value) ?? value : value
  const optionText = (label: string) => { const option = q.options?.find(option => option.label === label); return option ? `${label}. ${option.text}` : label }
  if (isMapped(q) && isMapping(decoded)) return (q.options || []).map(option => `${optionText(option.label)} → ${decoded[option.label] || '未提供'}`).join('\n')
  if (q.question_type === 'ordering' && Array.isArray(decoded)) return decoded.map(label => optionText(String(label))).join(' → ')
  if (q.question_type === 'fill_blank' && Array.isArray(decoded)) return decoded.map((text, index) => `第${index + 1}空：${String(text)}`).join('\n')
  if (q.question_type === 'true_false') return decoded === true || decoded === 'true' ? '正确' : decoded === false || decoded === 'false' ? '错误' : String(decoded)
  return typeof decoded === 'string' ? decoded : JSON.stringify(decoded) || '未提供'
}
