import api from './api'
import type { AxiosRequestConfig } from 'axios'

export type ModelChoice = { value: string; label: string; model_id: string; display_name: string }
export type ConnectionDraft = { name: string; provider: string; protocol: 'openai' | 'anthropic'; base_url: string; api_key: string; model: string; is_active: boolean }
export type ConnectionTest = { status: 'success' | 'failed'; message: string; checked_at: string; model: string; latency_ms: number }
export type ModelConnection = Omit<ConnectionDraft, 'api_key'> & { id: string; model_ref: string; has_key: boolean; key_masked: string; models: ModelChoice[]; fetched_at: string | null; last_test: ConnectionTest | null }
export type Preset = { provider: string; name: string; base_url: string; protocol: ConnectionDraft['protocol'] }
export type ModelConfiguration = {
  connections: ModelConnection[]; default_model: string; default_source?: 'personal' | 'legacy' | 'none'; presets: Preset[]
  legacy_keys: { id: string; name: string; provider: string; base_url: string; is_active: boolean; scope: string }[]
  server_connections: { base_url: string; model: string; protocol: string }[]
}
export const readModelConnections = (config?: AxiosRequestConfig) => api.get<ModelConfiguration>('/ai/config', config).then(r => r.data)
export const saveModelConnection = (draft: ConnectionDraft, id?: string) => (id ? api.put<ModelConnection>(`/ai/connections/${id}`, draft) : api.post<ModelConnection>('/ai/connections', draft)).then(r => r.data)
export const copyLegacyConnection = (id: string) => api.post<ModelConnection>(`/ai/connections/${id}/copy`).then(r => r.data)
export const removeModelConnection = (id: string) => api.delete(`/ai/connections/${id}`).then(r => r.data)
export const chooseDefaultConnection = (row: ModelConnection) => api.put('/ai/default', { connection_id: row.id, model: row.model }).then(r => r.data)
export const fetchConnectionModels = (id: string) => api.post<{ models: ModelChoice[]; fetched_at: string; errors: { message: string }[] }>(`/ai/connections/${id}/models`).then(r => r.data)
export const probeModelConnection = (id: string) => api.post<ConnectionTest>(`/ai/connections/${id}/test`).then(r => r.data)
export function configurationError(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => typeof item.msg === 'string' ? item.msg.replace(/^Value error, /, '') : '').filter(Boolean).join('；') || '配置格式不正确，请检查填写内容。'
  return error instanceof Error && !('isAxiosError' in error) ? error.message : '连接配置服务暂时不可用，请检查后台服务后重试。'
}
