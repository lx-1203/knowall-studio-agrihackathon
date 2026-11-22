export interface NoteStorage { getItem: (key: string) => Promise<string | null>; setItem: (key: string, text: string) => Promise<void> }
export interface MaterialNoteState { key: string; text: string; ready: boolean; saving: boolean; dirty: boolean; persisted: boolean; error: string }
export const materialNoteKey = (accountId?: string, documentId?: string) => accountId && documentId ? `material-note:${accountId}:${documentId}` : null

/** One session owns one storage key. Writes drain snapshots in order, including edits made during a save. */
export class MaterialNoteSession {
  readonly key: string
  private store: NoteStorage
  private text = ''
  private saved = ''
  private ready = false
  private saving = false
  private persisted = false
  private error = ''
  private loadRevision = 0
  private pending: Promise<boolean> | null = null
  private listeners = new Set<(state: MaterialNoteState) => void>()

  constructor(store: NoteStorage, key: string) { this.store = store; this.key = key }
  snapshot(): MaterialNoteState { return { key: this.key, text: this.text, ready: this.ready, saving: this.saving, dirty: this.ready && this.text !== this.saved, persisted: this.persisted, error: this.error } }
  subscribe(listener: (state: MaterialNoteState) => void) { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private emit() { const state = this.snapshot(); this.listeners.forEach(listener => listener(state)) }
  async load(): Promise<boolean> {
    if (this.snapshot().dirty || this.saving) return false
    const revision = ++this.loadRevision
    this.ready = false; this.error = ''; this.emit()
    try {
      const text = await this.store.getItem(this.key)
      if (revision !== this.loadRevision) return false
      this.text = text ?? ''; this.saved = this.text; this.ready = true; this.persisted = text !== null; this.emit()
      return true
    } catch {
      if (revision === this.loadRevision) { this.error = '笔记读取失败，原笔记未被覆盖。请重试读取。'; this.emit() }
      return false
    }
  }
  update(text: string) { if (!this.ready) return; this.text = text; this.error = ''; this.emit() }
  flush(): Promise<boolean> {
    if (!this.ready) return Promise.resolve(false)
    if (this.pending) return this.pending
    if (!this.snapshot().dirty) return Promise.resolve(true)
    this.pending = this.writeSnapshots().finally(() => { this.pending = null })
    return this.pending
  }
  private async writeSnapshots(): Promise<boolean> {
    this.saving = true; this.error = ''; this.emit()
    try {
      while (this.text !== this.saved) {
        const snapshot = this.text
        await this.store.setItem(this.key, snapshot)
        this.saved = snapshot; this.persisted = true
      }
      this.saving = false; this.emit()
      return true
    } catch {
      this.saving = false; this.error = '保存失败，笔记仍在编辑器中。请检查设备存储后重试。'; this.emit()
      return false
    }
  }
}
