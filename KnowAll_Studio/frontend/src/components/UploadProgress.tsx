import { Alert, Button, Progress, Space, Spin } from 'antd'
import type useUploadRequest from '../hooks/useUploadRequest'

export default function UploadProgress({ request }: { request: ReturnType<typeof useUploadRequest> }) {
  if (request.phase === 'cancelled') return <Alert type="info" showIcon
    message="已停止等待，服务端已收到的资料可能仍会保存，可刷新资料库确认。" />
  if (!request.active) return null
  return <div aria-live="polite">
    {request.percent === null ? <Space><Spin size="small" />正在传输文件...</Space>
      : <Progress aria-label="文件上传进度" percent={request.percent} status="active" format={p => `${p}% 已传输`} />}
    <p>{request.phase === 'processing' ? '文件已传输，正在等待解析结果。' : '正在传输文件，请稍候。'}</p>
    <Button onClick={request.cancel}>停止当前上传</Button>
  </div>
}
