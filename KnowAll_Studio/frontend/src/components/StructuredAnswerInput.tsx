import { useEffect, useState } from 'react'
import { Button, Checkbox, Input, Radio, Select, Space } from 'antd'
import { readBlankAnswers, writeBlankAnswers } from '../utils/blankAnswers'

export type LearningOption = { label: string; text: string; choices?: string[] }
export default function StructuredAnswerInput({ question, value, onChange, disabled = false }: {
  question: { question_type: string; options?: LearningOption[]; blank_count?: number }; value: string; onChange: (v: string) => void; disabled?: boolean
}) {
  const options = question.options || []
  const [order, setOrder] = useState<string[]>([])
  useEffect(() => {
    const initial = options.map(o => o.label)
    setOrder(initial)
    if (question.question_type === 'ordering') onChange(JSON.stringify(initial))
  }, [question])
  if (question.question_type === 'fill_blank') {
    const count = question.blank_count
    if (typeof count === 'number' && Number.isInteger(count) && count >= 1 && count <= 100) {
      const answers = readBlankAnswers(value, count)
      const update = (index: number, text: string) => {
        const next = Array.from({ length: count }, (_, i) => answers[i] || '')
        next[index] = text
        onChange(writeBlankAnswers(next))
      }
      return <Space direction="vertical" style={{ width: '100%' }}>{Array.from({ length: count }, (_, i) =>
        <label key={i}>第{i + 1}空<Input aria-label={`第${i + 1}空`} disabled={disabled} value={answers[i] || ''} onChange={e => update(i, e.target.value)} /></label>
      )}</Space>
    }
  }
  if (['fill_blank', 'short_answer', 'term_definition'].includes(question.question_type)) {
    return <Input.TextArea aria-label="你的答案" disabled={disabled} value={value} onChange={e => onChange(e.target.value)} rows={3} />
  }
  if (['matching', 'classification'].includes(question.question_type)) {
    let mapping: Record<string, string> = {}
    try { mapping = JSON.parse(value || '{}') } catch { /* input begins empty */ }
    return <Space direction="vertical" style={{ width: '100%' }}>{options.map(o => <label key={o.label} style={{ display: 'grid', gap: 8 }}>
      {o.label}. {o.text}<Select aria-label={`${o.label} 的答案`} disabled={disabled} value={mapping[o.label]} placeholder="请选择" options={(o.choices || []).map(c => ({ value: c, label: c }))} onChange={v => onChange(JSON.stringify({ ...mapping, [o.label]: v }))} />
    </label>)}</Space>
  }
  if (question.question_type === 'ordering') {
    const move = (index: number, delta: number) => { const next = [...order]; [next[index], next[index + delta]] = [next[index + delta], next[index]]; setOrder(next); onChange(JSON.stringify(next)) }
    return <Space direction="vertical" style={{ width: '100%' }}>{order.map((label, i) => <div key={label} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
      <span>{i + 1}. {options.find(o => o.label === label)?.text}</span>
      <Button aria-label={`${label} 上移`} disabled={disabled || i === 0} onClick={() => move(i, -1)}>上移</Button>
      <Button aria-label={`${label} 下移`} disabled={disabled || i === order.length - 1} onClick={() => move(i, 1)}>下移</Button>
    </div>)}</Space>
  }
  if (['multi_choice', 'multiple_choice'].includes(question.question_type)) return <Checkbox.Group disabled={disabled} value={value ? value.split(',') : []} onChange={v => onChange(v.join(','))}><Space direction="vertical">{options.map(o => <Checkbox key={o.label} value={o.label}>{o.label}. {o.text}</Checkbox>)}</Space></Checkbox.Group>
  // Judgment submissions use backend boolean tokens even if generated options are A/B.
  const choices = question.question_type === 'true_false' ? [{ label: 'true', text: '正确' }, { label: 'false', text: '错误' }] : options
  if (choices.length) return <Radio.Group disabled={disabled} value={value} onChange={e => onChange(e.target.value)}><Space direction="vertical">{choices.map(o => <Radio key={o.label} value={o.label}>{o.label}. {o.text}</Radio>)}</Space></Radio.Group>
  return <Input.TextArea aria-label="你的答案" disabled={disabled} value={value} onChange={e => onChange(e.target.value)} rows={3} />
}
