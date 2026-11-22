export type ReadingMode = 'original' | 'text'
import { desktopStateSlot } from './materialStateSync'
export interface MaterialProgress {
  chunkId?: string
  chunkIndex: number
  mode: ReadingMode
  chapter: string
  updatedAt: number
}

export function progressKey(userId: string, documentId: string) {
  return `knowall:material-position:${userId}:${documentId}`
}

export function readProgress(userId: string, documentId: string): MaterialProgress | null {
  try {
    const raw = localStorage.getItem(progressKey(userId, documentId))
    if (!raw) return null
    const value = JSON.parse(raw)
    // Keep reading positions saved by the first version of the workbench.
    if (Number.isInteger(value) && value >= 0) return { chunkIndex: value, mode: 'text', chapter: '', updatedAt: 0 }
    if (!value || typeof value !== 'object' || !Number.isInteger(value.chunkIndex) || value.chunkIndex < 0) return null
    return {
      chunkIndex: value.chunkIndex,
      chunkId: typeof value.chunkId === 'string' ? value.chunkId : undefined,
      mode: value.mode === 'text' ? 'text' : 'original',
      chapter: typeof value.chapter === 'string' ? value.chapter : '',
      updatedAt: typeof value.updatedAt === 'number' && Number.isFinite(value.updatedAt) ? value.updatedAt : 0,
    }
  } catch { return null }
}
export const syncProgress = (userId: string, documentId: string, canApply?: () => boolean) =>
  desktopStateSlot(userId, documentId, 'text_progress', canApply).sync()

export function saveProgress(userId: string, documentId: string, progress: MaterialProgress) {
  if (progress.mode !== 'text' || userId === 'local') {
    try { localStorage.setItem(progressKey(userId, documentId), JSON.stringify(progress)) } catch { /* Local reading remains available. */ }
    return
  }
  const slot = desktopStateSlot(userId, documentId, 'text_progress')
  void slot.saveRaw(JSON.stringify(progress)).then(() => slot.sync()).catch(() => {})
}

export function libraryReturn(state: unknown): string {
  const value = (state as { returnTo?: string } | null)?.returnTo
  return typeof value === 'string' && (value === '/materials' || value.startsWith('/materials?')) ? value : '/materials'
}
