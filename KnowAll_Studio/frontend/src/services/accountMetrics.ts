import type { AxiosRequestConfig } from 'axios'
import api from '../api'

export interface AccountUsage {
  tokens: number; input_tokens: number; output_tokens: number; calls: number; unmetered_calls: number
  day: string; utc_offset_minutes: number; scope: 'account'; source: 'local_call_logs'; checked_at: string
}
export interface ServerCache {
  active_entries: number; stored_entries: number; expired_entries: number; invalid_entries: number
  scope: 'server'; source: 'cache_database'; checked_at: string
}
export interface ProviderBalance {
  status: 'available' | 'unavailable' | 'unsupported' | 'error'; connection_name?: string; message?: string
  balances: { currency: 'CNY' | 'USD'; total_balance: string }[]; checked_at?: string; is_available?: boolean
}
const object = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0

export async function readAccountUsage(config?: AxiosRequestConfig): Promise<AccountUsage> {
  const { data } = await api.get<unknown>('/ai/usage', { ...config, params: { utc_offset_minutes: -new Date().getTimezoneOffset() } })
  if (!object(data) || data.scope !== 'account' || data.source !== 'local_call_logs'
    || !['tokens', 'input_tokens', 'output_tokens', 'calls', 'unmetered_calls'].every(key => count(data[key]))
    || typeof data.day !== 'string' || typeof data.utc_offset_minutes !== 'number' || typeof data.checked_at !== 'string') throw new Error('用量统计不完整，请重新加载。')
  return data as unknown as AccountUsage
}
export async function readServerCache(config?: AxiosRequestConfig): Promise<ServerCache> {
  const { data } = await api.get<unknown>('/ai/cache/stats', config)
  if (!object(data) || data.scope !== 'server' || data.source !== 'cache_database'
    || !['active_entries', 'stored_entries', 'expired_entries', 'invalid_entries'].every(key => count(data[key]))
    || typeof data.checked_at !== 'string') throw new Error('缓存统计不完整，请重新加载。')
  return data as unknown as ServerCache
}
export async function readProviderBalance(config?: AxiosRequestConfig): Promise<ProviderBalance> {
  const { data } = await api.get<unknown>('/ai/balance', config)
  if (!object(data) || !['available', 'unavailable', 'unsupported', 'error'].includes(String(data.status)) || !Array.isArray(data.balances)) throw new Error('余额数据不完整，请重查。')
  if (data.status === 'error') throw new Error('余额查询失败，请检查连接或稍后重试。')
  if (data.status === 'available' && (!data.balances.length || !data.balances.every(row => object(row)
    && ['CNY', 'USD'].includes(String(row.currency)) && typeof row.total_balance === 'string' && /^-?\d+(?:\.\d+)?$/.test(row.total_balance)))) throw new Error('服务商未返回有效余额，请重查。')
  return data as unknown as ProviderBalance
}
export const readAccountCalls = (limit: number, config?: AxiosRequestConfig) => api.get('/ai/calls', { ...config, params: { limit } }).then(r => r.data)
