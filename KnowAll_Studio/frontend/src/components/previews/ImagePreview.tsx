import { useEffect, useRef, useState } from 'react'
import { Button, Empty, Image, Space, Spin, theme } from 'antd'
import type { ImageProps } from 'antd/es/image'
import { useAuthStore } from '../../stores'
import { usePdfProgress } from '../../hooks/usePdfProgress'
import api from '../../api'

interface ImagePreviewProps {
  documentId?: string
  fileType?: string
  fileUrl: string
  fileName?: string
  maxHeight?: string
}

type PreviewConfig = Exclude<NonNullable<ImageProps['preview']>, boolean>
const renderToolbar: NonNullable<PreviewConfig['toolbarRender']> = (_, { actions, transform }) => (
  <Space wrap style={{ padding: 12, justifyContent: 'center', maxWidth: '90vw' }}>
    <Button size="small" autoInsertSpace={false} onClick={actions.onZoomOut} disabled={transform.scale <= 1}>缩小</Button>
    <Button size="small" autoInsertSpace={false} onClick={actions.onZoomIn} disabled={transform.scale >= 50}>放大</Button>
    <Button size="small" autoInsertSpace={false} onClick={actions.onRotateLeft}>左转</Button>
    <Button size="small" autoInsertSpace={false} onClick={actions.onRotateRight}>右转</Button>
    <Button size="small" onClick={actions.onFlipX}>横向翻转</Button>
    <Button size="small" onClick={actions.onFlipY}>纵向翻转</Button>
    <Button size="small" autoInsertSpace={false} onClick={actions.onReset}>恢复</Button>
    <Button size="small" autoInsertSpace={false} onClick={actions.onClose}>关闭</Button>
  </Space>
)

function NativeImagePreview({ documentId, fileUrl, fileName, maxHeight = '65vh' }: ImagePreviewProps) {
  const { token } = theme.useToken()
  const [error, setError] = useState(false)
  const [visible, setVisible] = useState(false)
  const opener = useRef<HTMLButtonElement>(null)
  const userId=useAuthStore(state=>state.user?.id)||'local'
  const progress=usePdfProgress(documentId?userId:'local',documentId||fileUrl,fileUrl,'image_progress')
  const [rendered,setRendered]=useState(false)
  const needsRaster=userId!=='local' && !!documentId
  const [raster,setRaster]=useState<string|null>(null)
  useEffect(()=>{
    if(!needsRaster || !progress.sourceVersion)return
    let live=true,url:string|undefined
    setRendered(false);setRaster(null);setError(false)
    // Match the authenticated raw Blob's version; multi-frame formats share the phone's first frame.
    void api.get(`/documents/${encodeURIComponent(documentId!)}/preview/1/image`,{responseType:'blob',timeout:5000,params:{source_version:progress.sourceVersion}}).then(response=>{
      if(!live)return;url=URL.createObjectURL(response.data);setRaster(url)
    }).catch(()=>{if(live){setRendered(false);setError(true)}})
    return()=>{live=false;if(url)URL.revokeObjectURL(url)}
  },[needsRaster,documentId,progress.sourceVersion])
  useEffect(()=>{if(rendered){progress.activate(1);progress.markRendered(1,progress.zoom)}},[rendered,progress.zoom])

  return (
    <div className="image-preview">
      <Space wrap style={{paddingBottom:12}}>
        <Button aria-label="图片缩小" disabled={progress.zoom<=.5} onClick={()=>progress.setZoom(z=>Math.max(.5,z-.2))}>缩小</Button>
        <span aria-label="图片阅读缩放">{Math.round(progress.zoom*100)}%</span>
        <Button aria-label="图片放大" disabled={progress.zoom>=3} onClick={()=>progress.setZoom(z=>Math.min(3,z+.2))}>放大</Button>
        <Button onClick={()=>progress.setZoom(1)}>适宽</Button>
      </Space>
      {!!progress.error&&<p role="status">{progress.error}</p>}
      <div className="image-preview-viewport" style={{
        display: 'flex', justifyContent: 'flex-start', alignItems: 'flex-start',
        background: token.colorBgLayout, borderRadius: 8, overflow: 'auto', maxHeight,
      }}>
        {error && <Empty description="图片未能显示，可下载原文件查看。" style={{ padding: 24 }} />}
        {!error && needsRaster && !raster && <Spin style={{padding:40}}/>}
        {!error && (!needsRaster || raster) && <div style={{width:`${progress.zoom*100}%`,flexShrink:0}}><Image
          src={raster||fileUrl} alt={fileName || '图片预览'} width="100%"
          placeholder={<Spin style={{ padding: 40 }} />}
          onLoad={()=>setRendered(true)}
          onError={() => { setRendered(false);setError(true); setVisible(false) }}
          style={{ maxWidth: '100%', objectFit: 'contain' }}
          preview={{
            visible, destroyOnHidden: true,
            onVisibleChange: value => { setVisible(value); if (!value) opener.current?.focus() },
            closeIcon: <span aria-label="关闭预览">×</span>,
            toolbarRender: renderToolbar,
          }}
        /></div>}
      </div>
      {!error && <div style={{ textAlign: 'center', paddingTop: 12 }}>
        <Button ref={opener} onClick={() => setVisible(true)}>放大查看</Button>
      </div>}
    </div>
  )
}

export default function ImagePreview(props: ImagePreviewProps) {
  const userId=useAuthStore(state=>state.user?.id)||'local'
  // Native load/transform/modal state belongs to this authorized Blob only.
  // A URL switch destroys the previous dialog before its Blob is revoked.
  return <NativeImagePreview key={`${userId}:${props.documentId||''}:${props.fileUrl}`} {...props} />
}
