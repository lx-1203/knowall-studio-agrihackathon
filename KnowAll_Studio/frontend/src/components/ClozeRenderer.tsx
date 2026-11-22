import { useState, useCallback } from 'react'
import { Alert, Input, Tag, theme } from 'antd'
import { CheckOutlined, CloseOutlined } from '@ant-design/icons'
import LearningContent from './LearningContent'
import FlashcardFront from './FlashcardFront'

interface ClozeRendererProps {
  front: string       // Text with {{c1::answer}} markers
  back: string        // Full explanation
  hints?: string
  onReveal?: () => void
}

/**
 * Parse cloze text and separate {c{N}::answer} markers from surrounding text.
 */
function parseCloze(text: string): Array<{ type: 'text' | 'blank'; content: string; blankIndex?: number; hint?: string }> {
  const parts: Array<{ type: 'text' | 'blank'; content: string; blankIndex?: number; hint?: string }> = []
  const regex = /\{\{c(\d+)::([^}]+)\}\}/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(text)) !== null) {
    // Text before this marker
    if (match.index > lastIndex) {
      parts.push({ type: 'text', content: text.slice(lastIndex, match.index) })
    }
    parts.push({
      type: 'blank',
      content: match[2].split('::', 1)[0],
      hint: match[2].includes('::') ? match[2].slice(match[2].indexOf('::') + 2) : undefined,
      blankIndex: parseInt(match[1]),
    })
    lastIndex = match.index + match[0].length
  }

  // Remaining text
  if (lastIndex < text.length) {
    parts.push({ type: 'text', content: text.slice(lastIndex) })
  }

  return parts
}

export default function ClozeRenderer({ front, back, hints, onReveal }: ClozeRendererProps) {
  const { token } = theme.useToken()
  const [userAnswers, setUserAnswers] = useState<Record<number, string>>({})
  const [revealed, setRevealed] = useState<Record<number, boolean>>({})
  const [allRevealed, setAllRevealed] = useState(false)

  const parts = parseCloze(front)
  const blankCount = parts.filter(p => p.type === 'blank').length
  // Render one complete formula: slicing math around inputs breaks its delimiters.
  // Unrevealed answers never enter Markdown/KaTeX HTML or accessibility MathML.
  const question = parts.map(part => part.type === 'text' ? part.content
    : revealed[part.blankIndex!] ? part.content : `[${part.blankIndex}]`).join('')

  const handleRevealAll = useCallback(() => {
    setAllRevealed(true)
    const all: Record<number, boolean> = {}
    parts.filter(p => p.type === 'blank').forEach(p => {
      if (p.blankIndex !== undefined) all[p.blankIndex] = true
    })
    setRevealed(all)
    onReveal?.()
  }, [parts, onReveal])

  const checkAnswer = (blankIndex: number, userAnswer: string) => {
    const blank = parts.find(p => p.type === 'blank' && p.blankIndex === blankIndex)
    if (!blank) return false
    return userAnswer.trim().toLowerCase() === blank.content.trim().toLowerCase()
  }

  // Unsupported markers must never reach Markdown/MathML with their answer.
  if (parts.some(part => part.type === 'text' && /\{\{c\d+::/i.test(part.content))) {
    return <Alert type="warning" showIcon message="此填空格式暂不能预览"
      description="请编辑卡片检查填空标记。带花括号的填空答案暂不支持，可以调整格式或跳过此卡。" />
  }

  return (
    <div style={{ width: '100%' }}>
      <div style={{ color: '#999', fontSize: 12, marginBottom: 8 }}>
        填空卡片 ({blankCount} 个空)
      </div>

      <div style={{ fontSize: 17, lineHeight: 2.2, marginBottom: 16 }}>
        <FlashcardFront text={question} />
      </div>
      <div style={{ fontSize: 17, lineHeight: 2.2, marginBottom: 16, display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        {parts.map((part, i) => {
          if (part.type === 'text') {
            return null
          }
          const idx = part.blankIndex!
          const isRevealed = revealed[idx]
          const userAnswer = userAnswers[idx] || ''
          const isCorrect = isRevealed ? checkAnswer(idx, userAnswer) : null

          return (
            <span key={i} style={{ display: 'inline-block', verticalAlign: 'middle' }}>
              <span style={{ marginRight: 4 }}>[{idx}]</span>
              {isRevealed ? (
                <Tag color={isCorrect ? 'success' : 'error'} style={{ margin: '0 2px', fontSize: 15 }}>
                  <FlashcardFront text={part.content} />
                  {isCorrect ? <CheckOutlined style={{ marginLeft: 4 }} /> : <CloseOutlined style={{ marginLeft: 4 }} />}
                </Tag>
              ) : (
                <Input
                  size="small"
                  value={userAnswer}
                  onChange={e => {
                    setUserAnswers(prev => ({ ...prev, [idx]: e.target.value }))
                  }}
                  onPressEnter={() => {
                    if (userAnswers[idx]?.trim()) {
                      setRevealed(prev => ({ ...prev, [idx]: true }))
                    }
                  }}
                  placeholder="..."
                  aria-label={`填空 ${idx}`}
                  style={{
                    width: 160,
                    maxWidth: '100%',
                    margin: '0 2px',
                    borderBottom: '2px dashed #4f46e5',
                    borderTop: 'none',
                    borderLeft: 'none',
                    borderRight: 'none',
                    borderRadius: 0,
                    textAlign: 'center',
                    fontSize: 15,
                  }}
                />
              )}
              {!isRevealed && part.hint && <span style={{ fontSize: 13, marginLeft: 4 }}>提示：<FlashcardFront text={part.hint} /></span>}
            </span>
          )
        })}
      </div>

      {allRevealed && (
        <div style={{
          background: token.colorBgLayout, borderRadius: 8, padding: 16, marginTop: 12,
          border: `1px solid ${token.colorBorder}`,
        }}>
          <div style={{ color: '#666', fontSize: 13, marginBottom: 4 }}>完整解释:</div>
          <div className="flashcard-answer" style={{ fontSize: 15, lineHeight: 1.8 }}><LearningContent text={back} /></div>
          {hints && (
            <div style={{ marginTop: 8, color: '#999', fontSize: 13 }}>
              提示: <FlashcardFront text={hints} />
            </div>
          )}
        </div>
      )}

      {!allRevealed && (
        <div style={{ textAlign: 'center', marginTop: 12 }}>
          <button
            onClick={handleRevealAll}
            style={{
              padding: '8px 24px', borderRadius: 8, border: '1px solid #4f46e5',
              background: '#4f46e5', color: '#fff', cursor: 'pointer', fontSize: 14,
            }}
          >
            显示全部答案
          </button>
        </div>
      )}
    </div>
  )
}
