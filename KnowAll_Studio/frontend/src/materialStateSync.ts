import { SyncSlot, type StateKind, type StatePayload, type SyncResult } from '../../../KnowAll_Mobile/src/materialStateSync'
import { getMaterialState, putMaterialState } from './api'
// One stable facade gives every slot the same short local-IO queue per key.
const storage = { getItem: async (key: string) => localStorage.getItem(key), setItem: async (key: string, raw: string) => { localStorage.setItem(key, raw) } }
export function desktopStateSlot(userId: string, documentId: string, kind: Exclude<StateKind,'pdf_progress'|'image_progress'>, canApply?: () => boolean) {
  const key = kind === 'note' ? `knowall:material-note:${userId}:${documentId}` : `knowall:material-position:${userId}:${documentId}`
  return new SyncSlot({ storage, key, accountId: userId, documentId, kind, canApply,
    decode: (raw): StatePayload => {
      if (kind === 'note') return { text: raw }
      const value = JSON.parse(raw), index = Number.isInteger(value) ? value : value?.chunkIndex
      if (!Number.isSafeInteger(index) || index < 0) throw Error('Invalid local progress')
      return { chunk_index: index, chunk_id: typeof value?.chunkId === 'string' ? value.chunkId : null }
    },
    encode: (value, previous) => {
      if ('text' in value) return value.text
      if (!('chunk_index' in value)) throw Error('Invalid text state')
      const old = previous ? JSON.parse(previous) : null
      return JSON.stringify({ ...(old && typeof old === 'object' ? old : {}), chunkIndex: value.chunk_index,
        chunkId: value.chunk_id ?? undefined, mode: 'text', chapter: '', updatedAt: Date.now() })
    },
    api: { get: () => getMaterialState(documentId, kind), put: async body => {
      try { return await putMaterialState(documentId, kind, body) }
      catch (error) { const e = error as { current?: unknown; response?: { data?: { detail?: { current?: unknown } } } }; e.current = e.response?.data?.detail?.current; throw error }
    } } })
}
export const materialSyncMessage = (result: SyncResult) => result.status === 'conflict'
  ? '跨端版本冲突，两份副本已保留，未自动覆盖。'
  : result.status === 'offline' ? '本机已保留，暂未同步到服务器。'
  : result.status === 'storage_error' ? '同步备份或记录保存失败，本机内容仍保留。' : ''
