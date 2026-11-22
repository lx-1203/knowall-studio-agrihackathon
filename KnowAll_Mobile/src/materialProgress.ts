import { queueMaterialStorage as queueProgressStorage } from './materialStorageQueue.js'
export { queueMaterialStorage as queueProgressStorage } from './materialStorageQueue.js'
export interface ProgressStorage { getItem: (key: string) => Promise<string | null>; setItem: (key: string, text: string) => Promise<void> }
export interface ReadingPosition { index: number; fontSize: number }
export interface MaterialProgressState extends ReadingPosition { key: string; ready: boolean; saving: boolean; dirty: boolean; error: string }
export const materialProgressKey = (accountId?: string, documentId?: string) => accountId && documentId ? `material-progress:${accountId}:${documentId}` : null

const validIndex = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const validFont = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 16 && value <= 26
function parsePosition(raw: string | null): ReadingPosition {
  if (raw === null) return { index: 0, fontSize: 18 }
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || !('index' in value) || !('fontSize' in value) || !validIndex(value.index) || !validFont(value.fontSize)) throw Error('Invalid reading position')
  return { index: value.index, fontSize: value.fontSize }
}

export class MaterialProgressSession {
  readonly key: string
  private store: ProgressStorage
  private position: ReadingPosition = { index: 0, fontSize: 18 }
  private saved: string | null = null
  private count = 0
  private ready = false
  private saving = false
  private error = ''
  private edited = { index: false, fontSize: false }
  private changedByUser = false
  private loading: Promise<boolean> | null = null
  private pending: Promise<boolean> | null = null
  private listeners = new Set<(state: MaterialProgressState) => void>()
  constructor(store: ProgressStorage, key: string) { this.store = store; this.key = key }
  private text() { return JSON.stringify(this.position) }
  snapshot(): MaterialProgressState { return { key: this.key, ...this.position, ready: this.ready, saving: this.saving, dirty: this.ready && this.count > 0 && this.text() !== this.saved, error: this.error } }
  subscribe(listener: (state: MaterialProgressState) => void) { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private emit() { const state = this.snapshot(); this.listeners.forEach(listener => listener(state)) }
  private clamp() { if (this.count) this.position.index = Math.min(this.position.index, this.count - 1) }
  activate(count: number) {
    if (!Number.isSafeInteger(count) || count <= 0 || count === this.count) return
    this.count = count; this.clamp(); this.emit()
  }
  load(): Promise<boolean> {
    if (this.ready) return Promise.resolve(true)
    if (this.loading) return this.loading
    this.error = ''; this.emit()
    this.loading = queueProgressStorage(this.store, this.key, async () => {
      try {
        const raw = await this.store.getItem(this.key)
        const previous = parsePosition(raw)
        this.saved = raw === null ? null : JSON.stringify(previous)
        this.position = { index: this.edited.index ? this.position.index : previous.index, fontSize: this.edited.fontSize ? this.position.fontSize : previous.fontSize }
        this.clamp(); this.ready = true; this.emit()
        return true
      } catch {
        this.error = '阅读进度读取失败，旧记录未被覆盖。可继续阅读，恢复存储后重试。'; this.emit()
        return false
      }
    }).finally(() => { this.loading = null })
    return this.loading
  }
  update(change: Partial<ReadingPosition>) {
    if (validIndex(change.index) || validFont(change.fontSize)) this.changedByUser = true
    if (validIndex(change.index)) { this.position.index = change.index; if (!this.ready) this.edited.index = true }
    if (validFont(change.fontSize)) { this.position.fontSize = change.fontSize; if (!this.ready) this.edited.fontSize = true }
    this.clamp()
    if (this.ready) this.error = ''
    this.emit()
  }
  async reloadSaved(): Promise<boolean> {
    if (!this.ready || (this.snapshot().dirty && this.changedByUser) || this.pending) return false
    const before = this.text()
    try {
      const raw = await this.store.getItem(this.key), position = parsePosition(raw)
      if (this.text() !== before || this.pending) return false
      this.position = position; this.clamp(); this.saved = this.text(); this.emit(); return true
    } catch { return false }
  }
  // Called only after the reader explicitly confirms replacing the unreadable record.
  replaceWithCurrent(): Promise<boolean> {
    if (this.loading || !this.count) return Promise.resolve(false)
    this.ready = true; this.saved = null
    return this.flush()
  }
  flush(): Promise<boolean> {
    if (!this.ready || !this.count) return Promise.resolve(false)
    if (this.pending) return this.pending
    if (!this.snapshot().dirty) return Promise.resolve(true)
    this.saving = true; this.error = ''; this.emit()
    this.pending = queueProgressStorage(this.store, this.key, async () => {
      try {
        while (this.text() !== this.saved) {
          const text = this.text()
          await this.store.setItem(this.key, text)
          this.saved = text
        }
        this.saving = false; this.emit()
        return true
      } catch {
        this.saving = false; this.error = '阅读进度保存失败，当前位置仍保留在页面中。请重试保存。'; this.emit()
        return false
      }
    }).finally(() => { this.pending = null })
    return this.pending
  }
}
