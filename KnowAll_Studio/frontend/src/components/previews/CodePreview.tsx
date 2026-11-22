import { useState, useEffect } from 'react'
import { Spin, Empty } from 'antd'
import SyntaxHighlighter from 'react-syntax-highlighter/dist/esm/prism-async-light'
import vscDarkPlus from 'react-syntax-highlighter/dist/esm/styles/prism/vsc-dark-plus'
import { codeLanguages } from './codeLanguages'

interface CodePreviewProps {
  fileUrl: string
  fileName?: string
  fileType?: string
  maxHeight?: string
}

export default function CodePreview({ fileUrl, fileName, fileType, maxHeight = '65vh' }: CodePreviewProps) {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false
    setLoading(true)
    setError('')
    setContent('')
    // FilePreview already authorizes the raw file and supplies its Blob URL.
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

  const ext = fileType || fileName?.split('.').pop() || ''
  const lang = codeLanguages[ext.toLowerCase()] || 'text'
  const displayContent = content.length > 200000 ? content.slice(0, 200000) + '\n\n// 文件较长，仅显示部分内容。' : content

  return (
    <div className="code-preview" style={{ maxHeight, overflow: 'auto', borderRadius: 8, border: '1px solid #1e1e1e' }}>
      <div style={{
        background: '#2d2d2d', color: '#ccc', padding: '4px 16px',
        fontSize: 12, fontFamily: 'monospace',
        display: 'flex', justifyContent: 'space-between',
      }}>
        <span>{fileName || `代码文件`}</span>
        <span>{lang}</span>
      </div>
      <SyntaxHighlighter
        language={lang}
        style={vscDarkPlus}
        showLineNumbers
        customStyle={{ margin: 0, borderRadius: '0 0 8px 8px', fontSize: 13 }}
        lineNumberStyle={{ color: '#666', fontSize: 11 }}
      >
        {displayContent}
      </SyntaxHighlighter>
    </div>
  )
}
