import type { AuthUser } from './types'

export function canUseLocalSession(): boolean {
  return ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)
}

export async function openLocalSession(): Promise<{ access_token: string; user: AuthUser } | null> {
  if (!canUseLocalSession()) return null
  let userId: string | undefined
  try { userId = JSON.parse(localStorage.getItem('knowall_user') || 'null')?.id } catch {}
  userId ||= localStorage.getItem('knowall_local_user_id') || undefined
  const response = await fetch('/api/v1/auth/local-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-KnowAll-Local': '1' },
    body: JSON.stringify({ user_id: userId }),
    signal: AbortSignal.timeout(10000),
  })
  // Compatibility with older backends that have no local session endpoint.
  if (response.status === 404) return null
  if (!response.ok) throw new Error('本地服务连接失败，请确认后台已启动后重试。')
  const data = await response.json()
  if (!data.enabled) return null
  if (!data.access_token || !data.user?.id) throw new Error('本地会话建立失败，请重试。')
  localStorage.setItem('knowall_local_user_id', data.user.id)
  return data
}

