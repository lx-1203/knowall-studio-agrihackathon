import { useState, useEffect } from 'react'
import { Spin, Empty } from 'antd'
import mammoth from 'mammoth'
import DOMPurify from 'dompurify'

interface DocxPreviewProps {
  fileUrl: string
  fileName?: string
  maxHeight?: string
}

export default function DocxPreview({ fileUrl, maxHeight = '65vh' }: DocxPreviewProps) {
  const [html, setHtml] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false
    setLoading(true)
    setError('')
    setHtml('')
    // FilePreview supplies an already-authorized Blob URL; no token is added here.
    fetch(fileUrl, { signal: controller.signal })
      .then(r => {
        if (r.status === 204) throw new Error('原始文件暂不可用，可能已被删除')
        if (!r.ok) throw new Error('加载失败')
        return r.arrayBuffer()
      })
      .then(buf => {
        if (cancelled) return
        return mammoth.convertToHtml({ arrayBuffer: buf })
      })
      .then(result => {
        if (cancelled || !result) return
        if (!DOMPurify.isSupported) throw new Error('当前环境无法安全预览此文档，请切换到阅读正文。')
        // Clean the converter's output immediately before the HTML sink. Keep
        // the upstream URI/clobbering defaults and normal document formatting.
        const clean = DOMPurify.sanitize(result.value, {
          USE_PROFILES: { html: true },
          FORBID_TAGS: ['style', 'form', 'input', 'button', 'select', 'textarea', 'option', 'iframe', 'object', 'embed', 'video', 'audio'],
          FORBID_ATTR: ['style'],
          ALLOW_DATA_ATTR: false,
        })
        setHtml(clean)
        setLoading(false)
      })
      .catch(err => { if (!cancelled) { setError(err.message); setLoading(false) } })
    // Mammoth conversion itself is a Promise, so late conversion results also
    // need the cancelled guard even after aborting the original fetch.
    return () => { cancelled = true; controller.abort() }
  }, [fileUrl])

  if (loading) return <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
  if (error) return <Empty description={`加载失败: ${error}`} />

  return (
    <div
      className="docx-preview"
      style={{
        maxHeight, overflow: 'auto', padding: '24px 32px',
        background: '#fff', borderRadius: 8, border: '1px solid #e8e8e8',
        fontFamily: '"Microsoft YaHei", "SimSun", sans-serif',
        fontSize: 15, lineHeight: 1.8, color: '#333',
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
