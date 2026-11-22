import { useCallback, useEffect, useRef, useState } from 'react'
import { Pressable, ScrollView, View } from 'react-native'
import { ActivityIndicator, Button, Chip, Dialog, Divider, Portal, Text, TextInput, useTheme } from 'react-native-paper'
import { useFocusEffect, useRouter } from 'expo-router'
import { useAuthStore } from '../../src/stores'
import api from '../../src/api'
import type { DictationResult, HidePlan, RecitationPassage, RecitationRecord, RecitationStats } from '../../src/types'
import { CalmScreen, CalmPanel, ScreenNotice, calm } from '../../src/components/CalmScreen'
import { noReplay, recordToolActivity, screenError } from '../../src/services/mobileScreenActions'

export default function RecitationPage() {
  const theme = useTheme(), router = useRouter(), userId = useAuthStore(s => s.user?.id)
  const lock = useRef(false), revision = useRef(0), started = useRef(Date.now())
  const [passages, setPassages] = useState<RecitationPassage[]>([]), [due, setDue] = useState<RecitationRecord[]>([])
  const [stats, setStats] = useState<RecitationStats | null>(null)
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [dueError, setDueError] = useState('')
  const [dueOnly, setDueOnly] = useState(false), [active, setActive] = useState<RecitationPassage | null>(null), [mode, setMode] = useState<'hide' | 'dictation'>('hide')
  const [level, setLevel] = useState(1), [plan, setPlan] = useState<HidePlan | null>(null), [planLoading, setPlanLoading] = useState(false), [revealed, setRevealed] = useState<number[]>([]), [reference, setReference] = useState(false)
  const [drafts, setDrafts] = useState<Record<string, string>>({}), [result, setResult] = useState<DictationResult | null>(null), [rated, setRated] = useState(false), [recordId, setRecordId] = useState('')
  const [dialog, setDialog] = useState<'create' | 'delete' | null>(null), [deleteId, setDeleteId] = useState(''), [title, setTitle] = useState(''), [content, setContent] = useState(''), [formError, setFormError] = useState('')
  const load = useCallback(async () => {
    setLoading(true)
    const [p, d, s] = await Promise.allSettled([api.get('/recitation/passages', { params: { limit: 200 } }), api.get('/recitation/due', { params: { limit: 50 } }), api.get('/recitation/stats')])
    if (p.status === 'fulfilled' && Array.isArray(p.value.data?.passages)) { setPassages(p.value.data.passages); setError('') } else setError('背诵内容没有加载成功，请重试。')
    if (d.status === 'fulfilled' && Array.isArray(d.value.data?.due_items)) { setDue(d.value.data.due_items); setDueError('') } else setDueError('到期复习暂时无法读取。')
    setStats(s.status === 'fulfilled' ? s.value.data : null)
    setLoading(false)
  }, [])
  useFocusEffect(useCallback(() => { load(); return () => { revision.current++ } }, [load]))
  useEffect(() => { setDrafts({}); setActive(null); setResult(null) }, [userId])
  const hide = useCallback(async (p: RecitationPassage, value: number) => {
    const version = ++revision.current; setPlan(null); setPlanLoading(true); setRevealed([]); setError('')
    try {
      const data = (await api.post('/recitation/hide-plan', { content: p.content, level: value }, noReplay)).data
      if (!Array.isArray(data.segments)) throw new Error()
      if (version === revision.current) setPlan(data)
    } catch { if (version === revision.current) setError('遮字内容没有加载成功，原文仍保留，请重试。') }
    finally { if (version === revision.current) setPlanLoading(false) }
  }, [])
  useEffect(() => { if (active && mode === 'hide') hide(active, level) }, [active, mode, level, hide])
  const start = (p: RecitationPassage, value: 'hide' | 'dictation', reviewId = '') => {
    if (lock.current) return
    revision.current++; setActive(p); setMode(value); setResult(null); setRecordId(reviewId); setRated(false); setReference(false); setError(''); started.current = Date.now()
  }
  const check = async () => {
    if (lock.current || !active) return
    const input = drafts[active.id] || ''
    if (!input.trim()) { setError('先写下记得的内容，再检查。'); return }
    lock.current = true; setBusy(true); setError('')
    try {
      const data = (await api.post('/recitation/dictation/submit', { passage_id: active.id, user_input: input, time_spent_ms: Date.now() - started.current }, noReplay)).data
      if (!data.record_id || !Array.isArray(data.diffs) || typeof data.accuracy !== 'number') throw new Error()
      setResult(data); setRecordId(data.record_id); setRated(false)
      void recordToolActivity('recitation')
    } catch (e) { setError(screenError(e, '提交未确认，默写内容已保留。请核对后决定是否重试。')) }
    finally { lock.current = false; setBusy(false) }
  }
  const rate = async (rating: number) => {
    if (lock.current || !recordId || rated) return
    lock.current = true; setBusy(true); setError('')
    try {
      const data = (await api.post('/recitation/review', { record_id: recordId, rating }, noReplay)).data
      if (data.record_id !== recordId) throw new Error()
      setRated(true); setDue(prev => prev.filter(d => d.record_id !== recordId))
    } catch { setError('评分未确认，练习结果会保留。没有自动重复评分，请核对后重试。') }
    finally { lock.current = false; setBusy(false) }
  }
  const mutate = async () => {
    if (lock.current) return
    if (dialog === 'create' && (!title.trim() || !content.trim())) { setFormError('请填写标题和背诵内容。'); return }
    lock.current = true; setBusy(true); setFormError('')
    try {
      if (dialog === 'create') {
        const data = (await api.post('/recitation/passages', { title: title.trim(), content: content.trim(), category: 'custom' }, noReplay)).data
        if (!data.id) throw new Error()
      } else { const data = (await api.delete('/recitation/passages/' + encodeURIComponent(deleteId), noReplay)).data; if (data.deleted !== true) throw new Error(); setDrafts(prev => { const next = { ...prev }; delete next[deleteId]; return next }) }
      setDialog(null); await load()
    } catch (e) { setFormError(screenError(e, '保存没有确认，当前输入已保留，请核对后重试。')) }
    finally { lock.current = false; setBusy(false) }
  }
  const goBack = () => { if (busy) return; if (active) { revision.current++; setActive(null); setError(''); load() } else router.replace('/review') }
  const ratingButtons = <View style={calm.wrap}>{['再学一次', '有点难', '记住了', '很熟悉'].map((label, i) => <Button key={label} mode={i === 2 ? 'contained' : 'outlined'} style={{ width: '47%' }} contentStyle={{ minHeight: 48 }} disabled={busy || rated} onPress={() => rate(i + 1)}>{label}</Button>)}</View>
  return <CalmScreen title={active ? active.title : '背诵练习'} subtitle={active ? '一遍一遍，慢慢记牢。' : '收藏要记住的文字，分段回顾。'} onBack={goBack} backLabel={active ? '返回背诵列表' : '返回复习'} refreshing={loading} onRefresh={!active && !busy ? load : undefined}
    action={!active ? <Button mode="contained-tonal" onPress={() => { setTitle(''); setContent(''); setFormError(''); setDialog('create') }}>新建</Button> : undefined}>
    {active ? <>
      <View style={[calm.wrap, { marginBottom: 18 }]}><Chip selected={mode === 'hide'} disabled={busy} onPress={() => { setMode('hide'); setError('') }}>遮字背诵</Chip><Chip selected={mode === 'dictation'} disabled={busy} onPress={() => { setMode('dictation'); setError('') }}>默写检查</Chip></View>
      {mode === 'hide' ? <>
        <CalmPanel><Text variant="titleMedium">遮字等级</Text><View style={[calm.wrap, { marginTop: 14 }]}>{[1, 2, 3, 4, 5].map(l => <Chip key={l} selected={level === l} onPress={() => setLevel(l)}>等级 {l}</Chip>)}</View></CalmPanel>
        <CalmPanel>{planLoading ? <ActivityIndicator /> : plan ? <><Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>点击空白处，查看这一小段。</Text><View style={calm.wrap}>{plan.segments.map((s, i) => s.hidden ? <Pressable key={i} accessibilityRole="button" accessibilityLabel={(revealed.includes(i) ? '重新隐藏第' : '揭示第') + (i + 1) + '处'} onPress={() => setRevealed(prev => prev.includes(i) ? prev.filter(v => v !== i) : [...prev, i])} style={{ minHeight: 48, maxWidth: '100%', padding: 10, borderRadius: 8, backgroundColor: theme.colors.primaryContainer }}><Text style={{ color: theme.colors.onPrimaryContainer, lineHeight: 28 }}>{revealed.includes(i) ? s.text : '点击揭示 · ' + s.text.length + ' 字'}</Text></Pressable> : <Text key={i} style={{ lineHeight: 30, fontSize: 18, maxWidth: '100%' }}>{s.text}</Text>)}</View></> : <Text>遮字内容待加载。</Text>}</CalmPanel>
        <Button onPress={() => setReference(v => !v)}>{reference ? '收起原文' : '查看原文'}</Button>{reference && <CalmPanel><Text selectable style={{ lineHeight: 30 }}>{active.content}</Text></CalmPanel>}
        {!!recordId && <CalmPanel><Text variant="titleMedium" style={calm.section}>{rated ? '已安排下次复习' : '回顾后，给记忆评分'}</Text>{!rated && ratingButtons}</CalmPanel>}
      </> : result ? <>
        <CalmPanel><Text variant="headlineMedium">{Math.round(result.accuracy * 100)}%</Text><Text style={calm.body}>正确 {result.correct_chars} 字 · 漏写 {result.missing_chars} 字 · 多写 {result.extra_chars} 字</Text><Divider /><Text style={calm.body}>对照原文</Text>{result.diffs.map((d, i) => <Text key={i} selectable style={{ lineHeight: 28, color: d.type === 'equal' ? theme.colors.onSurface : d.type === 'delete' ? theme.colors.error : theme.colors.primary }}>{d.type === 'equal' ? '' : d.type === 'delete' ? '漏写：' : '多写：'}{d.text}</Text>)}</CalmPanel>
        <CalmPanel><Text variant="titleMedium" style={calm.section}>{rated ? '已安排下次复习' : '给这次记忆评分'}</Text>{!rated && ratingButtons}</CalmPanel>
        <Button mode="outlined" onPress={() => { setResult(null); setRecordId(''); setRated(false); started.current = Date.now() }}>修改后再检查</Button>
      </> : <CalmPanel><TextInput label="默写内容" accessibilityLabel="默写内容" mode="outlined" multiline value={drafts[active.id] || ''} maxLength={100000} onChangeText={value => setDrafts(prev => ({ ...prev, [active.id]: value }))} disabled={busy} style={{ minHeight: 180 }} /><Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>已输入 {(drafts[active.id] || '').length} 字。返回列表后，当前页面会保留这段输入。</Text><Button mode="contained" disabled={busy} loading={busy} onPress={check}>检查默写</Button></CalmPanel>}
      {!!error && <ScreenNotice text={error} onRetry={mode === 'hide' && !plan && !busy ? () => hide(active, level) : undefined} />}
    </> : <>
      {stats && <Text style={{ color: theme.colors.onSurfaceVariant, marginBottom: 16 }}>共 {stats.total_passages} 段 · 已练习 {stats.total_records} 次{stats.total_records ? ' · 平均准确率 ' + Math.round(stats.avg_accuracy * 100) + '%' : ''}</Text>}
      <View style={[calm.wrap, { marginBottom: 18 }]}><Chip selected={!dueOnly} onPress={() => setDueOnly(false)}>全部内容</Chip><Chip selected={dueOnly} onPress={() => setDueOnly(true)}>到期复习 ({due.length})</Chip></View>
      {loading && !passages.length && <ActivityIndicator />}
      {!!error && <ScreenNotice text={error} onRetry={load} />}{!!dueError && <ScreenNotice text={dueError} onRetry={load} />}
      {dueOnly ? due.map(d => <CalmPanel key={d.record_id}><Text variant="titleMedium">{d.passage_title}</Text><Text style={calm.body}>上次准确率 {Math.round(d.accuracy * 100)}%</Text><Button mode="outlined" onPress={() => start(passages.find(p => p.id === d.passage_id) || { id: d.passage_id, title: d.passage_title || '背诵内容', content: d.passage_content || '', category: 'custom', difficulty: 1, tags: [], source: null, created_at: '' }, 'hide', d.record_id)}>开始回顾</Button></CalmPanel>) : passages.map(p => <CalmPanel key={p.id}><Text variant="titleMedium">{p.title}</Text><Text numberOfLines={3} style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>{p.content}</Text><View style={calm.wrap}><Button mode="outlined" onPress={() => start(p, 'hide')}>遮字背诵</Button><Button mode="contained-tonal" onPress={() => start(p, 'dictation')}>开始默写</Button><Button textColor={theme.colors.error} onPress={() => { setDeleteId(p.id); setFormError(''); setDialog('delete') }}>删除</Button></View></CalmPanel>)}
      {!loading && !error && !dueOnly && !passages.length && <ScreenNotice text="还没有背诵内容，先添加一段想记住的文字。" />}
      {!loading && dueOnly && !due.length && !dueError && <ScreenNotice text="当前没有到期的背诵记录。" />}
    </>}
    <Portal><Dialog visible={!!dialog} onDismiss={() => { if (!busy) setDialog(null) }} style={{ width: '90%', maxWidth: 460, alignSelf: 'center' }}><Dialog.Title>{dialog === 'create' ? '添加背诵内容' : '删除这段内容？'}</Dialog.Title><Dialog.ScrollArea><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingVertical: 12 }}>
      {dialog === 'create' ? <><TextInput label="标题" accessibilityLabel="背诵标题" value={title} onChangeText={setTitle} maxLength={200} mode="outlined" disabled={busy} /><TextInput label="背诵原文" accessibilityLabel="背诵原文" value={content} onChangeText={setContent} maxLength={100000} multiline mode="outlined" disabled={busy} style={{ minHeight: 140, marginTop: 12 }} /></> : <Text style={calm.body}>这段内容和它的背诵记录会一起删除。</Text>}
      {!!formError && <ScreenNotice text={formError} />}
    </ScrollView></Dialog.ScrollArea><Dialog.Actions><Button disabled={busy} onPress={() => setDialog(null)}>取消</Button><Button disabled={busy} loading={busy} onPress={mutate}>{dialog === 'create' ? '保存内容' : '确认删除'}</Button></Dialog.Actions></Dialog></Portal>
  </CalmScreen>
}
