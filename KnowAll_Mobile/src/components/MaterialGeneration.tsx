import { useEffect, useRef, useState } from 'react'
import { isAxiosError } from 'axios'
import { StyleSheet, View } from 'react-native'
import { ActivityIndicator, Button, Modal, Portal, Surface, Text, TextInput, useTheme } from 'react-native-paper'
import { generateMaterialLearningPack, getMaterialGenerationPreview, getMaterialGenerationResult } from '../api'
import { generationSettings, generationTypes, normalizeGenerationPreview, type GenerationDraft, type GenerationPreview } from '../materialGeneration'
import { normalizeLearningPack, type MaterialPack } from '../materialLearning'
import { MaterialChoice } from './MaterialAnswerInput'
import { clearRecovery, confirmRecovery, loadRecovery, startRecovery, CorruptRecoveryError, discardCorruptRecovery, type RecoveryRecord } from '../materialGenerationRecovery'
import { storage } from '../services/storage'
import { useAuthStore } from '../stores'

const defaults = (): GenerationDraft => ({ questions: '10', cards: '10', model: 'deepseek-chat', types: ['single_choice'] })
export default function MaterialGeneration({ active, documentId, title, previousId, onCancel, onResume, onGenerated }: {
  active: boolean; documentId: string; title: string; previousId?: string; onCancel: () => void; onResume: () => void; onGenerated: (pack: MaterialPack, message: string) => void
}) {
  const theme = useTheme()
  const [draft, setDraft] = useState(defaults)
  const [preview, setPreview] = useState<{ key: string; data: GenerationPreview } | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewAttempt, setPreviewAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const accountId = useAuthStore(state => state.user?.id || '')
  const [pending, setPending] = useState<RecoveryRecord | null>(null)
  const [localReady, setLocalReady] = useState(false)
  const [localError, setLocalError] = useState('')
  const [corruptRaw, setCorruptRaw] = useState<string | null>(null)
  const [discardOpen, setDiscardOpen] = useState(false)
  const [retryOpen, setRetryOpen] = useState(false)
  const uncertain = !!pending
  const lock = useRef(false)
  const revision = useRef(0)
  const settings = generationSettings(draft)
  const signature = documentId + JSON.stringify(settings.value)
  const readLocal = async () => {
    const current = revision.current
    setLocalReady(false); setLocalError(''); setCorruptRaw(null); setDiscardOpen(false)
    try {
      const record = await loadRecovery(storage, { accountId, documentId })
      if (current !== revision.current) return
      setPending(record); setLocalReady(true)
      if (record) setDraft({ questions: String(record.settings.question_count), cards: String(record.settings.card_count), model: record.settings.model, types: record.settings.question_types })
    } catch (reason) { if (current === revision.current) {
      setLocalError('生成记录读取失败，暂不能发起新生成。原资料仍可阅读。')
      if (reason instanceof CorruptRecoveryError) setCorruptRaw(reason.raw)
    } }
  }
  useEffect(() => {
    ++revision.current
    setDraft(defaults()); setError(''); setPending(null); setLocalReady(false); setBusy(false); setRetryOpen(false); setCorruptRaw(null); setDiscardOpen(false); lock.current = false
    void readLocal()
    return () => { ++revision.current }
  }, [documentId, accountId])
  useEffect(() => {
    setPreview(null); setPreviewError(''); setPreviewLoading(false)
    if (!active || !settings.value) return
    const controller = new AbortController()
    const snapshot = settings.value
    setPreviewLoading(true)
    const timer = setTimeout(() => {
      getMaterialGenerationPreview(documentId, snapshot, controller.signal)
        .then(value => { if (!controller.signal.aborted) setPreview({ key: signature, data: normalizeGenerationPreview(value, snapshot) }) })
        .catch(reason => { if (!controller.signal.aborted) setPreviewError(isAxiosError(reason) && reason.response?.status === 400 ? '资料还没有可生成的文字，请先完成正文解析。' : '章节分配加载失败，请重试。') })
        .finally(() => { if (!controller.signal.aborted) setPreviewLoading(false) })
    }, 300)
    return () => { clearTimeout(timer); controller.abort() }
  }, [active, documentId, signature, previewAttempt])
  const ready = !!settings.value && preview?.key === signature && !previewLoading && !previewError
  const apply = async (value: unknown, record: RecoveryRecord, current: number, direct = false) => {
    // Leaving the page retains the journal until that account checks its result.
    if (current !== revision.current) return
    // A direct POST response may legitimately reuse the previous cached pack id.
    const receipt = confirmRecovery(value, direct ? { ...record, beforeId: null } : record)
    const cleared = await clearRecovery(storage, record, record.id)
    if (current !== revision.current) return
    if (!cleared) { await readLocal(); throw Error('Recovery changed') }
    setPending(null); setError('')
    if (receipt.status === 'failed') { setError(receipt.message); return }
    const pack = normalizeLearningPack(value)
    if (!pack) throw new Error('Invalid pack')
    onGenerated(pack, receipt.message)
  }
  const generate = async () => {
    if (!ready || !settings.value || !preview || !localReady || uncertain || lock.current) return
    lock.current = true
    const current = revision.current
    setBusy(true); setError('')
    let record: RecoveryRecord
    try {
      record = await startRecovery(storage, { accountId, documentId }, settings.value, preview.data.source_hash, previousId)
    } catch {
      if (current === revision.current) {
        await readLocal()
        if (current !== revision.current) return
        setError('提交记录未能保存，尚未发送生成请求。请检查设备存储后重试。')
        lock.current = false; setBusy(false)
      }
      return
    }
    if (current === revision.current) setPending(record)
    try {
      if (useAuthStore.getState().user?.id !== accountId) { await clearRecovery(storage, record, record.id); return }
      const result = await generateMaterialLearningPack(documentId, record.settings)
      await apply(result, record, current, true)
    } catch (reason) {
      if (current !== revision.current) return
      const status = isAxiosError(reason) ? reason.response?.status : undefined
      if (status && [400, 401, 403, 404, 409, 413, 422].includes(status)) {
        try {
          if (await clearRecovery(storage, record, record.id)) {
            if (current !== revision.current) return
            setPending(null)
          } else await readLocal()
        } catch { if (current === revision.current) setError('请求未被接受，本机记录清理失败。请先检查或明确重新尝试。'); return }
        if (current !== revision.current) return
        setError(status === 409 ? '资料已变化，请重新预览后再整理。' : '生成请求未被接受，请检查资料解析、账号权限和模型配置。')
        setPreview(null); setPreviewAttempt(n => n + 1)
      } else {
        setError('生成结果尚未确认。请先检查已生成内容，避免重复请求。')
      }
    } finally { if (current === revision.current) { lock.current = false; setBusy(false) } }
  }
  const check = async () => {
    if (lock.current || !pending || !localReady) return
    lock.current = true
    const current = revision.current
    setBusy(true); setError('')
    try {
      const result = await getMaterialGenerationResult(documentId)
      if (current !== revision.current) return
      await apply(result, pending, current)
    } catch { if (current === revision.current) setError('暂时无法确认生成结果。请稍后再检查，原资料仍可阅读。') }
    finally { if (current === revision.current) { lock.current = false; setBusy(false) } }
  }
  const retry = async () => {
    if (!pending || lock.current) return
    const current = revision.current
    lock.current = true; setBusy(true); setError('')
    try {
      if (!await clearRecovery(storage, pending, pending.id)) { await readLocal(); return }
      if (current !== revision.current) return
      setPending(null); setRetryOpen(false); setPreview(null); setPreviewAttempt(n => n + 1)
    } catch { if (current === revision.current) setError('本机记录清理失败，已保留原记录。请稍后重试。') }
    finally { if (current === revision.current) { lock.current = false; setBusy(false) } }
  }
  const discardCorrupt = async () => {
    if (corruptRaw === null || lock.current) return
    const current = revision.current
    lock.current = true; setBusy(true); setError('')
    try {
      const cleared = await discardCorruptRecovery(storage, { accountId, documentId }, corruptRaw)
      if (current !== revision.current) return
      await readLocal()
      if (current !== revision.current) return
      setError(cleared ? '损坏的本机记录已清除。请先检查已有学习内容；需要重新生成时再点击生成。' : '生成记录已变化，未清除。请核对当前记录后再操作。')
    } catch { if (current === revision.current) setError('损坏记录清理失败，已保留原记录。请稍后重试。') }
    finally { if (current === revision.current) { lock.current = false; setBusy(false) } }
  }
  const recovery = <>
    {!!localError && <><Text accessibilityLiveRegion="polite" style={{ color: theme.colors.error }}>{localError}</Text><Button disabled={busy} onPress={() => { void readLocal() }}>重试读取生成记录</Button></>}
    {corruptRaw !== null && <Button disabled={busy} onPress={() => { setError(''); setDiscardOpen(true) }}>处理损坏的生成记录</Button>}
    <Portal><Modal visible={discardOpen} onDismiss={() => { if (!busy) setDiscardOpen(false) }} contentContainerStyle={[styles.confirm, { backgroundColor: theme.colors.surface }]}>
      <Text variant="titleLarge">清除损坏的本机记录？</Text>
      <Text>无法读取上次提交的配置。服务器可能仍在处理上次请求；清除本机记录不会取消服务器生成。请先检查已有学习内容，重新生成可能产生重复的模型费用。确认清除不会提交新请求。</Text>
      {!!error && <Text style={{ color: theme.colors.error }}>{error}</Text>}
      <Button loading={busy} disabled={busy} onPress={() => { void discardCorrupt() }}>确认清除损坏记录</Button>
      <Button disabled={busy} onPress={() => setDiscardOpen(false)}>保留损坏记录</Button>
    </Modal></Portal>
    {pending && <Surface elevation={0} style={[styles.budget, { backgroundColor: theme.colors.surfaceVariant }]}>
      <Text variant="titleMedium">上次生成结果待确认</Text><Text>已保留这份资料的生成配置。返回或重新打开不会自动重复生成；原资料可以继续阅读。</Text>
      <Text variant="bodySmall">习题 {pending.settings.question_count} · 知识卡 {pending.settings.card_count} · {pending.settings.model}</Text>
      <Button mode="outlined" disabled={busy || !localReady} onPress={check}>检查已生成内容</Button>
      {!active && <Button disabled={busy} onPress={onResume}>查看上次生成配置</Button>}
      <Button disabled={busy || !localReady} onPress={() => setRetryOpen(true)}>保留配置，重新尝试</Button>
    </Surface>}
    <Portal><Modal visible={retryOpen} onDismiss={() => { if (!busy) setRetryOpen(false) }} contentContainerStyle={[styles.confirm, { backgroundColor: theme.colors.surface }]}>
      <Text variant="titleLarge">重新尝试可能重复生成</Text><Text>服务器可能仍在处理上次请求。重新尝试可能产生重复的模型费用。确认后只清除本机恢复记录，保留配置；再次点击生成才会提交新请求。</Text>
      {!!error && <Text style={{ color: theme.colors.error }}>{error}</Text>}
      <Button loading={busy} disabled={busy} onPress={() => { void retry() }}>确认保留配置重试</Button><Button disabled={busy} onPress={() => setRetryOpen(false)}>继续检查结果</Button>
    </Modal></Portal>
  </>
  if (!active) return <View style={styles.stack}>{recovery}{!!error && <Text accessibilityLiveRegion="polite" style={{ color: theme.colors.error }}>{error}</Text>}</View>
  return <View style={styles.stack}>
    <Text variant="titleMedium">整理这份资料的学习内容</Text>
    <Text>{title}</Text>
    <Text variant="bodySmall">按章节整理讲解、练习与知识卡。点击生成会使用你已配置的 AI 模型，可能产生模型费用；直接阅读无需生成。</Text>
    {recovery}
    <TextInput mode="outlined" label="习题数量" accessibilityLabel="生成习题数量" keyboardType="number-pad" value={draft.questions} disabled={busy || uncertain} onChangeText={questions => setDraft(value => ({ ...value, questions }))} />
    <TextInput mode="outlined" label="知识卡数量" accessibilityLabel="生成知识卡数量" keyboardType="number-pad" value={draft.cards} disabled={busy || uncertain} onChangeText={cards => setDraft(value => ({ ...value, cards }))} />
    <TextInput mode="outlined" label="模型名称" accessibilityLabel="学习生成模型" autoCapitalize="none" autoCorrect={false} value={draft.model} disabled={busy || uncertain} onChangeText={model => setDraft(value => ({ ...value, model }))} />
    <Text variant="titleSmall">生成题型</Text>
    <Text variant="bodySmall">可选多种题型；知识卡数量不受题型选择限制。</Text>
    <View style={styles.typeGrid}>{generationTypes.map(([type, label]) => <View key={type} style={styles.typeCell}>
      <MaterialChoice label={label} name={`生成题型：${label}`} multiple checked={draft.types.includes(type)} disabled={busy || uncertain}
        onPress={() => setDraft(value => ({ ...value, types: value.types.includes(type) ? value.types.filter(t => t !== type) : [...value.types, type] }))} />
    </View>)}</View>
    {!!settings.error && <Text style={{ color: theme.colors.error }}>{settings.error}</Text>}
    {previewLoading && <View style={styles.stack}><ActivityIndicator /><Text>正在查看章节分配…</Text></View>}
    {!!previewError && <><Text style={{ color: theme.colors.error }}>{previewError}</Text><Button disabled={busy} onPress={() => setPreviewAttempt(n => n + 1)}>重新预览章节</Button></>}
    {ready && preview && <Surface elevation={0} style={[styles.budget, { backgroundColor: theme.colors.surfaceVariant }]}>
      <Text variant="titleMedium">{preview.data.sections.length} 个章节 · 查看分配</Text>
      {preview.data.sections.map(section => <View key={section.id} style={styles.stack}><Text>{section.title}</Text><Text variant="bodySmall">习题 {section.question_budget} · 知识卡 {section.card_budget}</Text></View>)}
    </Surface>}
    {!!error && <Text accessibilityLiveRegion="polite" style={{ color: theme.colors.error }}>{error}</Text>}
    {busy && <Text>正在处理，请稍候。返回阅读不会取消服务器生成；再次打开可刷新学习内容。</Text>}
    <Button mode="contained" contentStyle={styles.touch} loading={busy} disabled={busy || !ready || uncertain || !localReady} onPress={generate}>生成学习包</Button>
    <Button disabled={busy} onPress={onCancel}>返回学习工具</Button>
  </View>
}
const styles = StyleSheet.create({ stack: { gap: 12 }, typeGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, typeCell: { width: '48%' }, budget: { padding: 16, borderRadius: 12, gap: 12 }, touch: { minHeight: 48 }, confirm: { margin: 20, padding: 20, borderRadius: 16, gap: 16, maxHeight: '90%' } })
