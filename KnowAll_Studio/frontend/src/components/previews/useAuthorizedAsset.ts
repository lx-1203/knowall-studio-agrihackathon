import { useEffect, useState } from 'react'
import { getAuthToken } from '../../authToken'

/** Blob URLs let PDF/image/document viewers use protected files without URL tokens. */
export default function useAuthorizedAsset(path?: string) {
  const token = getAuthToken()
  const requestKey = `${token || ''}:${path || ''}`
  const [asset, setAsset] = useState<{ key: string; url?: string; error?: string }>({ key: '' })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!path) return
    const controller = new AbortController()
    let cancelled = false
    let objectUrl = ''
    setAsset({ key: requestKey })
    async function load() {
      const target = new URL(path!, window.location.href)
      if (target.origin !== window.location.origin || !target.pathname.startsWith('/api/v1/documents/')) throw new Error('文件地址不可用')
      const response = await fetch(target.href, { signal: controller.signal, headers: token ? { Authorization: `Bearer ${token}` } : {} })
      if (!response.ok || response.status === 204) throw new Error('原文件未能加载，请重试或切换到阅读正文。')
      const blob = await response.blob()
      if (cancelled) return
      objectUrl = URL.createObjectURL(blob)
      setAsset({ key: requestKey, url: objectUrl })
    }
    load().catch(() => { if (!cancelled) setAsset({ key: requestKey, error: '原文件未能加载，请重试或切换到阅读正文。' }) })
    return () => { cancelled = true; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [path, token, requestKey, attempt])
  return { url: asset.key === requestKey ? asset.url : undefined, error: asset.key === requestKey ? asset.error : undefined, reload: () => setAttempt(value => value + 1) }
}
