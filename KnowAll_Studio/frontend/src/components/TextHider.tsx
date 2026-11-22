import { useMemo } from 'react'
import { Tooltip } from 'antd'
import type { HideSegment } from '../types'

interface TextHiderProps {
  segments: HideSegment[]
  revealedIndices: Set<number>
  onToggleReveal: (idx: number) => void
  onRevealAll: () => void
  allRevealed: boolean
}

/**
 * 遮字背诵组件
 * 将文本分成若干段，hidden 段默认显示为灰色下划线占位，
 * 点击可揭示答案。全部揭示后显示完整原文。
 */
export default function TextHider({ segments, revealedIndices, onToggleReveal, onRevealAll, allRevealed }: TextHiderProps) {
  const hiddenCount = useMemo(() => segments.filter(s => s.hidden).length, [segments])
  const revealedCount = useMemo(() => {
    if (allRevealed) return hiddenCount
    return [...revealedIndices].filter(i => segments[i]?.hidden).length
  }, [revealedIndices, segments, allRevealed, hiddenCount])

  return (
    <div>
      {/* 顶部工具栏 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <span style={{ color: '#666', fontSize: 14 }}>
          已揭示 {revealedCount} / {hiddenCount} 处
        </span>
        <a onClick={onRevealAll} style={{ fontSize: 14 }}>
          {allRevealed ? '已全部揭示' : '揭示全部'}
        </a>
      </div>

      {/* 文本区域 */}
      <div style={{
        fontSize: 18,
        lineHeight: 2.2,
        letterSpacing: '0.05em',
        padding: '16px 20px',
        background: '#fafafa',
        borderRadius: 8,
        border: '1px solid #f0f0f0',
        userSelect: 'none',
      }}>
        {segments.map((seg, idx) => {
          if (!seg.hidden) {
            return <span key={idx}>{seg.text}</span>
          }

          const isRevealed = allRevealed || revealedIndices.has(idx)

          if (isRevealed) {
            return (
              <Tooltip key={idx} title="点击隐藏">
                <span
                  onClick={() => onToggleReveal(idx)}
                  style={{
                    color: '#52c41a',
                    fontWeight: 600,
                    cursor: 'pointer',
                    borderBottom: '2px solid #52c41a',
                    padding: '0 1px',
                  }}
                >
                  {seg.text}
                </span>
              </Tooltip>
            )
          }

          // 未揭示：显示占位下划线
          const placeholderLen = seg.text.replace(/\s/g, '').length
          return (
            <Tooltip key={idx} title="点击揭示">
              <span
                onClick={() => onToggleReveal(idx)}
                style={{
                  display: 'inline-block',
                  cursor: 'pointer',
                  borderBottom: '2px dashed #d9d9d9',
                  minWidth: placeholderLen * 18,
                  height: 24,
                  verticalAlign: 'baseline',
                  margin: '0 1px',
                  background: '#f5f5f5',
                  borderRadius: 2,
                }}
              />
            </Tooltip>
          )
        })}
      </div>

      {/* 图例 */}
      <div style={{ display: 'flex', gap: 24, marginTop: 12, fontSize: 13, color: '#999' }}>
        <span>
          <span style={{ display: 'inline-block', width: 16, height: 3, background: '#d9d9d9', borderRadius: 1, marginRight: 6, verticalAlign: 'middle' }} />
          未揭示（点击显示）
        </span>
        <span>
          <span style={{ color: '#52c41a', fontWeight: 600, marginRight: 4 }}>字</span>
          已揭示（点击隐藏）
        </span>
      </div>
    </div>
  )
}
