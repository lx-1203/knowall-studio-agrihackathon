import { Alert, Button, Result, Space } from 'antd'
import { isAxiosError } from 'axios'

// Presentation only: requests, cancellation and retry ownership stay in callers.
// Never render raw server detail/trace or the Axios config (which carries tokens).
export default function LoadFailure({ title, error, onRetry, retryLabel = '重新加载', onBack, inline = false, pending = false, stacked = false }: {
  title: string; error: unknown; onRetry: () => void; retryLabel?: string
  onBack?: () => void; inline?: boolean; pending?: boolean; stacked?: boolean
}) {
  const status = isAxiosError(error) ? error.response?.status : undefined
  const code = isAxiosError(error) ? error.code : undefined
  let description = '加载失败，请检查网络后重试。'
  if (status === 404) description = '该内容不存在或已被移除。'
  else if (status === 403) description = '当前账号无权访问这份内容。'
  else if (status === 429) description = '请求较多，请稍后再试。'
  else if (status && status >= 500) description = '服务暂时不可用，请稍后重试。'
  else if (code === 'ECONNABORTED' || code === 'ETIMEDOUT') description = '请求超时，请检查网络后重试。'
  const retry = <Button onClick={onRetry} loading={pending} disabled={pending} aria-label={retryLabel}>{retryLabel}</Button>
  if (inline) return <Alert type="error" showIcon message={title}
    description={stacked ? <Space direction="vertical">{description}{retry}</Space> : description} action={stacked ? undefined : retry} />
  return <Result status={status === 403 || status === 404 ? status : 'error'} title={title} subTitle={description}
    extra={<Space>{retry}{onBack && <Button onClick={onBack}>返回资料库</Button>}</Space>} />
}
