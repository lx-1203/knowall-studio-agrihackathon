import { queueMaterialStorage } from './materialStorageQueue.js'
import type { ProgressStorage } from './materialProgress'
interface OriginalPosition { page: number; zoom: number }
export interface OriginalProgressState extends OriginalPosition { key: string; renderedPage: number; ready: boolean; saving: boolean; dirty: boolean; error: string }
export const originalProgressKey = (accountId?: string, documentId?: string) => accountId && documentId ? `material-original:${accountId}:${documentId}` : null
const validPage = (page: unknown): page is number => typeof page === 'number' && Number.isSafeInteger(page) && page >= 1
const validZoom = (zoom: unknown): zoom is number => typeof zoom === 'number' && [1, 1.5, 2].includes(zoom)
function parsePosition(raw: string | null): OriginalPosition {
  if (raw === null) return { page: 1, zoom: 1 }
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || !('page' in value) || !('zoom' in value) || !validPage(value.page) || !validZoom(value.zoom)) throw Error('Invalid original position')
  return { page: value.page, zoom: value.zoom }
}

// Requested pages remain readable during storage faults; only successfully displayed pages persist.
export class OriginalProgressSession {
  readonly key: string
  private store: ProgressStorage
  private position: OriginalPosition = { page: 1, zoom: 1 }
  private saved: string | null = null
  private count = 0
  private renderedPage = 0
  private confirmed: OriginalPosition | null = null
  private ready = false
  private saving = false
  private error = ''
  private edited = { page: false, zoom: false }
  private changedByUser = false
  private loading: Promise<boolean> | null = null
  private pending: Promise<boolean> | null = null
  private listeners = new Set<(state: OriginalProgressState) => void>()
  constructor(store: ProgressStorage, key: string) { this.store = store; this.key = key }
  private text() { return this.confirmed ? JSON.stringify(this.confirmed) : null }
  private displayed() { return this.count > 0 && this.renderedPage === this.position.page }
  snapshot(): OriginalProgressState { return { key: this.key, ...this.position, renderedPage: this.renderedPage, ready: this.ready, saving: this.saving, dirty: this.ready && this.count > 0 && this.confirmed !== null && this.text() !== this.saved, error: this.error } }
  subscribe(listener: (state: OriginalProgressState) => void) { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private emit() { const state = this.snapshot(); this.listeners.forEach(listener => listener(state)) }
  private setPosition(position: OriginalPosition) {
    if (this.count) position.page = Math.min(position.page, this.count)
    if (position.page !== this.position.page) this.renderedPage = 0
    this.position = position
  }
  activate(count: number) {
    if (!Number.isSafeInteger(count) || count <= 0 || count === this.count) return
    const previous = this.position.page
    this.count = count; this.setPosition({ ...this.position })
    if (this.position.page !== previous) this.confirmed = null
    this.emit()
  }
  load(): Promise<boolean> {
    if (this.ready) return Promise.resolve(true)
    if (this.loading) return this.loading
    this.error = ''; this.emit()
    this.loading = queueMaterialStorage(this.store, this.key, async () => {
      try {
        const raw = await this.store.getItem(this.key), previous = parsePosition(raw)
        this.saved = raw === null ? null : JSON.stringify(previous)
        const requestedPage = this.position.page
        this.setPosition({ page: this.edited.page ? this.position.page : previous.page, zoom: this.edited.zoom ? this.position.zoom : previous.zoom })
        if (this.position.page !== requestedPage) this.confirmed = null
        else if (this.displayed()) this.confirmed = { ...this.position }
        this.ready = true; this.emit(); return true
      } catch {
        this.error = '进度读取失败，本次未覆盖已有进度'; this.emit(); return false
      }
    }).finally(() => { this.loading = null })
    return this.loading
  }
  update(change: Partial<OriginalPosition>) {
    if (validPage(change.page) || validZoom(change.zoom)) this.changedByUser = true
    const next = { ...this.position }
    if (validPage(change.page)) { next.page = change.page; if (!this.ready) this.edited.page = true }
    if (validZoom(change.zoom)) { next.zoom = change.zoom; if (!this.ready) this.edited.zoom = true }
    this.setPosition(next)
    if (this.displayed()) this.confirmed = { ...this.position }
    if (this.ready) this.error = ''; this.emit()
  }
  markRendered(page: number) { if (page !== this.position.page) return; this.renderedPage = page; this.confirmed = { ...this.position }; this.emit() }
  async reloadSaved(): Promise<boolean> {
    if (!this.ready || this.pending || (this.changedByUser && (this.snapshot().dirty || !this.displayed()))) return false
    const before = JSON.stringify(this.position)
    try {
      const raw = await this.store.getItem(this.key), position = parsePosition(raw)
      if (this.pending || JSON.stringify(this.position) !== before) return false
      this.setPosition(position); this.saved = JSON.stringify(position)
      this.confirmed = this.displayed() ? {...this.position} : null
      this.emit(); return true
    } catch { return false }
  }
  replaceWithCurrent(): Promise<boolean> {
    if (this.loading || !this.displayed()) return Promise.resolve(false)
    this.ready = true; this.saved = null; this.confirmed = { ...this.position }; return this.flush()
  }
  flush(): Promise<boolean> {
    if (!this.ready || !this.count) return Promise.resolve(false)
    if (this.pending) return this.pending
    if (!this.confirmed) return Promise.resolve(false)
    if (!this.snapshot().dirty) return Promise.resolve(true)
    this.saving = true; this.error = ''; this.emit()
    this.pending = queueMaterialStorage(this.store, this.key, async () => {
      try {
        while (this.confirmed && this.text() !== this.saved) {
          const text = this.text()!; await this.store.setItem(this.key, text); this.saved = text
        }
        this.saving = false; this.emit(); return true
      } catch {
        this.saving = false; this.error = '原件进度保存失败，请检查设备存储'; this.emit(); return false
      }
    }).finally(() => { this.pending = null })
    return this.pending
  }
}
