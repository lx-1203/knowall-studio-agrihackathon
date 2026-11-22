import { useEffect, useRef, useState } from 'react'
import { Document, Outline, Page, pdfjs } from 'react-pdf'
import type { DocumentProps } from 'react-pdf'
import { Spin, Button, Space, InputNumber, theme } from 'antd'
import { LeftOutlined, RightOutlined, ZoomInOutlined, ZoomOutOutlined } from '@ant-design/icons'
import { useAuthStore } from '../../stores'
import { usePdfProgress } from '../../hooks/usePdfProgress'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'
import './pdf-preview.css'

// Use bundled worker from react-pdf
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

const pdfOptions = {
  cMapUrl: '/pdfjs/cmaps/', cMapPacked: true,
  standardFontDataUrl: '/pdfjs/standard_fonts/', wasmUrl: '/pdfjs/wasm/',
  useSystemFonts: false,
}

interface PdfPreviewProps {
  documentId: string
  fileUrl: string
  fileName?: string
  maxHeight?: string
}

function PdfReader({ documentId,fileUrl, maxHeight,userId }: PdfPreviewProps & { userId: string }) {
  const { token } = theme.useToken()
  const area = useRef<HTMLDivElement>(null)
  const pagesArea = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(300)
  const [numPages, setNumPages] = useState<number>(0)
  const progress=usePdfProgress(userId,documentId,fileUrl)
  const {page:pageNumber,zoom:scale,setPage:setPageNumber,setZoom:setScale}=progress
  const [outline, setOutline] = useState<'loading' | 'ready' | 'empty' | 'error'>('loading')

  useEffect(() => {
    if (!area.current) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(160, entry.contentRect.width - 24)))
    observer.observe(area.current)
    return () => observer.disconnect()
  }, [])
  const onLoadSuccess = ({ numPages }: { numPages: number }) => {
    setNumPages(numPages)
    progress.activate(numPages)
  }
  // The native LinkService resolves destinations to this document's page number.
  const onItemClick: NonNullable<DocumentProps['onItemClick']> = ({ pageNumber: target }) => {
    if (!Number.isInteger(target) || target < 1) return
    setPageNumber(target)
    pagesArea.current?.scrollTo({ top: 0 })
  }

  return (
    <div ref={area} className="material-pdf" style={{ width: '100%', minWidth: 0,
      color: token.colorText, '--pdf-surface': token.colorBgLayout,
      '--pdf-link': token.colorLink, '--pdf-border': token.colorBorder,
    } as React.CSSProperties}>
      <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'center', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
        <Space>
          <Button aria-label="PDF 缩小" icon={<ZoomOutOutlined />} size="small" disabled={scale <= 0.5}
            onClick={() => setScale(s => Math.max(0.5, s - 0.2))} />
          <span style={{ fontSize: 13 }}>{Math.round(scale * 100)}%</span>
          <Button aria-label="PDF 放大" icon={<ZoomInOutlined />} size="small" disabled={scale >= 3}
            onClick={() => setScale(s => Math.min(3, s + 0.2))} />
        </Space>
        <Space>
          <Button aria-label="PDF 上一页" icon={<LeftOutlined />} size="small" disabled={pageNumber <= 1 || !numPages}
            onClick={() => setPageNumber(p => Math.max(1, p - 1))} />
          <InputNumber aria-label="PDF 页码" size="small" min={1} max={numPages || 1} value={pageNumber} precision={0} disabled={!numPages}
            onChange={v => v && setPageNumber(Math.max(1, Math.min(v, numPages)))} style={{ width: 60 }} />
          <span style={{ fontSize: 13 }}>/ {numPages}</span>
          <Button aria-label="PDF 下一页" icon={<RightOutlined />} size="small" disabled={pageNumber >= numPages}
            onClick={() => setPageNumber(p => Math.min(numPages, p + 1))} />
        </Space>
      </div>

      {!!progress.error && <div role="status">{progress.error}</div>}
      <div ref={pagesArea} className="material-pdf-pages" style={{ maxHeight: maxHeight || '65vh', overflow: 'auto', borderRadius: 8, padding: 12 }}>
        <Document file={fileUrl} options={pdfOptions} onLoadSuccess={onLoadSuccess} onItemClick={onItemClick}
          loading={<Spin style={{ display: 'block', padding: 40 }} />}
          error={<div role="status" style={{ padding: 40, color: token.colorError }}>PDF 加载失败，请下载原文件检查。</div>}>
          <details className="material-pdf-outline" open>
            <summary>教材目录</summary>
            <Outline onItemClick={onItemClick}
              onLoadSuccess={items => setOutline(items?.length ? 'ready' : 'empty')}
              onLoadError={() => setOutline('error')} />
            {outline === 'loading' && <Spin size="small" />}
            {outline === 'empty' && <p>此文档没有目录</p>}
            {outline === 'error' && <p>目录暂不可用，可使用页码继续阅读。</p>}
          </details>
          <Page pageNumber={pageNumber} width={Math.min(width, 760)} scale={scale}
            onRenderSuccess={()=>progress.markRendered(pageNumber,scale)}
            renderTextLayer={true} renderAnnotationLayer={true} />
        </Document>
      </div>
    </div>
  )
}

export default function PdfPreview(props: PdfPreviewProps) {
  const userId = useAuthStore(state => state.user?.id || 'local')
  return <PdfReader key={JSON.stringify([userId, props.documentId, props.fileUrl])} {...props} userId={userId} />
}
