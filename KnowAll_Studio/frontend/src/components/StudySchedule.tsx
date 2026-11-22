type Row = Record<string, unknown>
type SavedPlan = {
  daily_hours?: unknown
  short_term_plan?: unknown
  long_term_plan?: unknown
  ebbinghaus_nodes?: unknown
}

const rows = (value: unknown): Row[] => Array.isArray(value)
  ? value.filter((item): item is Row => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : []
const text = (value: unknown) => typeof value === 'string' || (typeof value === 'number' && Number.isFinite(value)) ? String(value) : ''
const topics = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []

/** Render persisted calendar strings as text; never regenerate or timezone-shift them. */
export default function StudySchedule({ plan }: { plan: SavedPlan }) {
  const short = rows(plan.short_term_plan)
  const long = rows(plan.long_term_plan)
  const review = rows(plan.ebbinghaus_nodes)
  if (!short.length && !long.length && !review.length) return <p>此计划没有保存的排期</p>

  return <section aria-label="已保存学习排期" style={{ marginTop: 16 }}>
    <p>每日学习：{text(plan.daily_hours) || '未记录'} 小时</p>
    {!!short.length && <details>
      <summary style={{ cursor: 'pointer', padding: '8px 0' }}>短期排期</summary>
      <div role="region" aria-label="短期排期">
        {short.map((day, index) => <div key={index}>
          <p><strong>第{text(day.day)}天 · {text(day.date)}</strong></p>
          <ul>{rows(day.hours).map((slot, slotIndex) => <li key={slotIndex}>
            {text(slot.hour)} · {text(slot.topic)}{slot.is_review === true ? '（复习）' : ''}
          </li>)}</ul>
        </div>)}
      </div>
    </details>}
    {!!long.length && <details>
      <summary style={{ cursor: 'pointer', padding: '8px 0' }}>长期排期</summary>
      <div role="region" aria-label="长期排期">
        {long.map((week, index) => <div key={index}>
          <p><strong>第{text(week.week)}周</strong></p>
          <ul>{rows(week.days).map((day, dayIndex) => <li key={dayIndex}>
            {text(day.date)} · {text(day.hours) || '未记录'} 小时{day.is_review_day === true ? '（复习日）' : ''}
            <p>{topics(day.topics).join('、') || '未安排新知识点'}</p>
          </li>)}</ul>
        </div>)}
      </div>
    </details>}
    {!!review.length && <details>
      <summary style={{ cursor: 'pointer', padding: '8px 0' }}>复习节点</summary>
      <div role="region" aria-label="复习节点">
        <p>以下为保存的建议复习节点。</p>
        <ul>{review.map((node, index) => <li key={index}>第{text(node.day)}天 · {text(node.description)}</li>)}</ul>
      </div>
    </details>}
  </section>
}
