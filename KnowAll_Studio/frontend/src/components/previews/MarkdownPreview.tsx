import { useState, useEffect } from 'react'
import { Spin, Empty, theme } from 'antd'
import MarkdownContent from '../MarkdownContent'
import './markdown-preview.css'

interface MarkdownPreviewProps {
  fileUrl: string
  fileName?: string
  fileType?: string
  maxHeight?: string
}

export default function MarkdownPreview({ fileUrl, fileName, fileType, maxHeight = '65vh' }: MarkdownPreviewProps) {
  const { token } = theme.useToken()
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false
    setLoading(true)
    setError('')
    setContent('')
    // The parent supplies an already authorized Blob; no second credential path.
    fetch(fileUrl, { signal: controller.signal })
      .then(r => {
        if (r.status === 204) throw new Error('原始文件暂不可用，可能已被删除')
        if (!r.ok) throw new Error('加载失败')
        return r.text()
      })
      .then(text => { if (!cancelled) { setContent(text); setLoading(false) } })
      .catch(err => { if (!cancelled) { setError(err.message); setLoading(false) } })
    return () => { cancelled = true; controller.abort() }
  }, [fileUrl])

  if (loading) return <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
  if (error) return <Empty description={`加载失败: ${error}`} />

  const type = (fileType || fileName?.split('.').pop() || '').toLowerCase()
  const isMarkdown = type === 'md' || type === 'markdown'
  const truncated = content.length > 100000
  // Avoid cutting a UTF-16 surrogate pair at the existing display bound.
  const displayContent = truncated ? content.slice(0, 100000).replace(/[\uD800-\uDBFF]$/, '') : content

  return (
    <div style={{
      maxHeight, overflow: 'auto', padding: '24px 32px',
      background: token.colorBgContainer, borderRadius: 8, border: `1px solid ${token.colorBorder}`,
      fontFamily: '"Microsoft YaHei", "SimSun", sans-serif',
      fontSize: 15, lineHeight: 1.8, color: token.colorText,
    }}
      className="markdown-preview">
      {isMarkdown ? (
        <MarkdownContent text={displayContent} />
      ) : (
        <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'inherit' }}>
          {displayContent}
        </pre>
      )}
      {truncated && <p role="status" className="markdown-limit">内容较长，当前仅显示部分内容；下载文件可查看完整原文。</p>}
    </div>
  )
}
