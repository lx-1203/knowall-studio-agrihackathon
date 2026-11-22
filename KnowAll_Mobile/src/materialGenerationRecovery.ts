import { queueMaterialStorage } from './materialStorageQueue.js'
import { generationReceipt, generationSettings, type GenerationSettings } from './materialGeneration.ts'

export interface RecoveryScope { accountId: string; documentId: string }
export interface RecoveryRecord extends RecoveryScope {
  version: 1; id: string; createdAt: number; sourceHash: string; beforeId: string | null; settings: GenerationSettings
}
interface RecoveryStore { getItem: (key: string) => Promise<string | null>; setItem: (key: string, value: string) => Promise<void>; removeItem: (key: string) => Promise<void> }
const nonempty = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0
export function recoveryKey(scope: RecoveryScope) {
  if (!nonempty(scope.accountId) || !nonempty(scope.documentId)) throw Error('Missing recovery scope')
  return `material-generation:${encodeURIComponent(scope.accountId)}:${encodeURIComponent(scope.documentId)}`
}
function parse(raw: string | null, scope: RecoveryScope): RecoveryRecord | null {
  if (raw === null) return null
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Invalid recovery record')
  const r = value as Record<string, unknown>
  if (r.version !== 1 || !nonempty(r.id) || r.accountId !== scope.accountId || r.documentId !== scope.documentId ||
    !nonempty(r.sourceHash) || typeof r.createdAt !== 'number' || !Number.isSafeInteger(r.createdAt) || r.createdAt <= 0 ||
    (r.beforeId !== null && !nonempty(r.beforeId)) || !r.settings || typeof r.settings !== 'object') throw Error('Invalid recovery scope')
  const config = r.settings as GenerationSettings
  if (typeof config.question_count !== 'number' || typeof config.card_count !== 'number' || !nonempty(config.model) ||
    !Array.isArray(config.question_types) || config.question_types.some(type => typeof type !== 'string')) throw Error('Invalid recovery config')
  const checked = generationSettings({ questions: String(config.question_count), cards: String(config.card_count), model: config.model, types: config.question_types })
  if (!checked.value || JSON.stringify(checked.value) !== JSON.stringify({ question_count: config.question_count, card_count: config.card_count, model: config.model, question_types: config.question_types })) throw Error('Invalid recovery config')
  return { version: 1, id: r.id, accountId: scope.accountId, documentId: scope.documentId, createdAt: r.createdAt, sourceHash: r.sourceHash, beforeId: r.beforeId as string | null, settings: checked.value }
}
export function loadRecovery(store: RecoveryStore, scope: RecoveryScope) {
  const key = recoveryKey(scope)
  return queueMaterialStorage(store, key, async () => {
    const raw = await store.getItem(key)
    try { return parse(raw, scope) }
    catch { throw new CorruptRecoveryError(raw!) }
  })
}
export class CorruptRecoveryError extends Error {
  readonly raw: string
  constructor(raw: string) { super('Corrupt recovery record'); this.name = 'CorruptRecoveryError'; this.raw = raw }
}
export function discardCorruptRecovery(store: RecoveryStore, scope: RecoveryScope, expected: string) {
  const key = recoveryKey(scope)
  return queueMaterialStorage(store, key, async () => {
    const raw = await store.getItem(key)
    if (raw !== expected) return false
    // Never remove a valid journal, even if a caller supplies its exact bytes.
    try { parse(raw, scope); return false } catch { /* Confirmed corrupt bytes only. */ }
    await store.removeItem(key)
    return true
  })
}
export function startRecovery(store: RecoveryStore, scope: RecoveryScope, settings: GenerationSettings, sourceHash: string, beforeId?: string): Promise<RecoveryRecord> {
  const key = recoveryKey(scope)
  return queueMaterialStorage(store, key, async () => {
    if (parse(await store.getItem(key), scope)) throw Error('Submission already pending')
    const record: RecoveryRecord = { ...scope, version: 1, id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`, createdAt: Date.now(), sourceHash, beforeId: beforeId ?? null, settings }
    const validated = parse(JSON.stringify(record), scope)!
    await store.setItem(key, JSON.stringify(validated))
    return validated
  })
}
export function clearRecovery(store: RecoveryStore, scope: RecoveryScope, id: string) {
  const key = recoveryKey(scope)
  return queueMaterialStorage(store, key, async () => {
    const record = parse(await store.getItem(key), scope)
    if (!record) return true
    if (record.id !== id) return false
    await store.removeItem(key); return true
  })
}
export function confirmRecovery(value: unknown, record: RecoveryRecord) {
  const receipt = generationReceipt(value, record.documentId, record.settings)
  if (receipt.id === record.beforeId || !value || typeof value !== 'object' || !('source_hash' in value) || value.source_hash !== record.sourceHash) throw Error('Recovery does not match submission')
  return receipt
}
