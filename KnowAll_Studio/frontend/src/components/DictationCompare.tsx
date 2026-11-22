import { useMemo } from 'react'
import { Progress, Tag, Typography } from 'antd'
import { CheckCircleOutlined, CloseCircleOutlined, MinusCircleOutlined } from '@ant-design/icons'
import type { DiffSegment } from '../types'

const { Text } = Typography

interface DictationCompareProps {
  diffs: DiffSegment[]
  accuracy: number
  correctChars: number
  missingChars: number
  extraChars: number
  originalLength: number
  inputLength: number
}

/**
 * 默写对比组件
 * 展示 diff 对比结果，用颜色高亮正确/漏写/多写的部分：
 * - 绿色：正确（equal）
 * - 红色 + 删除线：漏写（delete，原文有但用户没写）
 * - 橙色：多写（insert，用户多写了）
 */
export default function DictationCompare({
  diffs, accuracy, correctChars, missingChars, extraChars, originalLength, inputLength,
}: DictationCompareProps) {
  const accuracyPercent = useMemo(() => Math.round(accuracy * 100), [accuracy])
  const statusColor = accuracyPercent >= 90 ? '#52c41a' : accuracyPercent >= 60 ? '#faad14' : '#ff4d4f'

  return (
    <div>
      {/* 评分卡 */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 24, alignItems: 'center',
        padding: '16px 20px', background: '#fafafa', borderRadius: 8, marginBottom: 20,
        border: '1px solid #f0f0f0',
      }}>
        <div style={{ textAlign: 'center', minWidth: 120 }}>
          <Progress
            type="circle"
            percent={accuracyPercent}
            size={80}
            strokeColor={statusColor}
            format={p => <span style={{ fontSize: 20, fontWeight: 700, color: statusColor }}>{p}%</span>}
          />
          <div style={{ marginTop: 8, fontSize: 13, color: '#999' }}>准确率</div>
        </div>

        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          <Tag icon={<CheckCircleOutlined />} color="success" style={{ fontSize: 14, padding: '4px 12px' }}>
            正确 {correctChars} 字
          </Tag>
          <Tag icon={<CloseCircleOutlined />} color="error" style={{ fontSize: 14, padding: '4px 12px' }}>
            漏写 {missingChars} 字
          </Tag>
          <Tag icon={<MinusCircleOutlined />} color="warning" style={{ fontSize: 14, padding: '4px 12px' }}>
            多写 {extraChars} 字
          </Tag>
        </div>

        <div style={{ marginLeft: 'auto', fontSize: 13, color: '#999' }}>
          <div>原文 {originalLength} 字 · 你的输入 {inputLength} 字</div>
        </div>
      </div>

      {/* Diff 对比区 */}
      <div style={{
        fontSize: 18,
        lineHeight: 2.2,
        letterSpacing: '0.05em',
        padding: '16px 20px',
        background: '#fff',
        borderRadius: 8,
        border: '1px solid #f0f0f0',
      }}>
        {diffs.map((seg, idx) => {
          switch (seg.type) {
            case 'equal':
              return (
                <span key={idx} style={{ color: '#333' }}>
                  {seg.text}
                </span>
              )
            case 'delete':
              return (
                <span key={idx} style={{
                  color: '#ff4d4f',
                  textDecoration: 'line-through',
                  background: 'rgba(255,77,79,0.08)',
                  borderRadius: 2,
                  padding: '0 1px',
                }}>
                  {seg.text}
                </span>
              )
            case 'insert':
              return (
                <span key={idx} style={{
                  color: '#fa8c16',
                  background: 'rgba(250,140,22,0.08)',
                  borderRadius: 2,
                  padding: '0 1px',
                  fontStyle: 'italic',
                }}>
                  {seg.text}
                </span>
              )
            default:
              return null
          }
        })}
      </div>

      {/* 图例 */}
      <div style={{ display: 'flex', gap: 24, marginTop: 12, fontSize: 13, color: '#999' }}>
        <span>
          <span style={{ color: '#333', marginRight: 4 }}>■</span>
          正确
        </span>
        <span>
          <span style={{ color: '#ff4d4f', textDecoration: 'line-through', marginRight: 4 }}>■</span>
          漏写（原文有，你没写）
        </span>
        <span>
          <span style={{ color: '#fa8c16', fontStyle: 'italic', marginRight: 4 }}>■</span>
          多写（你多写了）
        </span>
      </div>
    </div>
  )
}
