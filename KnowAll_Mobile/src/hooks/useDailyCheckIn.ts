import { useCallback, useRef, useState } from 'react'
import { AppState } from 'react-native'
import { useFocusEffect } from 'expo-router'
import api from '../api'
import { useAuthStore } from '../stores'
import { type CheckInSummary, millisecondsToBeijingMidnight } from '../checkIn'
import { noReplay, screenError } from '../services/mobileScreenActions'

function checkedData(data: CheckInSummary) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(data?.today) || typeof data.checked_in_today !== 'boolean'
    || !/^\d{4}-\d{2}$/.test(data.month) || !Array.isArray(data.records)
    || ![data.total_days, data.current_streak, data.longest_streak].every(n => Number.isInteger(n) && n >= 0)) throw new Error('打卡记录返回不完整')
  return data
}

export function useDailyCheckIn(month?: string) {
  const userId = useAuthStore(state => state.user?.id)
  const owner = useRef(userId); owner.current = userId
  const [cache, setCache] = useState<{ owner: string; data: CheckInSummary } | null>(null)
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const revision = useRef(0), request = useRef<AbortController | null>(null), operation = useRef(false)
  const load = useCallback(async () => {
    if (!userId || operation.current) return
    request.current?.abort(); const controller = new AbortController(); request.current = controller
    const current = ++revision.current; setLoading(true); setError('')
    try {
      const data = checkedData((await api.get<CheckInSummary>('/check-ins', { params: month ? { month } : {}, signal: controller.signal })).data)
      if (current === revision.current && owner.current === userId) setCache({ owner: userId, data })
    } catch (e) { if (current === revision.current && owner.current === userId) setError(screenError(e, '打卡记录暂时无法读取，请重试。')) }
    finally { if (current === revision.current && owner.current === userId) setLoading(false) }
  }, [userId, month])
  useFocusEffect(useCallback(() => {
    void load()
    const subscription = AppState.addEventListener('change', state => { if (state === 'active') void load() })
    let timer: ReturnType<typeof setTimeout>
    const refreshAtMidnight = () => {
      if (operation.current) { timer = setTimeout(refreshAtMidnight, 1000); return }
      void load(); schedule()
    }
    const schedule = () => { timer = setTimeout(refreshAtMidnight, millisecondsToBeijingMidnight() + 250) }
    schedule()
    return () => { revision.current++; request.current?.abort(); subscription.remove(); clearTimeout(timer) }
  }, [load]))
  const submit = async (note: string) => {
    if (!userId || operation.current) return
    operation.current = true; setBusy(true); setError(''); request.current?.abort()
    const current = ++revision.current
    try {
      const result = (await api.post<CheckInSummary & { already_checked_in: boolean }>('/check-ins', { note }, noReplay)).data
      const data = checkedData(result)
      if (!data.checked_in_today || typeof result.already_checked_in !== 'boolean') throw new Error('打卡回执不完整')
      if (current !== revision.current || owner.current !== userId) return
      setCache({ owner: userId, data })
      return result
    } catch (e) { if (current === revision.current && owner.current === userId) setError(screenError(e, '没有收到打卡回执，请先刷新核对。重复提交不会多记一天。')) }
    finally { operation.current = false; if (owner.current === userId) setBusy(false) }
  }
  return { data: cache && cache.owner === userId ? cache.data : null, loading, busy, error, reload: load, submit }
}
