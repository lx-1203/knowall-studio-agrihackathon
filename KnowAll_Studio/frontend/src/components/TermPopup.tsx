// KnowAll_Studio/frontend/src/components/TermPopup.tsx
import { Space, Tag, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useGlossary } from './GlossaryContext'
import type { GlossaryItem } from '../types'

const { Text } = Typography

/** 来源分级标签：type 决定颜色与文案，label（完整可展示文本）直接展示，过长时以 title tooltip 兜底。 */
export function SourceBadge({ type, label }: { type?: string; label?: string }) {
  if (!type) return null
  const map: Record<string, { color: string; text: string }> = {
    authoritative: { color: 'green', text: '权威来源' },
    web: { color: 'blue', text: '网络出处' },
    ai: { color: 'orange', text: 'AI 生成' },
    document: { color: 'default', text: '资料库' },
  }
  const m = map[type] || map.document
  return <Tag color={m.color} title={label || ''}>{label || m.text}</Tag>
}

/** 术语卡片内容（复用于浮窗/Modal）。 */
export function TermCardContent({ item, onDetail }: { item: GlossaryItem; onDetail: (item: GlossaryItem) => void }) {
  return (
    <div style={{ maxWidth: 340 }}>
      <Text style={{ fontSize: 13, display: 'block', marginBottom: 6 }}>
        {(item.definition || '').slice(0, 120)}{(item.definition || '').length > 120 ? '…' : ''}
      </Text>
      {item.chapter && <Tag style={{ marginBottom: 6, fontSize: 11 }}>{item.chapter}</Tag>}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Space size={6}>
          <Text style={{ fontSize: 12, color: '#888' }}>{item.subject}</Text>
          <SourceBadge type={item.source_type} label={item.source_label} />
        </Space>
        <a onClick={() => onDetail(item)}>查看详情 →</a>
      </div>
    </div>
  )
}

/** 点击术语时获取浮窗卡片内容；多学科同名（缩写歧义）时列出全部候选。 */
export function useTermPopover() {
  const { terms } = useGlossary()
  const navigate = useNavigate()

  /** 取所有同名学科的变体（命中 term 或 aliases）。 */
  const getVariants = (name: string): GlossaryItem[] => {
    const hits = terms.filter(t => t.term === name || (t.aliases || []).includes(name))
    return hits
  }

  /** 浮窗内容：单条或（多学科歧义时）全部候选。 */
  const getTermCard = (name: string): { title: ReactNode; content: ReactNode } | null => {
    const variants = getVariants(name)
    if (!variants.length) return null
    return {
      title: <span style={{ fontWeight: 700 }}>{variants[0].term}</span>,
      content: (
        <div style={{ maxWidth: 340 }}>
          {variants.length > 1 && (
            <Text style={{ fontSize: 12, color: '#888', display: 'block', marginBottom: 6 }}>
              同名术语 · {variants.length} 个学科解释：
            </Text>
          )}
          {variants.map((item, idx) => (
            <div key={idx} style={{ marginBottom: variants.length > 1 ? 8 : 0 }}>
              {variants.length > 1 && <Tag style={{ marginBottom: 4, fontSize: 11 }}>{item.subject}</Tag>}
              <TermCardContent
                item={item}
                onDetail={(it) => navigate(`/glossary/${encodeURIComponent(it.term)}${it.subject ? `?subject=${encodeURIComponent(it.subject)}` : ''}`)}
              />
            </div>
          ))}
        </div>
      ),
    }
  }

  return { getTermCard, getVariants }
}
