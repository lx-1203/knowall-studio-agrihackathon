import { useCallback, useEffect, useRef, useState } from 'react'
import api from '../../api'

export const consolePath = '/admin/console'
export type Overview = {
  totals: { users: number; active_users: number; documents: number; questions: number; flashcards: number; reviews: number }
  period: { days: number; calls: number; successes: number; tokens: number; cost: number; avg_duration_ms: number }
  daily: { date: string; calls: number; successes: number; tokens: number }[]
  models: { model: string; calls: number; tokens: number }[]
  document_types: { type: string; count: number }[]
  learning: { attempts: number; correct: number; accuracy: number }
  generated_at: string
}
export type AdminKey = { id: string; provider: string; key_alias: string; key_masked: string; is_active: boolean; permission_level: string }
export type ModelConfig = { model: string; fallback_models: string[]; daily_token_limit: number }
export type CallLog = { id: string; task_type: string; model_name: string; tokens_input: number; tokens_output: number; cost_estimate: number; from_cache: boolean; success: boolean; duration_ms: number; created_at: string }

export function errorText(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : '暂时无法连接管理服务，请稍后重试。'
}

/** Each resource has its own cancellation and error state; old responses cannot overwrite newer reads. */
export function useAdminRead<T>(path: string) {
  const [data, setData] = useState<T | null>(null)
  const [pending, setPending] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [version, setVersion] = useState(0)
  const generation = useRef(0)
  const reload = useCallback(() => setVersion(value => value + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    const id = ++generation.current
    setPending(true); setError(null); setData(null)
    api.get<T>(`${consolePath}${path}`, { signal: controller.signal, timeout: 20000 }).then(response => {
      if (!controller.signal.aborted && id === generation.current) setData(response.data)
    }).catch(reason => {
      if (!controller.signal.aborted && id === generation.current) setError(reason)
    }).finally(() => {
      if (!controller.signal.aborted && id === generation.current) setPending(false)
    })
    return () => controller.abort()
  }, [path, version])
  return { data, pending, error, reload }
}

export const count = (value: number | undefined) => new Intl.NumberFormat('zh-CN').format(value || 0)
export const localTime = (value: string) => {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}
