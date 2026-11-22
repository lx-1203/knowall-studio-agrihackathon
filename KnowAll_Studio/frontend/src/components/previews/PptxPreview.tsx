import { useState, useEffect } from 'react'
import { Button, Spin, Empty, theme } from 'antd'
import { authHeaderValue } from '../../authToken'
import useAuthorizedAsset from './useAuthorizedAsset'
import ImagePreview from './ImagePreview'

const apiBase = '/api/v1'

interface PptxPreviewProps {
  documentId: string
  fileName?: string
  maxHeight?: string
}

function PptxReader({ documentId, maxHeight = '60vh' }: PptxPreviewProps) {
  const { token } = theme.useToken()
  const [slides, setSlides] = useState<{ index: number; image_url: string }[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [current, setCurrent] = useState(0)
  const slideAsset = useAuthorizedAsset(slides[current]?.image_url)

  useEffect(() => {
    setLoading(true)
    setError('')
    let cancelled = false
    const controller = new AbortController()
    setSlides([]); setCurrent(0)
    fetch(`${apiBase}/documents/${encodeURIComponent(documentId)}/slides`, { headers: { Authorization: authHeaderValue() }, signal: controller.signal })
      .then(r => {
        if (!r.ok) throw new Error(r.status === 400 ? '仅支持 PPTX 格式' : '加载失败')
        return r.json()
      })
      .then(data => {
        if (cancelled) return
        setSlides(data.slides || [])
        setLoading(false)
      })
      .catch(err => { if (!cancelled) { setError(err.message); setLoading(false) } })
    return () => { cancelled = true; controller.abort() }
  }, [documentId])

  if (loading) return <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
  if (error) return <Empty description={`加载失败: ${error}`} />
  if (!slides.length) return <Empty description="无幻灯片" />

  let content = <Spin />
  if (slideAsset.error) {
    content = <div><Empty description="幻灯片图片未能加载" /><Button onClick={slideAsset.reload}>重试当前页</Button></div>
  } else if (slideAsset.url) {
    content = <ImagePreview fileUrl={slideAsset.url} fileName={`幻灯片 ${current + 1}`} maxHeight={maxHeight} />
  }

  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ marginBottom: 8, fontSize: 13, color: token.colorTextSecondary }}>
        {current + 1} / {slides.length}
      </div>
      {content}
      <div style={{ marginTop: 12, display: 'flex', justifyContent: 'center', gap: 16 }}>
        <Button onClick={() => setCurrent(c => Math.max(0, c - 1))}
          disabled={current === 0}
        >
          上一页
        </Button>
        <span style={{ lineHeight: '32px', fontSize: 13 }}>
          {current + 1} / {slides.length}
        </span>
        <Button onClick={() => setCurrent(c => Math.min(slides.length - 1, c + 1))}
          disabled={current >= slides.length - 1}
        >
          下一页
        </Button>
      </div>
    </div>
  )
}

export default function PptxPreview(props: PptxPreviewProps) {
  return <PptxReader key={props.documentId} {...props} />
}
