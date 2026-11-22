import { SyncSlot, type StateKind, type StatePayload, type SyncResult } from '../materialStateSync'
import { getMaterialState, putMaterialState } from '../api'
import { storage } from './storage'
const materialSessionQueueOwner = {}
export function syncMessage(result?: SyncResult) {
  if (!result || ['synced', 'downloaded', 'pending'].includes(result.status)) return ''
  return result.status === 'conflict' ? '跨端版本冲突：本机和服务器副本均已保留，未覆盖。'
    : result.status === 'storage_error' ? '同步备份或记录保存失败，原本机数据保留。请重试。'
    : '本机已保留，暂未同步到服务器。联网后重新打开或重试保存。'
}
export function createMobileStateBridge(accountId: string, documentId: string, kind: Exclude<StateKind,'pdf_progress'|'image_progress'>, canApply: () => boolean) {
  const key = kind === 'note' ? `material-note:${accountId}:${documentId}` : `material-progress:${accountId}:${documentId}`
  const decode = (raw: string): StatePayload => {
    if (kind === 'note') return { text: raw }
    const value = JSON.parse(raw)
    if (!Number.isSafeInteger(value.index) || value.index < 0 || typeof value.fontSize !== 'number' || value.fontSize < 16 || value.fontSize > 26) throw Error('Invalid local progress')
    return { chunk_index: value.index, chunk_id: null }
  }
  const slot = new SyncSlot({ storage, key, accountId, documentId, kind, canApply, decode,
    encode: (value, previous) => {
      if ('text' in value) return value.text
      if (!('chunk_index' in value)) throw Error('Invalid text state')
      return JSON.stringify({ index: value.chunk_index, fontSize: previous ? JSON.parse(previous).fontSize : 18 })
    },
    api: { get: () => getMaterialState(documentId, kind), put: async body => {
      try { return await putMaterialState(documentId, kind, body) }
      catch (error) { const e = error as { current?: unknown; response?: { data?: { detail?: { current?: unknown } } } }; e.current = e.response?.data?.detail?.current; throw error }
    } } })
  const listeners = new Set<(result: SyncResult) => void>()
  let wanted = false, running: Promise<SyncResult> | null = null
  const sync = (): Promise<SyncResult> => {
    wanted = true
    if (running) return running
    running = (async () => {
      let result: SyncResult = { status: 'pending' }
      while (wanted) { wanted = false; result = await slot.sync(); listeners.forEach(fn => fn(result)) }
      return result
    })().finally(() => { running = null })
    return running
  }
  return { slot, sync, subscribe: (fn: (r: SyncResult) => void) => { listeners.add(fn); return () => { listeners.delete(fn) } },
    store: { queueOwner: materialSessionQueueOwner, getItem: (k: string) => storage.getItem(k), setItem: async (k: string, raw: string) => {
      if (k !== key) return storage.setItem(k, raw)
      await slot.saveRaw(raw); void sync()
    } } }
}
