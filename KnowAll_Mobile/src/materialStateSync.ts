import { queueMaterialStorage } from './materialStorageQueue.js'
export type StateKind = 'note' | 'text_progress' | 'pdf_progress' | 'image_progress'
export type PdfStatePayload = { page_number: number; zoom: number; zoom_basis: 'fit_width' }
export type StatePayload = { text: string } | { chunk_index: number; chunk_id: string | null } | PdfStatePayload
export interface RemoteState {
  version: 1; account_id: string; document_id: string; kind: StateKind; revision: number
  payload: StatePayload | null; source_version: string | null; current_source_version: string; stale: boolean
  current_page_count?: number | null
}
export interface StateWrite { account_id: string; base_revision: number; operation_id: string; payload: StatePayload; source_version: string | null }
type Store = { getItem(key: string): Promise<string | null>; setItem(key: string, value: string): Promise<void> }
interface Pending { id: string; revision: number; payload: StatePayload; source: string | null; reanchored?: true }
interface Meta { version: 1; account: string; document: string; kind: StateKind; base: RemoteState | null; pending: Pending | null; conflict: RemoteState | null }
export interface SyncResult { status: 'synced' | 'downloaded' | 'conflict' | 'offline' | 'storage_error' | 'pending'; remote?: RemoteState }
interface Options {
  storage: Store; key: string; accountId: string; documentId: string; kind: StateKind
  decode(raw: string): StatePayload; encode(value: StatePayload, previous: string | null): string
  api: { get(): Promise<RemoteState>; put(request: StateWrite): Promise<RemoteState> }
  canApply?: () => boolean
  canUpload?: (payload: StatePayload) => boolean
  // Explicit original reanchoring needs proof from the actual reader, not old stored metadata.
  canResolveLocal?: (remote: RemoteState, payload: StatePayload) => boolean
  localSourceVersion?: (raw: string) => string | null
}
const newId = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
function validPayload(value: unknown, kind: StateKind): value is StatePayload {
  if (!value || typeof value !== 'object') return false
  const p = value as Record<string, unknown>
  if (kind === 'pdf_progress' || kind === 'image_progress') return Number.isSafeInteger(p.page_number) &&
    (kind === 'image_progress' ? p.page_number === 1 : Number(p.page_number) >= 1) && typeof p.zoom === 'number' && Number.isFinite(p.zoom) && p.zoom >= .5 && p.zoom <= 3 && p.zoom_basis === 'fit_width'
  return kind === 'note' ? typeof p.text === 'string' && p.text.length <= 100000
    : Number.isSafeInteger(p.chunk_index) && Number(p.chunk_index) >= 0 && Number(p.chunk_index) <= 1000000 && (p.chunk_id === null || typeof p.chunk_id === 'string')
}
export class SyncSlot {
  readonly backupKey: string
  readonly metaKey: string
  private running: Promise<SyncResult> | null = null
  private resolution: Promise<SyncResult> | null = null
  private o: Options
  constructor(o: Options) {
    this.o = o
    this.metaKey = `material-sync:1:${encodeURIComponent(o.accountId)}:${encodeURIComponent(o.documentId)}:${o.kind}`
    this.backupKey = this.metaKey + ':legacy-backup'
  }
  private same(a: StatePayload | null, b: StatePayload | null) {
    if (a === null || b === null) return a === b
    if ('text' in a && 'text' in b) return a.text === b.text
    if ('chunk_index' in a && 'chunk_index' in b) return a.chunk_index === b.chunk_index && (!a.chunk_id || !b.chunk_id || a.chunk_id === b.chunk_id)
    if ('page_number' in a && 'page_number' in b) return a.page_number === b.page_number && a.zoom === b.zoom && a.zoom_basis === b.zoom_basis
    return false
  }
  private remote(value: RemoteState): RemoteState {
    if (value.version !== 1 || value.account_id !== this.o.accountId || value.document_id !== this.o.documentId || value.kind !== this.o.kind ||
      !Number.isSafeInteger(value.revision) || value.revision < 0 || typeof value.current_source_version !== 'string' || typeof value.stale !== 'boolean' ||
      (value.payload !== null && !validPayload(value.payload, this.o.kind))) throw Error('Invalid sync scope/response')
    return value
  }
  private async meta(): Promise<Meta> {
    const raw = await this.o.storage.getItem(this.metaKey)
    if (raw === null) return { version: 1, account: this.o.accountId, document: this.o.documentId, kind: this.o.kind, base: null, pending: null, conflict: null }
    const m = JSON.parse(raw) as Meta
    if (!m || m.version !== 1 || m.account !== this.o.accountId || m.document !== this.o.documentId || m.kind !== this.o.kind ||
      !('base' in m) || !('pending' in m) || !('conflict' in m) ||
      (m.pending && (!validPayload(m.pending.payload, this.o.kind) || !Number.isSafeInteger(m.pending.revision) || m.pending.revision < 0 || typeof m.pending.id !== 'string' ||
        ('reanchored' in m.pending && m.pending.reanchored !== true) ||
        (m.pending.source!==null && (typeof m.pending.source!=='string' || !m.pending.source))))) throw Error('Corrupt sync journal')
    if (m.base) this.remote(m.base)
    if (m.conflict) this.remote(m.conflict)
    return m
  }
  private writeMeta(m: Meta) { return this.o.storage.setItem(this.metaKey, JSON.stringify(m)) }
  private async backup(raw: string | null) {
    if (raw !== null && await this.o.storage.getItem(this.backupKey) === null) await this.o.storage.setItem(this.backupKey, raw)
  }
  // Only local IO. Network waits must never block saving the next local edit.
  async saveRaw(raw: string) {
    return queueMaterialStorage(this.o.storage, this.o.key, async () => {
      const old = await this.o.storage.getItem(this.o.key)
      await this.backup(old)
      await this.o.storage.setItem(this.o.key, raw)
      // A failed journal write leaves the complete edit at its original local key.
      const m = await this.meta(), payload = this.o.decode(raw)
      if (!validPayload(payload, this.o.kind)) throw Error('Invalid local state')
      m.pending = { id: newId(), revision: m.base?.revision ?? 0, payload, source: this.o.localSourceVersion?.(raw) ?? m.base?.current_source_version ?? null }
      await this.writeMeta(m)
    })
  }
  sync(): Promise<SyncResult> {
    if (this.resolution) return this.resolution
    if (this.running) return this.running
    this.running = this.exchange().finally(() => { this.running = null })
    return this.running
  }
  private async exchange(): Promise<SyncResult> {
    let raw: string | null, m: Meta, local: StatePayload | null, localSource: string | null = null
    try {
      [raw, m] = await queueMaterialStorage(this.o.storage, this.o.key, async () => {
        const raw = await this.o.storage.getItem(this.o.key); await this.backup(raw)
        return [raw, await this.meta()] as const
      })
      local = raw === null ? null : this.o.decode(raw)
      if (local !== null && !validPayload(local, this.o.kind)) throw Error('Invalid local state')
      localSource = raw===null ? null : this.o.localSourceVersion?.(raw) ?? null
    } catch { return { status: 'storage_error' } }
    let remote: RemoteState
    try { remote = this.remote(await this.o.api.get()) } catch { return { status: 'offline' } }
    const explicitOriginalReanchor = (this.o.kind === 'pdf_progress' || this.o.kind === 'image_progress') && m.pending?.reanchored === true &&
      m.pending.source === remote.current_source_version && local !== null && this.o.canResolveLocal?.(remote, local) === true
    if (remote.stale && !explicitOriginalReanchor) return this.conflict(raw, m, remote)
    if (m.conflict) return this.conflict(raw, m, remote)
    const boundSource = m.pending?.source ?? (m.base && this.same(local,m.base.payload) ? m.base.current_source_version : localSource)
    if ((this.o.kind==='pdf_progress' || this.o.kind==='image_progress') && boundSource && boundSource!==remote.current_source_version) return this.conflict(raw,m,remote)
    if (this.same(local, remote.payload) && !remote.stale) return this.ack(raw, m.pending, remote)
    if (local === null || (m.base && this.same(local, m.base.payload) && !m.pending)) return this.pull(raw, remote)
    if ((m.base && remote.revision !== m.base.revision) || (!m.base && remote.revision > 0)) return this.conflict(raw, m, remote)
    if (local && this.o.canUpload?.(local) === false) return { status: 'pending' }
    const pending = m.pending ?? { id: newId(), revision: remote.revision, payload: local!, source: localSource ?? remote.current_source_version }
    // Before the first GET, an offline edit cannot know the source fingerprint.
    if (pending.source === null) pending.source = remote.current_source_version
    try {
      const recorded = await queueMaterialStorage(this.o.storage, this.o.key, async () => {
        if (await this.o.storage.getItem(this.o.key) !== raw) return false
        const current = await this.meta()
        if (current.pending && m.pending && current.pending.id !== m.pending.id) return false
        current.pending = pending; await this.writeMeta(current); return true
      })
      if (!recorded) return { status: 'pending' }
    } catch { return { status: 'storage_error' } }
    try {
      remote = this.remote(await this.o.api.put({ account_id: this.o.accountId, base_revision: pending.revision,
        operation_id: pending.id, payload: pending.payload, source_version: pending.source }))
      if (!this.same(pending.payload, remote.payload) || remote.stale) return this.conflict(raw, m, remote)
      return this.ack(raw, pending, remote)
    } catch (reason) {
      const current = (reason as { current?: RemoteState }).current
      if (current) { try { return this.conflict(raw, m, this.remote(current)) } catch {} }
      return { status: 'offline' }
    }
  }
  private async ack(raw: string | null, pending: Pending | null, remote: RemoteState): Promise<SyncResult> {
    try { return await queueMaterialStorage(this.o.storage, this.o.key, async () => {
      const m = await this.meta(), currentRaw = await this.o.storage.getItem(this.o.key)
      if (currentRaw !== raw || (m.pending && pending && m.pending.id !== pending.id)) {
        // Rebase a later edit only onto this client's own confirmed write, never a foreign head.
        if (pending && this.same(pending.payload, remote.payload) && m.pending?.revision === pending.revision) {
          m.base = remote; m.pending.revision = remote.revision; m.pending.source = remote.current_source_version; await this.writeMeta(m)
        }
        return { status: 'pending' }
      }
      m.base = remote; m.pending = null; m.conflict = null; await this.writeMeta(m)
      return { status: 'synced', remote }
    }) } catch { return { status: 'storage_error' } }
  }
  private async pull(raw: string | null, remote: RemoteState): Promise<SyncResult> {
    try { return await queueMaterialStorage(this.o.storage, this.o.key, async () => {
      if (await this.o.storage.getItem(this.o.key) !== raw || this.o.canApply?.() === false) return { status: 'pending' }
      await this.backup(raw)
      const m = await this.meta()
      if (m.pending) return { status: 'pending' }
      if (remote.payload !== null) await this.o.storage.setItem(this.o.key, this.o.encode(remote.payload, raw))
      m.base = remote; m.pending = null; m.conflict = null; await this.writeMeta(m)
      return { status: remote.payload === null ? 'synced' : 'downloaded', remote }
    }) } catch { return { status: 'storage_error' } }
  }
  private async conflict(raw: string | null, old: Meta, remote: RemoteState): Promise<SyncResult> {
    try { return await queueMaterialStorage(this.o.storage, this.o.key, async () => {
      if (await this.o.storage.getItem(this.o.key) !== raw) return { status: 'pending' }
      const m = await this.meta()
      if (!m.pending && raw !== null) m.pending = { id: newId(), revision: m.base?.revision ?? 0, payload: this.o.decode(raw), source: this.o.localSourceVersion?.(raw) ?? m.base?.current_source_version ?? remote.current_source_version }
      m.conflict = remote
      const archiveKey = this.metaKey + ':conflict:' + (m.pending?.id ?? 'remote') + ':' + remote.revision + (this.o.kind==='pdf_progress' || this.o.kind==='image_progress'?':'+encodeURIComponent(remote.current_source_version):'')
      if (await this.o.storage.getItem(archiveKey) === null) await this.o.storage.setItem(archiveKey, JSON.stringify({ local_raw: raw, remote }))
      await this.writeMeta(m)
      return { status: 'conflict', remote }
    }) } catch { return { status: 'storage_error' } }
  }
  resolve(choice: 'local' | 'remote'): Promise<SyncResult> {
    if (this.resolution) return Promise.resolve({ status: 'pending' })
    this.resolution = this.resolveChoice(choice).finally(() => { this.resolution = null })
    return this.resolution
  }
  private async resolveChoice(choice: 'local' | 'remote'): Promise<SyncResult> {
    // Capture the choice's local side before network waits, including its operation identity.
    let raw: string | null, old: Meta, local: StatePayload | null
    try {
      [raw, old] = await queueMaterialStorage(this.o.storage, this.o.key, async () =>
        [await this.o.storage.getItem(this.o.key), await this.meta()] as const)
      local = raw === null ? null : this.o.decode(raw)
      if (local !== null && !validPayload(local, this.o.kind)) throw Error('Invalid local state')
      if (raw !== null) this.o.localSourceVersion?.(raw)
    } catch { return { status: 'storage_error' } }
    if (this.running) await this.running
    let remote: RemoteState
    try { remote = this.remote(await this.o.api.get()) } catch { return { status: 'offline' } }
    if ((choice === 'remote' && (remote.payload === null || remote.stale)) ||
      (choice === 'local' && (local === null || this.o.canUpload?.(local) === false ||
        ((this.o.kind === 'pdf_progress' || this.o.kind === 'image_progress') && this.o.canResolveLocal?.(remote, local) !== true)))) {
      return this.conflict(raw, old, remote)
    }
    try {
      const applied = await queueMaterialStorage(this.o.storage, this.o.key, async () => {
        const m = await this.meta()
        if (await this.o.storage.getItem(this.o.key) !== raw || m.pending?.id !== old.pending?.id || this.o.canApply?.() === false) return false
        // Reader state can change while waiting for the storage queue as well.
        if (choice === 'local' && (this.o.canUpload?.(local!) === false ||
          ((this.o.kind === 'pdf_progress' || this.o.kind === 'image_progress') && this.o.canResolveLocal?.(remote, local!) !== true))) return false
        await this.backup(raw)
        await this.o.storage.setItem(this.metaKey + ':conflict:' + newId(), JSON.stringify({ local_raw: raw, remote }))
        if (await this.o.storage.getItem(this.o.key) !== raw || this.o.canApply?.() === false ||
          (choice === 'local' && (this.o.canUpload?.(local!) === false ||
            ((this.o.kind === 'pdf_progress' || this.o.kind === 'image_progress') && this.o.canResolveLocal?.(remote, local!) !== true)))) return false
        m.base = remote; m.conflict = null; m.pending = null
        if (choice === 'remote') {
          await this.o.storage.setItem(this.o.key, this.o.encode(remote.payload!, raw))
        } else m.pending = { id: newId(), revision: remote.revision, payload: local!, source: remote.current_source_version,
          ...(this.o.kind === 'pdf_progress' || this.o.kind === 'image_progress' ? { reanchored: true as const } : {}) }
        await this.writeMeta(m)
        return true
      })
      if (!applied) return { status: 'pending' }
      return choice === 'remote' ? { status: 'downloaded', remote } : this.exchange()
    } catch { return { status: 'storage_error' } }
  }
}
